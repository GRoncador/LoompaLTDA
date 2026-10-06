"""A decision model for narrow, typed questions (plan 13.6, ADR-0032).

Some of the factory's calls are not writing: "is this passage useful for that question?", "does
this source support that sentence?". A decision model (TypeSafe's Jev 1.13, served by OpenRouter
at `POST /api/alpha/decisions` with the same key and credits) answers them with a calibrated
confidence, for about US$ 0.04 per million input tokens and nothing for the output, in ~0.1 s. It
reads literally, does no arithmetic, counts or dates, and gets worse with a large noisy state:
it only gets short states and plain questions, one pair at a time (see the questions below).

Three kinds of question, as the endpoint takes them:
- `noul`: a proposition, true or false (criteria for each); the answer is P(true);
- `choice`: one of named options (criteria per option);
- `score`: one of ordered levels (criteria, lowest first).

`Decider.decide` returns None when it cannot answer (no key, the alpha endpoint changed its
shape, a timeout): the caller keeps its current path, never a guess. The dry-run gets a fixed
answer with no confidence, so `decide` mode never acts on it; the tests script their own.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

import httpx

from loompa.llm.providers import resolve_key

Kind = Literal["noul", "choice", "score"]
State = str | dict[str, Any] | list[Any]  # a JSON object with named fields, preferably
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
    # 0..1. A noul carries none of its own: |2p - 1|, TypeSafe's formula, on the same scale as a
    # choice's (0 at a coin toss, 1 at certainty). A ranking reads probabilities["true"] instead.
    confidence: float
    probabilities: dict[str, float] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Answer | None:
        kind = raw.get("type")
        try:
            if kind == "noul":
                p = float(raw["noul"])
                return cls("noul", p >= 0.5, abs(2 * p - 1), {"true": p})
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
        state: State,
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


# --------------------------------------------------------------------------- the questions (13.6)
# Every question and threshold the factory asks of the decision model lives here, so a review
# reads them in one place. The shapes follow TypeSafe's own cookbooks (re-ranking, classifying
# RAG passages, double-checking citations) and its list of Jev 1.13's weak points:
# - one request per pair (task and passage, claim and source, finding and card): no question
#   sees the other candidates, which are only distractors, and none points at "passage [3]";
# - the state is a JSON object with named fields, and the questions name them in backticks;
# - atomic yes/no questions, combined in code, where one question would hide two judgments;
# - a ranking reads the probability (a noul), never a threshold on confidence.

WORD = re.compile(r"\w{3,}", re.UNICODE)


def _yes_no(instructions: str, yes: str, no: str) -> Question:
    return Question("noul", instructions, {"true": yes, "false": no})


# Memory: is a pushed passage worth its place in the prompt? Measured offline (ADR-0032, 20 pt-BR
# questions, 120 passages): P(relevant) puts a right passage above a wrong one in 95% of pairs
# (the search's own order: 60%; P(applies): 73%), and none of the right passages fell under 0.3
# while a third of the wrong ones did. So the ranking reads `relevant`, and RELEVANT_MIN is
# the floor under which a passage is off the task.
PASSAGE_QUESTIONS = {
    "relevant": _yes_no(
        "Does `passage` address the subject of `task`?",
        "The passage is about the same feature, file, rule or problem as the task.",
        "The passage is about something else; sharing a few words with the task is not enough.",
    ),
    "applies": _yes_no(
        "Does `passage` state a decision, rule, convention or past mistake that someone doing "
        "`task` should follow or avoid?",
        "It states something the person doing the task should reuse, follow or avoid repeating.",
        "It states nothing that would change how the task is done.",
    ),
}
RELEVANT_MIN = 0.3
PASSAGE_CHARS = 1200


def passage_state(task: str, kind: str, title: str, text: str) -> dict[str, Any]:
    return {
        "task": task[:800],
        "passage": {"kind": kind, "title": title, "text": text.strip()[:PASSAGE_CHARS]},
    }


def p_true(answer: Answer | None) -> float:
    """A noul's probability of yes (0.5 when there is no answer: no evidence either way)."""
    return answer.probabilities.get("true", 0.5) if answer else 0.5


# Research: how does the part of a source nearest the claim relate to it? `contradicts` is kept
# apart from `says_nothing`: a source saying the opposite is a finding of its own.
RELATION = Question(
    "choice",
    "How does `source` relate to `claim`?",
    {
        "supports": "The source states the claim or directly implies that it is true.",
        "contradicts": "The source states the opposite of the claim or implies that it is false.",
        "says_nothing": "The source does not settle the claim: it is only on the same topic, or "
        "says something else.",
    },
)
SECTION_CHARS = 1500


def claim_state(claim: str, section: str) -> dict[str, str]:
    return {"claim": claim[:600], "source": section}


def nearest_section(text: str, claim: str, size: int = SECTION_CHARS) -> str:
    """The part of a long source text that shares the most words with the claim: located in
    code, so the model reads one section instead of a whole page (a large state costs it
    accuracy). Paragraphs are packed into windows of about `size` characters."""
    if len(text) <= size:
        return text
    want = {w.lower() for w in WORD.findall(claim)}
    paras = [p.strip() for p in re.split(r"\n\s*\n|\n", text) if p.strip()]
    windows, cur = [], ""
    for p in paras:
        if cur and len(cur) + len(p) > size:
            windows.append(cur)
            cur = cur[-200:]  # a little overlap, so a sentence cut at the edge is still whole
        cur = f"{cur}\n{p}" if cur else p
    windows.append(cur)

    def overlap(w: str) -> int:
        return len(want & {x.lower() for x in WORD.findall(w)})

    return max(windows, key=overlap)[:size]


# Kaizen triage: two questions about the finding alone, combined in code into "process only",
# and one yes/no per nearby open card: "the same problem?" (the offline check's shape).
FINDING_QUESTIONS = {
    "asks_change": _yes_no(
        "Does `finding` ask for a change to the product's code or to its tests?",
        "It asks to change code, fix a defect, or add or change a test.",
        "It asks for no change to the product's code or tests.",
    ),
    "asks_process": _yes_no(
        "Does `finding` ask for process work: running the test suite, attaching output or "
        "evidence, recording a closing, or writing a spec, plan or tasks for work already done?",
        "It asks for at least one of those process steps.",
        "It asks for none of those process steps.",
    ),
}
SAME_PROBLEM = _yes_no(
    "Do `finding` and `card` describe the same problem, so that doing `card` would also settle "
    "`finding`? Judge by meaning, not by wording.",
    "Same defect or task: one card is enough for both.",
    "Different problems, or the finding needs work the card does not cover.",
)
YES = 0.5  # a noul above this reads as yes
NO_CARD = "none"


def finding_state(text: str) -> dict[str, str]:
    return {"finding": text}


def pair_state(finding: str, card_id: str, card_title: str) -> dict[str, Any]:
    return {"finding": finding, "card": {"id": card_id, "title": card_title}}


def process_only(answers: dict[str, Answer]) -> bool:
    return p_true(answers.get("asks_process")) > YES and p_true(answers.get("asks_change")) < YES


def keeps(answer: Answer | None, threshold: float) -> bool:
    """Whether a `decide`-mode answer is trusted: confident enough, never the dry-run's."""
    return answer is not None and answer.confidence >= threshold
