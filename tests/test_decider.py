"""Plan 13.6: the decision model behind narrow typed questions, in shadow first. No network:
the Decisions endpoint is an httpx mock transport."""

from __future__ import annotations

import asyncio
import json

import httpx

from loompa.agents.analyst import AnalystAgent
from loompa.agents.base import _SHADOW, LoompaAgent
from loompa.decider import SUPPORTS, Answer, FixedDecider, JevDecider, Question, build_decider
from loompa.engine.state import StoryState
from loompa.factory import Factory
from test_engine import factory, make_ctx  # noqa: F401

SAME = Question("noul", "Same problem?", {"true": "yes", "false": "no"})


def events(ctx, type_: str) -> list[dict]:
    return [e for e in ctx.store.events_since(0, limit=5000) if e["type"] == type_]


def jev(answers: dict, model: str = "typesafe/jev-1.13-20260917", status: int = 200):
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        assert request.url.path == "/api/alpha/decisions"
        assert request.headers["authorization"] == "Bearer sk-or-test"
        body = {
            "model": model,
            "provider": "TypeSafe",
            "answers": answers,
            "usage": {"input_tokens": 455, "output_tokens": 70, "cost": 0.000019},
        }
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler), seen


def test_answers_of_each_kind_are_read():
    assert Answer.parse({"type": "noul", "noul": 0.2}) == Answer("noul", False, 0.8, {"true": 0.2})
    choice = Answer.parse({"type": "choice", "choice": "b", "confidence": 0.7, "probabilities": {}})
    assert choice.value == "b" and choice.confidence == 0.7
    score = Answer.parse(
        {"type": "score", "score": 1.9, "confidence": 0.9, "probabilities": {"0": 0.1, "2": 0.9}}
    )
    assert score.value == 2
    assert Answer.parse({"type": "noul"}) is None


async def test_a_call_is_metered_and_a_new_model_version_is_told_once(factory: Factory):  # noqa: F811
    ctx = make_ctx(factory)
    ctx.secrets = {"OPENROUTER_API_KEY": "sk-or-test"}
    transport, seen = jev({"same": {"type": "noul", "noul": 0.97}})
    got = await JevDecider(ctx, transport=transport).decide("x", {"same": SAME}, purpose="t")
    assert got["same"].value is True and seen[0]["model"] == "typesafe/jev-1.13"
    assert seen[0]["questions"]["same"]["type"] == "noul"
    usage = [u for u in ctx.store.usage_between(ctx.slug, "2000", "2100") if u["role"] == "decider"]
    assert usage and usage[0]["cost_usd"] == 0.000019
    moved, _ = jev({"same": {"type": "noul", "noul": 0.9}}, model="typesafe/jev-1.14-20261101")
    await JevDecider(ctx, transport=moved).decide("x", {"same": SAME}, purpose="t")
    await JevDecider(ctx, transport=moved).decide("x", {"same": SAME}, purpose="t")
    assert len(events(ctx, "decider.model_changed")) == 1
    notes = [m for m in ctx.store.list_messages(ctx.slug) if "versão" in m.title]
    assert len(notes) == 1 and notes[0].executive_audit() == []
    await ctx.aclose()


async def test_a_failure_answers_nothing_never_a_guess(factory: Factory):  # noqa: F811
    ctx = make_ctx(factory)
    ctx.secrets = {"OPENROUTER_API_KEY": "sk-or-test"}
    broken, _ = jev({}, status=500)
    assert await JevDecider(ctx, transport=broken).decide("x", {"same": SAME}, purpose="t") is None
    partial, _ = jev({"other": {"type": "noul", "noul": 0.9}})
    assert await JevDecider(ctx, transport=partial).decide("x", {"same": SAME}, purpose="t") is None
    assert len(events(ctx, "decider.unavailable")) == 2
    ctx.secrets = {}
    assert await JevDecider(ctx).decide("x", {"same": SAME}, purpose="t") is None  # no key
    await ctx.aclose()


async def test_off_by_default_and_the_dry_run_never_decides(factory: Factory):  # noqa: F811
    ctx = make_ctx(factory)
    assert build_decider(ctx) is None
    ctx.config.decider.mode = "decide"
    dry = make_ctx(factory, dry_run=True)
    dry.config.decider.mode = "decide"
    fixed = build_decider(dry)
    assert isinstance(fixed, FixedDecider)
    got = await fixed.decide("x", {"s": SUPPORTS}, purpose="t")
    assert got["s"].confidence == 0.0
    await ctx.aclose()
    await dry.aclose()


class Scripted:
    def __init__(self, answer: Answer):
        self.answer = answer

    async def decide(self, state, questions, *, purpose, story_id=None):  # noqa: ANN001
        return dict.fromkeys(questions, self.answer)


def _report() -> dict:
    return {
        "findings": [{"text": "O Node 24 é LTS.", "sources": ["https://nodejs.org/x"]}],
        "sources": ["https://nodejs.org/x"],
        "dropped_sources": [],
        "limitations": [],
        "sourced": 1,
    }


async def test_a_source_that_does_not_support_its_sentence(factory: Factory, monkeypatch):  # noqa: F811
    ctx = make_ctx(factory)
    no = Answer("noul", False, 0.95)
    monkeypatch.setattr("loompa.agents.analyst.build_decider", lambda c: Scripted(no))
    text = {"https://nodejs.org/x": "Release schedule…"}
    state = StoryState(story_id="S-001", title="Pesquisa")
    ctx.config.decider.mode = "shadow"
    kept = await AnalystAgent(ctx).check_claims(state, _report(), text)
    assert kept["findings"][0]["sources"] == ["https://nodejs.org/x"]  # shadow changes nothing
    assert events(ctx, "decider.shadow")[0]["payload"]["supports"] is False
    ctx.config.decider.mode = "decide"
    dropped = await AnalystAgent(ctx).check_claims(state, _report(), text)
    assert dropped["findings"][0]["sources"] == [] and dropped["sourced"] == 0
    assert "não sustentavam" in dropped["limitations"][-1]
    unsure = Answer("noul", False, 0.6)
    monkeypatch.setattr("loompa.agents.analyst.build_decider", lambda c: Scripted(unsure))
    held = await AnalystAgent(ctx).check_claims(state, _report(), text)
    assert held["findings"][0]["sources"]  # below the threshold, the LLM's citation stands
    await ctx.aclose()


async def test_pushed_passages_are_scored_beside_the_prompt(factory: Factory, monkeypatch):  # noqa: F811
    ctx = make_ctx(factory)
    ctx.memory.upsert_document("specs/S-001/spec.md", "Exportar CSV.", kind="spec", title="S-001")
    monkeypatch.setattr(
        "loompa.agents.base.build_decider", lambda c: Scripted(Answer("score", 2, 0.9))
    )
    text = LoompaAgent(ctx).precedents("exportar csv")
    assert "Exportar CSV" in text  # the prompt is the same as without the decider
    await asyncio.gather(*list(_SHADOW))
    (shadow,) = events(ctx, "decider.shadow")
    assert shadow["payload"]["passages"] == [["specs/S-001/spec.md", 2, 0.9]]
    await ctx.aclose()
