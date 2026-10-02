"""Model matrix router: role -> tier -> ordered candidates, fall-through on quota/5xx.

Also the single place where every LLM call is metered (Finance Loompa) and where the
escalation ladder (Tier 2 -> Tier 1) is expressed via `tier_override`.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from loompa.config.schema import LoompaConfig, ModelCandidate
from loompa.finance import CostTracker, UsageRecord
from loompa.llm.providers import (
    LLMError,
    LLMProvider,
    LLMResponse,
    LoopDetected,
    Message,
    QuotaExhausted,
    build_provider,
    model_not_found,
)
from loompa.trace import Tracer

log = logging.getLogger(__name__)

TRUNCATED_SUFFIX = " (resposta cortada no limite)"


CUT_TAIL_CHARS = 600
# An answer that thought this long keeps its tail too, cut or not: whether the factory needs a loop
# detector (ADR-0016 §3) is decided on what long reasoning looked like (a judge thought 50k+ tokens
# about a one-line command in the second smoke run, and nothing of it was kept).
LONG_REASONING_TOKENS = 16384


RUNAWAY_REST_S = 3600.0  # a candidate that ran away for a role goes last for that role this long
REASONING_KEEP_CHARS = 12_000  # the loop detector's window: enough to tell a loop from thinking


def _cut_tail(resp: LLMResponse, keep: Any = None) -> dict[str, str]:
    """The end of what a cut answer was writing, for the trace: a plan that runs past 16k
    tokens is either thinking in circles or repeating itself in the output, and only the tail
    tells which. The half-written tool call is read from the raw message: it never parses."""
    msg = ((resp.raw or {}).get("choices") or [{}])[0].get("message") or {}
    calls = msg.get("tool_calls") or []
    args = (calls[-1].get("function") or {}).get("arguments") if calls else None
    text = resp.content or (args if isinstance(args, str) else "")
    reasoning = msg.get("reasoning")
    tail = {"tail": text[-CUT_TAIL_CHARS:]} if text else {}
    if isinstance(reasoning, str) and reasoning:
        tail["reasoning_tail"] = reasoning[-CUT_TAIL_CHARS:]
        if keep is not None:  # the longer end goes to the trace as a message, by reference: a
            # span attribute is capped at 2k, and 600 chars could not say whether the Product
            # Owner's 96k-token rerank was a loop (contas Sprint 2)
            ref = keep(reasoning[-REASONING_KEEP_CHARS:])
            if ref:
                tail["reasoning"] = ref
    return tail


# The only effort label the factory sends (ADR-0016): `low` to think less. Everything else is the
# provider's default (or the founder's per-model setting). On glm-5.3-flash `medium` and `high`
# thought as little as `low`, and `max` as much as the default: the labels are not a scale.
LIGHT_EFFORTS = ("low", "minimal")

# One tier up, for the retries of a failed step (ADR-0016 §5: one try on the tier it used, then two
# on the tier above; tier 1 has nothing above it and stays).
TIER_ABOVE = {"tier3": "tier2", "tier2": "tier1", "tier1": "tier1"}

PROGRESS_EVERY_S = 30.0  # how often a streaming call says it is still writing


@dataclass
class RoutedCall:
    response: LLMResponse
    tier: str
    candidate: ModelCandidate
    cost_usd: float  # every attempt of the call, cut ones included: each was paid for
    attempts: int
    cuts: int = 0  # answers cut at the output limit and asked again with more room
    span_id: str = ""  # the call's span in the story's trace
    story_id: str | None = None


@dataclass
class _CallLog:
    """What one logical call went through before it answered (or did not)."""

    attempts: list[dict[str, Any]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    spent: float = 0.0
    cuts: int = 0
    count: int = 0


class ModelRouter:
    def __init__(
        self,
        config: LoompaConfig,
        *,
        tracker: CostTracker | None = None,
        providers: dict[str, LLMProvider] | None = None,
        client: httpx.AsyncClient | None = None,
        on_call: Callable[[str, str, RoutedCall], None] | None = None,
        on_model_gone: Callable[[ModelCandidate, str], None] | None = None,
        on_event: Callable[..., None] | None = None,
        tracer: Tracer | None = None,
        max_retries: int = 2,
        max_cooldown_wait: float = 90.0,
        secrets: Mapping[str, str] | None = None,
    ):
        self.config = config
        self.tracker = tracker
        self.secrets = secrets
        self.on_call = on_call
        # A candidate the provider says it does not have: a typo or a model that left the air.
        # The Ops Loompa turns it into one inbox note (loompa.models_sync.ModelWatch).
        self.on_model_gone = on_model_gone
        # `(type, *, story_id, agent, **payload)`: cuts and fall-through become events, so a call
        # that spends minutes retrying is not silence to the stall watchdog or to the dashboard.
        self.on_event = on_event
        self.tracer = tracer or Tracer()
        self.max_retries = max_retries
        self._budget_checked_at = 0.0
        # (model key, role) -> output budget that last fitted after a cut. A reasoning model
        # that needed 3600 tokens for a self-check will need them next time too: starting
        # there saves a paid, discarded call (52 cuts in one hour of `contas`, 2026-09-30).
        self._fitted: dict[tuple[str, str], int] = {}
        self._budget_downgrade = False
        # When every candidate of a tier is merely cooling down (typical with a single-model
        # tier on a free-tier rate limit), wait up to this long for the earliest one instead
        # of failing the story outright.
        self.max_cooldown_wait = max_cooldown_wait
        self._client = client
        self._providers: dict[str, LLMProvider] = dict(providers or {})
        self._cooldown: dict[
            str, float
        ] = {}  # "provider/model" -> loop time until which it's skipped
        # ("provider/model", role) -> loop time until which that role tries it last: it ran away
        # there (cut at the ceiling or looped). contas Sprint 2: the Product Owner's first
        # candidate thought to the 96k ceiling or looped on 6 of 9 calls (mean 8 min, one review
        # 44 min) while the next one in the same tier answered in about a minute.
        self._rested: dict[tuple[str, str], float] = {}

    def remember_rested(self, events: list[dict[str, Any]], now_iso_: str) -> None:
        """Rests still running from `llm.rested` events (a restarted engine keeps them): each
        lasts RUNAWAY_REST_S from when it was recorded."""
        from datetime import datetime

        now = datetime.fromisoformat(now_iso_)
        mono = time.monotonic()
        for e in events:
            p = e.get("payload") or {}
            age = (now - datetime.fromisoformat(e["created_at"])).total_seconds()
            if p.get("model") and p.get("role") and age < RUNAWAY_REST_S:
                self._rested[(p["model"], p["role"])] = mono + RUNAWAY_REST_S - age

    def _rest(self, key: str, role: str, story_id: str | None, who: str, why: str) -> None:
        """`key` ran away for `role`: that role tries it last for RUNAWAY_REST_S. The model list
        is untouched (ADR-0011); only the order within the tier changes, as cooldown does."""
        loop = asyncio.get_running_loop()
        if self._rested.get((key, role), 0) <= loop.time():
            self._event("llm.rested", story_id, who, model=key, role=role, reason=why)
        self._rested[(key, role)] = loop.time() + RUNAWAY_REST_S

    def provider(self, name: str) -> LLMProvider:
        if name not in self._providers:
            cfg = self.config.providers.get(name)
            if cfg is None:
                raise LLMError(f"provedor não configurado: {name}")
            self._providers[name] = build_provider(
                name,
                cfg,
                client=self._client,
                secrets=self.secrets,
                idle_s=self.config.models.stream_idle_s,
                token_idle_s=self.config.models.stream_token_idle_s,
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

    BUDGET_RECHECK_S = 60.0

    def budget_downgrade(self) -> bool:
        """True while the period's cap is spent and the founder chose free models over a pause.
        Re-read at most once a minute: it gates every call and costs two aggregates."""
        if self.tracker is None:
            return False
        now = time.monotonic()
        if now - self._budget_checked_at > self.BUDGET_RECHECK_S:
            self._budget_checked_at = now
            try:
                self._budget_downgrade = self.tracker.status().downgrade
            except Exception:  # noqa: BLE001 - accounting must never break a call
                self._budget_downgrade = False
        return self._budget_downgrade

    def candidates(
        self,
        role: str,
        tier_override: str | None = None,
        complexity: str | None = None,
        task: str | None = None,
        lift: bool = False,
    ) -> tuple[str, list[ModelCandidate]]:
        """Tier for a call: explicit override > story complexity > task > role/cluster default.
        Candidates are resolved from the matrix: cluster(role) x tier. With `lift` (a retry of a
        step that failed) the call goes one tier above what it would have used."""
        cluster = self.config.models.cluster_for_role(role)
        tier = tier_override or self.config.models.tier_for_task(role, task)
        if tier_override is None and complexity:
            c = str(complexity).upper()
            if c == "SIMPLE":
                tier = "tier2"
            elif c == "COMPLEX" and role in self.LIFT_ON_COMPLEX:
                tier = "tier1"

        tier = tier or ("tier3" if cluster == "routine" else "tier2")
        if lift:
            tier = TIER_ABOVE.get(tier, tier)
        # The cap is spent and the founder asked for free models instead of a pause: every call
        # drops to tier 3, whatever the role, the task or the story asked for.
        if self.budget_downgrade():
            tier = "tier3"
        cands = self.config.models.candidates_for_cluster_tier(cluster, tier)

        # In-cluster fallback if chosen tier has no candidates
        if not cands:
            for fallback_tier in ("tier2", "tier1", "tier3"):
                if fallback_tier != tier:
                    cands = self.config.models.candidates_for_cluster_tier(cluster, fallback_tier)
                    if cands:
                        tier = fallback_tier
                        break

        # Fallback to legacy tiers if cluster matrix has no models
        if not cands:
            cands = self.config.models.tiers.get(tier, [])

        return tier, list(cands)

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
        reasoning_effort: str | None = None,
        task: str | None = None,
        lift: bool = False,
    ) -> RoutedCall:
        tier, cands = self.candidates(role, tier_override, complexity, task, lift)
        with self.tracer.span(
            "llm",
            agent or role,
            story_id=story_id,
            role=role,
            tier=tier,
            task=task,
            complexity=complexity,
            json_mode=json_mode or None,
            tools=[str(t.get("name")) for t in tools] if tools else None,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
            temperature=temperature,
        ) as span:
            span.set(messages=self.tracer.messages(span.story_id, messages))
            calls = _CallLog()
            span.set(attempts=calls.attempts)
            if not cands:
                raise LLMError(f"nenhum modelo configurado para o tier {tier}")
            try:
                routed = await self._route(
                    tier,
                    cands,
                    role,
                    messages,
                    agent,
                    story_id,
                    tools,
                    json_mode,
                    max_tokens,
                    complexity,
                    temperature,
                    reasoning_effort,
                    calls,
                )
            except LLMError:
                # a call that never answered was still paid for, attempt by attempt (smoke run:
                # six cut attempts, US$0.05, shown as US$0 on the failed span)
                span.set(cost_usd=round(calls.spent, 6) or None)
                raise
            resp = routed.response
            answer = self.tracer.messages(
                span.story_id, [Message("assistant", resp.text, tool_calls=resp.tool_calls)]
            )
            span.set(
                provider=routed.candidate.provider,
                model=routed.candidate.model,
                responded=resp.model if resp.model != routed.candidate.model else None,
                served_by=resp.served_by or None,
                finish_reason=resp.finish_reason,
                input_tokens=resp.input_tokens,
                output_tokens=resp.output_tokens,
                cached_tokens=resp.cached_tokens,
                reasoning_tokens=resp.reasoning_tokens or None,
                cost_usd=round(routed.cost_usd, 6),
                cuts=routed.cuts or None,
                response=answer[0] if answer else None,
            )
            routed.span_id = span.id
            routed.story_id = story_id
            if self.on_call:
                self.on_call(role, agent or role, routed)
            return routed

    async def _route(
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
        complexity: str | None,
        temperature: float | None,
        reasoning_effort: str | None,
        calls: _CallLog,
    ) -> RoutedCall:
        loop = asyncio.get_running_loop()
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
                complexity,
                temperature,
                reasoning_effort,
                calls,
            )
            if routed is not None:
                return routed
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
                calls.errors.append(f"cooldown de {wait:.0f}s excede o limite de espera")
                break
            log.warning("tier %s: todos os modelos em cooldown, aguardando %.0fs", tier, wait)
            await asyncio.sleep(wait)
            waited += wait
        errors = calls.errors
        if errors and all(e.endswith(TRUNCATED_SUFFIX) for e in errors):
            # Every model ran out of room even at the ceiling: waiting will not change that.
            raise LLMError("resposta cortada: " + "; ".join(errors[-4:]), retryable=False)
        raise LLMError(
            "todos os modelos do tier falharam: " + "; ".join(errors[-4:]), retryable=True
        )

    def _room(self, effort: str, asked: int | None, cand: ModelCandidate) -> int:
        """Output room for a call (ADR-0016): a backstop, not an estimate. A call pays only for
        what it writes; room that is too small cuts it and pays for the discarded attempt (the
        smoke run's plan needed ~20k and was cut at 4k, 8k and 16k)."""
        m = self.config.models
        room = m.light_output_tokens if effort in LIGHT_EFFORTS else m.full_output_tokens
        room = max(room, asked or 0)  # a call that knows it writes more (whole files) says so
        if cand.max_output_tokens:  # the founder's cap for a model that cannot take more
            room = min(room, cand.max_output_tokens)
        return max(1, min(room, m.max_output_ceiling))

    def _progress(
        self, story_id: str | None, agent: str, key: str, role: str, started: float
    ) -> Callable[[int], None]:
        """While a call streams, one `llm.progress` every PROGRESS_EVERY_S with the tokens so
        far: the stall watchdog hears the story is alive, and the card shows it thinking."""
        loop = asyncio.get_running_loop()
        last = [started]

        def report(tokens: int) -> None:
            now = loop.time()
            if now - last[0] >= PROGRESS_EVERY_S:
                last[0] = now
                self._event(
                    "llm.progress",
                    story_id,
                    agent,
                    model=key,
                    role=role,
                    tokens=tokens,
                    seconds=int(now - started),
                )

        return report

    def _event(self, type_: str, story_id: str | None, agent: str, **payload: Any) -> None:
        if self.on_event is None:
            return
        try:
            self.on_event(type_, story_id=story_id, agent=agent, **payload)
        except Exception:  # noqa: BLE001 - telling about a retry must never stop the retry
            log.exception("could not emit %s", type_)

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
        complexity: str | None,
        temperature: float | None,
        reasoning_effort: str | None,
        calls: _CallLog,
    ) -> RoutedCall | None:
        """Try each candidate once (with per-candidate retries). Returns the RoutedCall, or None
        when none answered; `calls` keeps every attempt for the trace."""
        loop = asyncio.get_running_loop()
        who = agent or role
        current = self.tracer.current()
        trace_story = current.story_id if current is not None else story_id

        def keep(text: str) -> str | None:
            refs = self.tracer.messages(trace_story, [Message("assistant", text)])
            return refs[0] if refs else None

        # same candidates, same tier: one that ran away for this role is tried last for a while
        cands = sorted(
            cands,
            key=lambda c: self._rested.get((f"{c.provider}/{c.model}", role), 0) > loop.time(),
        )
        for cand in cands:
            key = f"{cand.provider}/{cand.model}"
            if self._cooldown.get(key, 0) > loop.time():
                calls.errors.append(f"{key}: em cooldown")
                continue
            try:
                prov = self.provider(cand.provider)
            except LLMError as exc:
                calls.errors.append(str(exc))
                continue
            if hasattr(prov, "available") and not prov.available():  # type: ignore[attr-defined]
                calls.errors.append(f"{key}: sem chave de API")
                continue
            effort = reasoning_effort if reasoning_effort is not None else cand.reasoning_effort
            budget = min(
                max(self._room(effort, max_tokens, cand), self._fitted.get((key, role), 0)),
                self.config.models.max_output_ceiling,
            )
            cuts = 0
            retry = 0
            while True:
                calls.count += 1
                attempt: dict[str, Any] = {"model": key, "max_tokens": budget}
                if effort:
                    attempt["effort"] = effort
                calls.attempts.append(attempt)
                started = loop.time()
                streamed = bool(getattr(prov, "streams", False))
                extra = (
                    {"on_progress": self._progress(story_id, who, key, role, started)}
                    if streamed
                    else {}
                )
                try:
                    # A streamed call has no wall-clock limit: it fails on silence and its output
                    # room ends a runaway (ADR-0016 §4); the others keep `call_timeout_s`.
                    resp = await asyncio.wait_for(
                        prov.complete(
                            cand.model,
                            messages,
                            tools=tools,
                            temperature=temperature
                            if temperature is not None
                            else (cand.temperature or self.config.models.temperature),
                            max_tokens=budget,
                            json_mode=json_mode,
                            reasoning_effort=effort,
                            **extra,
                        ),
                        timeout=None if streamed else self.config.models.call_timeout_s,
                    )
                except TimeoutError:
                    limit = self.config.models.call_timeout_s
                    calls.errors.append(f"{key}: sem resposta em {limit:.0f}s")
                    attempt.update(outcome="timeout", ms=int((loop.time() - started) * 1000))
                    log.warning("%s não respondeu em %.0fs; próximo modelo", key, limit)
                    self._event(
                        "llm.fallthrough", story_id, who, model=key, role=role, reason="timeout"
                    )
                    # a hung upstream tends to stay hung for a while: rest it like a 5xx
                    self._cooldown[key] = loop.time() + limit
                    resp = None
                    break  # next candidate
                except QuotaExhausted as exc:
                    calls.errors.append(str(exc))
                    attempt.update(
                        outcome="quota",
                        status=exc.status,
                        retry_after=exc.retry_after,
                        ms=int((loop.time() - started) * 1000),
                    )
                    self._event(
                        "llm.fallthrough",
                        story_id,
                        who,
                        model=key,
                        role=role,
                        reason="quota",
                        status=exc.status,
                    )
                    # Honour the provider's hint; otherwise a 429 is a per-minute rate limit,
                    # anything else (402 billing, 503 overloaded) deserves a longer pause.
                    pause = exc.retry_after or (60.0 if exc.status == 429 else 300.0)
                    self._cooldown[key] = loop.time() + pause
                    resp = None
                    break  # next candidate
                except LoopDetected as exc:
                    # Cut on strong evidence of a loop (ADR-0016 §3): what it wrote was billed,
                    # and its tail is what tells the next reader what the loop looked like.
                    cost = self._record(exc.partial, cand, tier, role, agent, story_id)
                    calls.spent += cost
                    calls.errors.append(str(exc))
                    attempt.update(
                        outcome="loop",
                        output_tokens=exc.partial.output_tokens,
                        cost_usd=round(cost, 6),
                        ms=int((loop.time() - started) * 1000),
                        loop=exc.sample[:300],
                        **_cut_tail(exc.partial, keep),
                    )
                    log.warning("%s entrou em loop; cortado e próximo modelo", key)
                    self._event(
                        "llm.loop",
                        story_id,
                        who,
                        model=key,
                        role=role,
                        tokens=exc.partial.output_tokens,
                        sample=exc.sample[:200],
                    )
                    self._event(
                        "llm.fallthrough", story_id, who, model=key, role=role, reason="loop"
                    )
                    self._rest(key, role, story_id, who, "loop")
                    resp = None
                    break  # next candidate, same effort
                except LLMError as exc:
                    calls.errors.append(str(exc))
                    attempt.update(
                        outcome="error",
                        status=exc.status,
                        error=str(exc)[:300],
                        ms=int((loop.time() - started) * 1000),
                    )
                    if model_not_found(exc) and self.on_model_gone:
                        # The provider does not have this id. Retrying cannot fix a name, so the
                        # founder hears about it once and the candidate is skipped meanwhile.
                        self.on_model_gone(cand, str(exc))
                    if exc.retryable and retry < self.max_retries:
                        # said, not silent: a stream the provider dropped after 15 min of
                        # reasoning restarted from zero with no trace of why (contas Sprint 2)
                        self._event(
                            "llm.retry",
                            story_id,
                            who,
                            model=key,
                            role=role,
                            error=str(exc)[:200],
                            lost_s=round(loop.time() - started, 1),
                        )
                        await asyncio.sleep(0.5 * (2**retry))
                        retry += 1
                        continue
                    self._event(
                        "llm.fallthrough",
                        story_id,
                        who,
                        model=key,
                        role=role,
                        reason="error",
                        status=exc.status,
                    )
                    resp = None
                    break
                cost = self._record(resp, cand, tier, role, agent, story_id)
                calls.spent += cost
                attempt.update(
                    outcome="cut" if resp.truncated else "ok",
                    finish_reason=resp.finish_reason,
                    input_tokens=resp.input_tokens,
                    output_tokens=resp.output_tokens,
                    cost_usd=round(cost, 6),
                    ms=resp.duration_ms or int((loop.time() - started) * 1000),
                    **({"served_by": resp.served_by} if resp.served_by else {}),
                    **(
                        {"reasoning_tokens": resp.reasoning_tokens} if resp.reasoning_tokens else {}
                    ),
                    **(
                        _cut_tail(resp, keep)
                        if resp.truncated or resp.reasoning_tokens > LONG_REASONING_TOKENS
                        else {}
                    ),
                )
                if not resp.truncated:
                    if cuts:
                        self._fitted[(key, role)] = budget
                    break
                # Cut mid-answer: the text or tool call is unusable. More room, never less
                # thinking (ADR-0016): the effort was chosen for what the call decides. At the
                # ceiling the next candidate gets the same call.
                bigger = min(budget * 2, self.config.models.max_output_ceiling)
                if cuts >= self.config.models.truncation_retries or bigger == budget:
                    calls.errors.append(f"{key}: {budget} tokens{TRUNCATED_SUFFIX}")
                    self._event(
                        "llm.fallthrough",
                        story_id,
                        who,
                        model=key,
                        role=role,
                        reason="cut",
                        max_tokens=budget,
                    )
                    self._rest(key, role, story_id, who, "cut")
                    resp = None
                    break
                log.warning(
                    "%s cortou a resposta em %d tokens; nova tentativa com %d", key, budget, bigger
                )
                self._event(
                    "llm.cut",
                    story_id,
                    who,
                    model=key,
                    role=role,
                    max_tokens=budget,
                    next_max_tokens=bigger,
                    effort=effort or None,
                )
                cuts += 1
                calls.cuts += 1
                budget = bigger
            if resp is not None:
                return RoutedCall(
                    response=resp,
                    tier=tier,
                    candidate=cand,
                    cost_usd=round(calls.spent, 6),
                    attempts=calls.count,
                    cuts=calls.cuts,
                )
        return None

    def _record(
        self,
        resp: LLMResponse,
        cand: ModelCandidate,
        tier: str,
        role: str,
        agent: str | None,
        story_id: str | None,
    ) -> float:
        """Meter one answered call, cut or not: a truncated answer was still paid for. What the
        provider says it charged wins over the price table (OpenRouter's `usage.cost`)."""
        if not self.tracker:
            return resp.cost_usd or 0.0
        span = self.tracer.current()
        return self.tracker.record(
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
                reported_cost=resp.cost_usd,
                served_by=resp.served_by,
                finish_reason=resp.finish_reason,
                span_id=span.id if span else "",
            )
        )

    async def aclose(self) -> None:
        for p in self._providers.values():
            await p.aclose()
