"""Model matrix router: role -> tier -> ordered candidates, fall-through on quota/5xx.

Also the single place where every LLM call is metered (Finance Loompa) and where the
escalation ladder (Tier 2 -> Tier 1) is expressed via `tier_override`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from loompa.config.schema import LoompaConfig, ModelCandidate
from loompa.finance import CostTracker, UsageRecord
from loompa.llm.providers import (
    LLMError,
    LLMProvider,
    LLMResponse,
    Message,
    QuotaExhausted,
    build_provider,
)

log = logging.getLogger(__name__)


@dataclass
class RoutedCall:
    response: LLMResponse
    tier: str
    candidate: ModelCandidate
    cost_usd: float
    attempts: int


class ModelRouter:
    def __init__(
        self,
        config: LoompaConfig,
        *,
        tracker: CostTracker | None = None,
        providers: dict[str, LLMProvider] | None = None,
        client: httpx.AsyncClient | None = None,
        on_call: Callable[[str, str, RoutedCall], None] | None = None,
        max_retries: int = 2,
        max_cooldown_wait: float = 90.0,
        secrets: Mapping[str, str] | None = None,
    ):
        self.config = config
        self.tracker = tracker
        self.secrets = secrets
        self.on_call = on_call
        self.max_retries = max_retries
        # When every candidate of a tier is merely cooling down (typical with a single-model
        # tier on a free-tier rate limit), wait up to this long for the earliest one instead
        # of failing the story outright.
        self.max_cooldown_wait = max_cooldown_wait
        self._client = client
        self._providers: dict[str, LLMProvider] = dict(providers or {})
        self._cooldown: dict[
            str, float
        ] = {}  # "provider/model" -> loop time until which it's skipped

    def provider(self, name: str) -> LLMProvider:
        if name not in self._providers:
            cfg = self.config.providers.get(name)
            if cfg is None:
                raise LLMError(f"provedor não configurado: {name}")
            self._providers[name] = build_provider(
                name, cfg, client=self._client, secrets=self.secrets
            )
        return self._providers[name]

    def reset_providers(self, secrets: Mapping[str, str] | None = None) -> None:
        """Forget built adapters so new keys/base URLs apply on the next call (no restart).
        Injected providers (tests, dry-run) are kept."""
        if secrets is not None:
            self.secrets = secrets
        self._providers = {
            n: p for n, p in self._providers.items() if getattr(p, "cfg", None) is None
        }
        self._cooldown.clear()

    # Roles whose judgement matters more on a hard story: lifted to tier1 when COMPLEX.
    LIFT_ON_COMPLEX = ("product", "product_owner", "inspector", "analyst")

    def candidates(
        self, role: str, tier_override: str | None = None, complexity: str | None = None
    ) -> tuple[str, list[ModelCandidate]]:
        """Tier for a call: explicit override > story complexity > role default.
        SIMPLE stories run every role on tier2; COMPLEX ones lift the review roles to tier1."""
        tier = tier_override or self.config.models.tier_for(role)
        if tier_override is None and complexity:
            c = str(complexity).upper()
            if c == "SIMPLE" and "tier2" in self.config.models.tiers:
                tier = "tier2"
            elif (
                c == "COMPLEX"
                and role in self.LIFT_ON_COMPLEX
                and "tier1" in self.config.models.tiers
            ):
                tier = "tier1"
        return tier, list(self.config.models.tiers.get(tier, []))

    async def complete(
        self,
        role: str,
        messages: list[Message],
        *,
        agent: str | None = None,
        story_id: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        tier_override: str | None = None,
        json_mode: bool = False,
        max_tokens: int | None = None,
        temperature: float | None = None,
        complexity: str | None = None,
    ) -> RoutedCall:
        tier, cands = self.candidates(role, tier_override, complexity)
        if not cands:
            raise LLMError(f"nenhum modelo configurado para o tier {tier}")
        loop = asyncio.get_running_loop()
        errors: list[str] = []
        attempts = 0
        waited = 0.0
        while True:
            routed = await self._one_pass(
                tier,
                cands,
                role,
                messages,
                agent,
                story_id,
                tools,
                json_mode,
                max_tokens,
                temperature,
                errors,
                attempts,
            )
            if isinstance(routed, RoutedCall):
                return routed
            attempts = routed
            # Nothing answered. If every candidate is only cooling down, wait for the first
            # one to come back rather than failing the story on the spot.
            cooling = [
                self._cooldown[f"{c.provider}/{c.model}"]
                for c in cands
                if self._cooldown.get(f"{c.provider}/{c.model}", 0) > loop.time()
            ]
            if len(cooling) != len(cands):
                break
            wait = min(cooling) - loop.time() + 0.05
            if waited + wait > self.max_cooldown_wait:
                errors.append(f"cooldown de {wait:.0f}s excede o limite de espera")
                break
            log.warning("tier %s: todos os modelos em cooldown, aguardando %.0fs", tier, wait)
            await asyncio.sleep(wait)
            waited += wait
        raise LLMError(
            "todos os modelos do tier falharam: " + "; ".join(errors[-4:]), retryable=True
        )

    async def _one_pass(
        self,
        tier: str,
        cands: list[ModelCandidate],
        role: str,
        messages: list[Message],
        agent: str | None,
        story_id: str | None,
        tools: list[dict[str, Any]] | None,
        json_mode: bool,
        max_tokens: int | None,
        temperature: float | None,
        errors: list[str],
        attempts: int,
    ) -> RoutedCall | int:
        """Try each candidate once (with per-candidate retries). Returns the RoutedCall, or
        the updated attempt count when none answered."""
        loop = asyncio.get_running_loop()
        for cand in cands:
            key = f"{cand.provider}/{cand.model}"
            if self._cooldown.get(key, 0) > loop.time():
                errors.append(f"{key}: em cooldown")
                continue
            try:
                prov = self.provider(cand.provider)
            except LLMError as exc:
                errors.append(str(exc))
                continue
            if hasattr(prov, "available") and not prov.available():  # type: ignore[attr-defined]
                errors.append(f"{key}: sem chave de API")
                continue
            for retry in range(self.max_retries + 1):
                attempts += 1
                try:
                    resp = await prov.complete(
                        cand.model,
                        messages,
                        tools=tools,
                        temperature=temperature
                        if temperature is not None
                        else (cand.temperature or self.config.models.temperature),
                        max_tokens=max_tokens
                        or cand.max_output_tokens
                        or self.config.models.max_output_tokens,
                        json_mode=json_mode,
                    )
                except QuotaExhausted as exc:
                    errors.append(str(exc))
                    # Honour the provider's hint; otherwise a 429 is a per-minute rate limit,
                    # anything else (402 billing, 503 overloaded) deserves a longer pause.
                    pause = exc.retry_after or (60.0 if exc.status == 429 else 300.0)
                    self._cooldown[key] = loop.time() + pause
                    break  # next candidate
                except LLMError as exc:
                    errors.append(str(exc))
                    if exc.retryable and retry < self.max_retries:
                        await asyncio.sleep(0.5 * (2**retry))
                        continue
                    break
                cost = 0.0
                if self.tracker:
                    cost = self.tracker.record(
                        UsageRecord(
                            agent=agent or role,
                            role=role,
                            provider=cand.provider,
                            model=cand.model,
                            tier=tier,
                            input_tokens=resp.input_tokens,
                            output_tokens=resp.output_tokens,
                            cached_tokens=resp.cached_tokens,
                            duration_ms=resp.duration_ms,
                            story_id=story_id,
                        )
                    )
                routed = RoutedCall(
                    response=resp, tier=tier, candidate=cand, cost_usd=cost, attempts=attempts
                )
                if self.on_call:
                    self.on_call(role, agent or role, routed)
                return routed
        return attempts

    async def aclose(self) -> None:
        for p in self._providers.values():
            await p.aclose()
