"""Fase 1 (ADR-0006): pipeline as data, spec review, graded QA gate, DoD, complexity→tier."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from loompa.agents.dryrun import dry_run_script, role_of
from loompa.agents.inspector import grade
from loompa.comms import FounderAnswer
from loompa.config import default_config
from loompa.engine import PHASES, Complexity, Scheduler, Stage, StoryKind, build_route, load_state
from loompa.engine.phases import ensure_route
from loompa.engine.state import StoryState
from loompa.factory import Factory
from loompa.llm import Message, ModelRouter, ToolCall
from test_engine import factory, make_ctx, seed_story, tool_results  # noqa: F401

Script = Callable[[str, list[Message], list[dict[str, Any]] | None], Any]


def scripted(overrides: dict[str, Callable[[str, list[Message]], Any]]) -> Script:
    """Dry-run behaviour for every role except the ones overridden (keyed by role marker)."""

    def script(model: str, messages: list[Message], tools: list[dict[str, Any]] | None) -> Any:
        fn = overrides.get(role_of(messages))
        return fn(model, messages) if fn else dry_run_script(model, messages, tools)

    return script


def good_worker(model: str, messages: list[Message]) -> Any:
    if not tool_results(messages):
        return [
            ToolCall(
                "w1",
                "write_file",
                {"path": "tests/test_new.py", "content": "def test_new():\n    assert True\n"},
            )
        ]
    return [ToolCall("w2", "done", {"summary": "teste escrito"})]


# ------------------------------------------------------------------ routes as data


def test_routes_by_kind_and_complexity():
    assert build_route("feature", "STANDARD") == [
        "intake",
        "spec",
        "spec_review",
        "plan",
        "dev",
        "test",
        "review",
    ]
    assert "spec_review" not in build_route(StoryKind.FEATURE, Complexity.SIMPLE)
    assert "spec_review" not in build_route(StoryKind.BUGFIX, Complexity.STANDARD)
    assert "spec_review" in build_route(StoryKind.BUGFIX, Complexity.COMPLEX)
    assert all(p in PHASES for p in build_route("research", "COMPLEX"))
    assert {p.owner for p in PHASES.values()} >= {"master", "product", "product_owner", "worker"}
    assert PHASES["spec"].reviewer == "product_owner" and PHASES["test"].description


def test_legacy_state_without_route_resumes_from_its_stage():
    state = StoryState(story_id="S-9", title="antiga", stage=Stage.DEV)
    ensure_route(state)
    assert state.phase == "dev" and state.next_phase() == "test"
    state.hand_off("olhe o cache", phase="test")
    assert state.handoff_from("spec_review", "test") == "[test] olhe o cache"


def test_complexity_steers_the_tier():
    router = ModelRouter(default_config())
    assert router.candidates("product")[0] == "tier2"
    assert router.candidates("product", complexity="COMPLEX")[0] == "tier1"
    assert router.candidates("worker", complexity="COMPLEX")[0] == "tier2"  # only review roles lift
    assert router.candidates("master", complexity="SIMPLE")[0] == "tier2"
    assert router.candidates("master", tier_override="tier1", complexity="SIMPLE")[0] == "tier1"


# ------------------------------------------------------------------- intake / epic


async def test_intake_classifies_and_splits_epics(factory: Factory):
    def master(model: str, messages: list[Message]) -> Any:
        if "Classify the story" in messages[0].content:
            big = "Plataforma completa" in messages[-1].content
            return json.dumps(
                {
                    "kind": "bugfix" if "corrigir" in messages[-1].content.lower() else "feature",
                    "complexity": "COMPLEX" if big else "SIMPLE",
                    "children": [
                        {"title": "Cadastro de usuários", "description": "parte 1"},
                        {"title": "Cobrança recorrente", "description": "parte 2"},
                    ]
                    if big
                    else [],
                    "reason": "teste",
                }
            )
        return dry_run_script(model, messages, None)

    ctx = make_ctx(factory, scripted({"master": master}), dry_run=True)
    epic = seed_story(ctx, "Plataforma completa", "cadastro, cobrança e relatórios")
    small = seed_story(ctx, "Corrigir acento no título")
    done = await Scheduler(ctx).run()
    parent = load_state(ctx, epic)
    assert parent.stage == Stage.DONE and parent.extra["children"] == ["S-003", "S-004"]
    assert parent.kind == StoryKind.FEATURE and parent.complexity == Complexity.COMPLEX
    kids = [s for s in ctx.store.list_stories(factory.slug) if s["origin"] == "epic"]
    assert [k["title"] for k in kids] == ["Cadastro de usuários", "Cobrança recorrente"]
    assert all(k["epic"] == "Plataforma completa" for k in kids)
    # children ran on their own and the small bugfix skipped the spec review
    assert sorted(done) == sorted([small, epic, "S-003", "S-004"])
    for sid in ("S-003", "S-004"):
        assert load_state(ctx, sid).stage == Stage.AWAITING_FOUNDER
    s = load_state(ctx, small)
    assert s.kind == StoryKind.BUGFIX and s.complexity == Complexity.SIMPLE
    assert "spec_review" not in s.route and s.stage == Stage.AWAITING_FOUNDER
    info = [m for m in ctx.store.list_messages(factory.slug) if m.story_id == epic]
    assert info and info[0].kind == "info" and info[0].executive_audit() == []
    await ctx.aclose()


# ---------------------------------------------------------------------- spec review


async def test_spec_review_rejects_once_then_product_rewrites(factory: Factory):
    seen_reviews = {"n": 0}
    product_prompts: list[str] = []

    def po(model: str, messages: list[Message]) -> Any:
        seen_reviews["n"] += 1
        if seen_reviews["n"] == 1:
            return json.dumps(
                {
                    "approved": False,
                    "unsupported": ["Dado o sistema, quando exporta PDF, então funciona"],
                    "missing": [],
                    "notes": "ninguém pediu PDF",
                }
            )
        return json.dumps({"approved": True, "unsupported": [], "missing": [], "notes": "ok"})

    def product(model: str, messages: list[Message]) -> Any:
        product_prompts.append(messages[-1].content)
        return dry_run_script(model, messages, None)

    ctx = make_ctx(factory, scripted({"product_owner": po, "product": product}), dry_run=True)
    sid = seed_story(ctx, "Exportar CSV")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.spec_review_rounds == 2
    assert len(product_prompts) == 2 and "ninguém pediu PDF" in product_prompts[1]
    assert "PDF" not in product_prompts[0]
    types = [e["type"] for e in ctx.store.events_since(0, limit=10_000)]
    assert types.count("spec.rejected") == 1 and types.count("spec.approved") == 1
    await ctx.aclose()


# ------------------------------------------------------------------- graded QA gate


def test_grade_rules():
    assert grade(True, [], True) == "PASS"
    assert grade(True, [{"severity": "low"}], True) == "CONCERNS"
    assert grade(True, [{"severity": "medium"}, {"severity": "high"}], True) == "WAIVED"
    assert grade(False, [{"severity": "high"}], True) == "FAIL"
    assert grade(True, [], False) == "FAIL"


def inspector_with(findings: list[dict[str, str]]) -> Callable[[str, list[Message]], Any]:
    def fn(model: str, messages: list[Message]) -> Any:
        return json.dumps({"criteria": [], "findings": findings, "summary": "revisado"})

    return fn


@pytest.mark.parametrize("answer", ["fix", "waive"])
async def test_waived_verdict_asks_the_founder(factory: Factory, answer: str):
    calls = {"inspector": 0}

    def inspector(model: str, messages: list[Message]) -> Any:
        calls["inspector"] += 1
        findings = (
            [{"prefix": "SEC", "severity": "high", "text": "senha gravada em texto puro"}]
            if calls["inspector"] == 1
            else []
        )
        return inspector_with(findings)(model, messages)

    ctx = make_ctx(factory, scripted({"worker": good_worker, "inspector": inspector}))
    sid = seed_story(ctx, "Login")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "waiver"
    assert state.qa_verdict == "WAIVED" and state.qa_findings[0]["id"] == "SEC-1"
    msg = ctx.store.get_message(state.blocked_message_id)
    assert msg.kind == "decision" and msg.executive_audit() == []
    assert [o.key for o in msg.options] == ["fix", "waive", "drop"]
    assert "senha gravada em texto puro" in msg.context
    state = await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key=answer))
    if answer == "fix":
        assert state.stage == Stage.DEV and state.phase == "dev"
        assert "Inspector apontou" in state.failure_history[-1]
        await Scheduler(ctx).run()
        final = load_state(ctx, sid)
        assert final.blocked_reason == "delivery" and final.qa_verdict == "PASS"
        assert calls["inspector"] == 2
    else:
        assert state.stage == Stage.REVIEW and state.phase == "review"
        await Scheduler(ctx).run()
        final = load_state(ctx, sid)
        assert final.blocked_reason == "delivery" and final.qa_verdict == "WAIVED"
        assert any(learn["kind"] == "risk" for learn in final.learnings)
        # the accepted risk is swept into a card at review and offered on the delivery
        delivery = ctx.store.get_message(final.blocked_message_id)
        assert [d.title for d in delivery.decisions] == ["Achado: [risk] Risco aceito pelo Founder"]
        assert delivery.executive_audit() == [] and final.finding_cards == [
            delivery.decisions[0].id
        ]
    await ctx.aclose()


async def test_concerns_ship_and_feed_kaizen(factory: Factory):
    findings = [{"prefix": "perf", "severity": "medium", "text": "consulta sem índice"}]
    ctx = make_ctx(
        factory, scripted({"worker": good_worker, "inspector": inspector_with(findings)})
    )
    sid = seed_story(ctx, "Relatório")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery" and state.qa_verdict == "CONCERNS"
    assert state.qa_findings == [
        {"id": "PERF-1", "severity": "medium", "text": "consulta sem índice"}
    ]
    cards = [s for s in ctx.store.list_stories(factory.slug) if s["origin"] == "kaizen"]
    assert len(cards) == 1 and "PERF-1" in cards[0]["title"]
    assert any(r["kind"] == "opportunity" for r in ctx.store.list_learnings())
    await ctx.aclose()


# --------------------------------------------------------------------------- DoD


async def test_dod_check_reruns_an_incomplete_task(factory: Factory):
    worker_calls: list[str] = []
    dod_calls = {"n": 0}

    def worker(model: str, messages: list[Message]) -> Any:
        worker_calls.append(messages[-1].content if messages[-1].role == "user" else "tool")
        if not tool_results(messages):
            return [
                ToolCall(
                    "w1",
                    "write_file",
                    {"path": "tests/test_dod.py", "content": "def test_dod():\n    assert True\n"},
                )
            ]
        return [ToolCall("w2", "done", {"summary": "feito"})]

    def dod(model: str, messages: list[Message]) -> Any:
        dod_calls["n"] += 1
        assert "```diff" in messages[-1].content and "test_dod" in messages[-1].content
        if dod_calls["n"] == 1:
            return json.dumps({"complete": False, "missing": ["faltou o caso de erro"]})
        return json.dumps({"complete": True, "missing": []})

    ctx = make_ctx(factory, scripted({"worker": worker, "dod": dod}))
    sid = seed_story(ctx, "Com DoD")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery"
    assert dod_calls["n"] == 1  # the follow-up run is not re-checked
    followups = [c for c in worker_calls if "faltou o caso de erro" in c]
    assert followups and "Self-check found these still missing" in followups[0]
    types = [e["type"] for e in ctx.store.events_since(0, limit=10_000)]
    assert types.count("worker.dod_incomplete") == 1
    await ctx.aclose()
