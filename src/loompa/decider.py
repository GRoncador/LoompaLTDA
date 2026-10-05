"""A decision model for narrow, typed questions (plan 13.6, ADR-0032).

Some of the factory's calls are not writing: "is this passage useful for that question?", "does
this source support that sentence?". A decision model (TypeSafe's Jev 1.13, served by OpenRouter
at `POST /api/alpha/decisions` with the same key and credits) answers them with a calibrated
confidence, for about US$ 0.04 per million input tokens and nothing for the output, in ~0.1 s. It
reads literally, does no arithmetic, counts or dates, and gets worse with a large noisy state:
it only gets short states and plain questions.

Three kinds of question, as the endpoint takes them:
- `noul`: a proposition, true or false (criteria for each); the answer is P(true);
- `choice`: one of named options (criteria per option);
- `score`: one of ordered levels (criteria, lowest first).

`Decider.decide` returns None when it cannot answer (no key, the alpha endpoint changed its
shape, a timeout): the caller keeps its current path, never a guess. The dry-run gets a fixed
answer with no confidence, so `decide` mode never acts on it; the tests script their own.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from loompa.llm.providers import resolve_key

Kind = Literal["noul", "choice", "score"]
MODEL_SEEN_KEY = "decider_model_seen:"  # kv: the dated id the alias last resolved to


@dataclass(frozen=True)
class Question:
    kind: Kind
    instructions: str
    # noul: {"true": ..., "false": ...}; choice: {option: meaning}; score: [lowest, ..., highest]
    criteria: dict[str, str] | list[str]

    def payload(self) -> dict[str, Any]:
        return {"type": self.kind, "instructions": self.instructions, "criteria": self.criteria}


@dataclass(frozen=True)
class Answer:
    kind: Kind
    value: Any  # bool (noul), the option (choice), the level index (score)
    confidence: float  # 0..1; for a noul, how far P(true) is from a coin toss, as max(p, 1 - p)
    probabilities: dict[str, float] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Answer | None:
        kind = raw.get("type")
        try:
            if kind == "noul":
                p = float(raw["noul"])
                return cls("noul", p >= 0.5, max(p, 1 - p), {"true": p})
            if kind == "choice":
                return cls(
                    "choice",
                    str(raw["choice"]),
                    float(raw.get("confidence") or 0.0),
                    {str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()},
                )
            if kind == "score":
                probs = {str(k): float(v) for k, v in (raw.get("probabilities") or {}).items()}
                level = max(probs, key=probs.get) if probs else str(round(float(raw["score"])))
                return cls("score", int(level), float(raw.get("confidence") or 0.0), probs)
        except (KeyError, TypeError, ValueError):
            return None
        return None


class Decider(Protocol):
    async def decide(
        self,
        state: str,
        questions: dict[str, Question],
        *,
        purpose: str,
        story_id: str | None = None,
    ) -> dict[str, Answer] | None: ...


class FixedDecider:
    """The dry-run's answer: the first option, the lowest level, false, all with no confidence.
    `decide` mode never acts on it (confidence 0 is under any threshold)."""

    async def decide(self, state, questions, *, purpose, story_id=None):  # noqa: ANN001
        out = {}
        for key, q in questions.items():
            if q.kind == "choice":
                out[key] = Answer("choice", next(iter(q.criteria)), 0.0)
            elif q.kind == "score":
                out[key] = Answer("score", 0, 0.0)
            else:
                out[key] = Answer("noul", False, 0.0)
        return out


class JevDecider:
    """Jev through OpenRouter's Decisions endpoint. Every call is metered like any model call
    (role `decider`, the cost OpenRouter reports) and emits `decider.call`; a failure emits
    `decider.unavailable` and answers None."""

    PATH = "/api/alpha/decisions"

    def __init__(self, ctx: Any, *, transport: httpx.AsyncBaseTransport | None = None):
        self.ctx = ctx
        self.cfg = ctx.config.decider
        provider = ctx.config.providers.get(self.cfg.provider)
        base = (provider.base_url if provider else "") or "https://openrouter.ai/api/v1"
        self.url = base.split("/api/")[0].rstrip("/") + self.PATH
        self.key_env = provider.api_key_env if provider else "OPENROUTER_API_KEY"
        self.transport = transport

    async def decide(self, state, questions, *, purpose, story_id=None):  # noqa: ANN001
        key = resolve_key(self.key_env, self.ctx.secrets)
        if not key or not questions:
            return None
        body = {
            "model": self.cfg.model,
            "state": state,
            "questions": {k: q.payload() for k, q in questions.items()},
        }
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=self.cfg.timeout_s, transport=self.transport
            ) as client:
                resp = await client.post(
                    self.url, json=body, headers={"Authorization": f"Bearer {key}"}
                )
            resp.raise_for_status()
            data = resp.json()
            raw = data["answers"]
            answers = {k: a for k, v in raw.items() if (a := Answer.parse(v)) is not None}
        except Exception as exc:  # noqa: BLE001 - the caller keeps its own path
            self.ctx.emit(
                "decider.unavailable",
                story_id=story_id,
                purpose=purpose,
                error=f"{type(exc).__name__}: {str(exc)[:200]}",
            )
            return None
        if set(answers) != set(questions):
            self.ctx.emit("decider.unavailable", story_id=story_id, purpose=purpose, error="shape")
            return None
        self._meter(data, story_id, int((time.monotonic() - started) * 1000))
        self._watch_model(str(data.get("model") or ""))
        self.ctx.emit(
            "decider.call",
            story_id=story_id,
            purpose=purpose,
            model=data.get("model"),
            answers={k: [a.value, round(a.confidence, 3)] for k, a in answers.items()},
        )
        return answers

    def _meter(self, data: dict[str, Any], story_id: str | None, ms: int) -> None:
        from loompa.finance.tracker import UsageRecord

        usage = data.get("usage") or {}
        cost = usage.get("cost")
        self.ctx.tracker.record(
            UsageRecord(
                agent="Decider",
                role="decider",
                provider=self.cfg.provider,
                model=self.cfg.model,
                tier="decider",
                input_tokens=int(usage.get("input_tokens") or 0),
                output_tokens=int(usage.get("output_tokens") or 0),
                duration_ms=ms,
                story_id=story_id,
                reported_cost=float(cost) if cost is not None else None,
                served_by=str(data.get("provider") or ""),
            )
        )

    def _watch_model(self, served: str) -> None:
        """The alias moved to another dated model: the founder hears it once (like AliasWatch).
        A supplier of 17 days whose limits change without notice is worth watching."""
        if not served:
            return
        key = MODEL_SEEN_KEY + self.cfg.model
        seen = self.ctx.store.get(key)
        self.ctx.store.set(key, served)
        if seen and seen != served:
            from loompa.comms import FounderMessage, MessageKind

            self.ctx.emit("decider.model_changed", model=self.cfg.model, was=seen, now=served)
            self.ctx.inbox(
                FounderMessage(
                    kind=MessageKind.INFO,
                    sender="Ops Loompa",
                    title="O modelo de decisões rápidas mudou de versão",
                    context=(
                        "O fornecedor trocou a versão do modelo que responde às perguntas "
                        "simples da fábrica. Ele continua só em observação ou acima do limite "
                        "de confiança que você aprovou; vale conferir a concordância no "
                        "próximo relatório."
                    ),
                    allow_free_text=False,
                )
            )


def build_decider(ctx: Any) -> Decider | None:
    """The factory's decider, or None when it is off."""
    if ctx.config.decider.mode == "off":
        return None
    return FixedDecider() if ctx.dry_run else JevDecider(ctx)


# --------------------------------------------------------------------------- the uses (13.6)

SUPPORTS = Question(
    "noul",
    "Does the source text state or directly support the sentence? Read both literally; a source "
    "that is only about the same topic does not support it.",
    {
        "true": "The source says what the sentence claims, or something that clearly implies it.",
        "false": "The source does not say it, says something else, or is only on the same topic.",
    },
)
USEFUL_LEVELS = [
    "Unrelated to the task",
    "Same area, but nothing to reuse or avoid",
    "A decision, rule or past mistake that applies to the task",
]


def useful_question(n: int) -> Question:
    return Question(
        "score",
        f"How useful is passage [{n}] to someone about to do the task?",
        USEFUL_LEVELS,
    )


def keeps(answer: Answer | None, threshold: float) -> bool:
    """Whether a `decide`-mode answer is trusted: confident enough, never the dry-run's."""
    return answer is not None and answer.confidence >= threshold
