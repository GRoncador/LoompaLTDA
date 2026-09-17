"""Model matrix router: role -> tier -> ordered candidates, fall-through on quota/5xx.

Also the single place where every LLM call is metered (Finance Loompa) and where the
escalation ladder (Tier 2 -> Tier 1) is expressed via `tier_override`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
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
    ):
        self.config = config
        self.tracker = tracker
        self.on_call = on_call
        self.max_retries = max_retries
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
            self._providers[name] = build_provider(name, cfg, client=self._client)
        return self._providers[name]

    def candidates(
        self, role: str, tier_override: str | None = None
    ) -> tuple[str, list[ModelCandidate]]:
        tier = tier_override or self.config.models.tier_for(role)
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
    ) -> RoutedCall:
        tier, cands = self.candidates(role, tier_override)
        if not cands:
            raise LLMError(f"nenhum modelo configurado para o tier {tier}")
        loop = asyncio.get_running_loop()
        errors: list[str] = []
        attempts = 0
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
                    self._cooldown[key] = loop.time() + 300
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
        raise LLMError(
            "todos os modelos do tier falharam: " + "; ".join(errors[-4:]), retryable=True
        )

    async def aclose(self) -> None:
        for p in self._providers.values():
            await p.aclose()
