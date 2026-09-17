"""End-to-end engine tests driven by scripted providers (no network)."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import git
from loompa.agents import MasterAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.comms import FounderAnswer, MessageStatus
from loompa.engine import EngineContext, Scheduler, Stage, load_state
from loompa.factory import Factory, bootstrap_factory
from loompa.llm import LLMError, Message, MockProvider, ModelRouter, ToolCall
from loompa.store import Store

PYTEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider'


@pytest.fixture
def factory(git_repo: Path, hub) -> Factory:
    (git_repo / "app").mkdir()
    (git_repo / "app" / "__init__.py").write_text("")
    (git_repo / "app" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_calc.py").write_text(
        "from app.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    git("add", ".", cwd=git_repo)
    git("commit", "-qm", "feat: calc", cwd=git_repo)
    result = bootstrap_factory(git_repo, name="Demo", store=hub)
    f = result.factory
    f.config.quality.test_command = PYTEST_CMD
    f.config.quality.lint_command = ""
    f.config.quality.typecheck_command = ""
    f.config.schedule.max_parallel = 2
    f.save()
    return Factory.open(git_repo)


def make_ctx(
    factory: Factory, script: Callable[..., Any] | None = None, *, dry_run: bool = False
) -> EngineContext:
    provider = MockProvider("mock", script=script or dry_run_script)
    router = ModelRouter(
        factory.config, providers=dict.fromkeys(factory.config.providers, provider)
    )
    return EngineContext.build(factory, router=router, dry_run=dry_run)


def with_worker(worker_fn: Callable[[str, list[Message]], Any]) -> Callable[..., Any]:
    def script(model: str, messages: list[Message], tools: list[dict[str, Any]] | None) -> Any:
        role = role_of(messages)
        if role == "worker":
            return worker_fn(model, messages)
        return dry_run_script(model, messages, tools)

    return script


def seed_story(ctx: EngineContext, title: str, description: str = "") -> str:
    sid = ctx.store.next_story_id(ctx.slug)
    ctx.store.upsert_story(
        {
            "id": sid,
            "factory": ctx.slug,
            "title": title,
            "description": description,
            "stage": Stage.BACKLOG,
        }
    )
    return sid


def tool_results(messages: list[Message]) -> list[str]:
    return [m.content for m in messages if m.role == "tool"]


# ------------------------------------------------------------------------ happy path


async def test_dry_run_pipeline_delivers_and_founder_approves(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    result = await MasterAgent(ctx).meeting("Página de login; Exportar relatório em CSV")
    assert [s["title"] for s in result["stories"]] == [
        "Página de login",
        "Exportar relatório em CSV",
    ]
    done = await Scheduler(ctx).run()
    assert sorted(done) == ["S-001", "S-002"]
    for sid in ("S-001", "S-002"):
        state = load_state(ctx, sid)
        assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
        assert (
            state.spec_ready
            and state.plan_ready
            and state.tasks_done == [1]
            and len(state.commits) == 1
        )
        specs = factory.paths.specs / sid
        assert (
            (specs / "spec.md").is_file()
            and (specs / "plan.md").is_file()
            and "[x] T1" in (specs / "tasks.md").read_text()
        )
        assert (Path(state.worktree) / "loompa_dryrun").is_dir()
    msgs = [
        m for m in ctx.store.list_messages(factory.slug, status="pending") if m.kind == "delivery"
    ]
    assert len(msgs) == 2 and all(m.executive_audit() == [] for m in msgs)
    # stories are isolated: two worktrees, two branches, root untouched
    assert len(ctx.worktrees.list()) == 2 and not (factory.root / "loompa_dryrun").exists()
    # founder approves one delivery in the evening review
    approve = next(m for m in msgs if m.story_id == "S-001")
    state = Scheduler(ctx).answer(approve.id, FounderAnswer(option_key="approve"))
    assert state.stage == Stage.DONE and state.merged_sha
    assert (factory.root / "loompa_dryrun").is_dir()
    assert "feat(s-001)" in git("log", "--oneline", "-1", cwd=factory.root)
    assert ctx.worktrees.get("S-001") is None and ctx.worktrees.get("S-002") is not None
    # and asks for changes on the other
    changes = next(m for m in msgs if m.story_id == "S-002")
    state = Scheduler(ctx).answer(
        changes.id, FounderAnswer(option_key="changes", text="quero também o formato Excel")
    )
    assert state.stage == Stage.DEV and "Excel" in state.founder_notes[0]
    assert ctx.store.checkpoints("S-002")[-1]["node"] == "founder_answer"
    # usage was metered per story
    assert ctx.store.usage_totals(factory.slug, story_id="S-001")["calls"] >= 3
    ctx.close()


# ------------------------------------------------------------------------- escalation


async def test_escalation_ladder_tier2_to_tier1_and_constitution_lesson(factory: Factory):
    seen_models: list[str] = []

    def worker(model: str, messages: list[Message]) -> Any:
        seen_models.append(model)
        results = tool_results(messages)
        if not results:
            content = (
                "def test_new():\n    assert 1 == 1\n"
                if model == "deepseek-reasoner"
                else "def test_new():\n    assert 1 == 2\n"
            )
            return [ToolCall("w1", "write_file", {"path": "tests/test_new.py", "content": content})]
        return [ToolCall("w2", "done", {"summary": f"escrevi teste com {model}"})]

    ctx = make_ctx(factory, with_worker(worker))
    sid = seed_story(ctx, "Novo teste")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    assert state.attempts_tier2 == 2 and state.current_tier == "tier1" and state.attempts_tier1 == 0
    # tier2 twice (initial + one retry), then tier1 fixes it
    tiers = [r["key"] for r in ctx.store.usage_by("tier", factory.slug)]
    assert set(tiers) == {"tier1", "tier2"}
    assert seen_models.count("deepseek-chat") == 4 and seen_models.count("deepseek-reasoner") == 2
    types = [e["type"] for e in ctx.store.events_since(0)]
    assert types.count("story.retry") == 1 and types.count("story.escalated") == 1
    assert len(state.failure_history) == 2 and "assert 1 == 2" in state.failure_history[0]
    assert (
        "[pytest] FAIL" in state.failure_history[0] and "Traceback" not in state.failure_history[0]
    )
    # Kaizen: lesson added to the constitution and resolution stored in memory
    constitution = factory.paths.constitution.read_text()
    assert f"{sid}] Sempre rodar a suíte completa" in constitution
    assert "constitution.lesson" in types
    assert ctx.memory.search("suíte completa", kinds=("constitution",))
    assert any(row["kind"] == "resolution" for row in ctx.store.list_learnings())
    ctx.close()


async def test_persistent_failure_blocks_only_that_story(factory: Factory):
    def worker(model: str, messages: list[Message]) -> Any:
        if "história boa" in messages[0].content.lower():
            return (
                [ToolCall("w2", "done", {"summary": "nada a fazer"})]
                if tool_results(messages)
                else [
                    ToolCall(
                        "w1",
                        "write_file",
                        {
                            "path": "tests/test_ok.py",
                            "content": "def test_ok():\n    assert True\n",
                        },
                    )
                ]
            )
        if not tool_results(messages):
            return [
                ToolCall(
                    "w1",
                    "write_file",
                    {
                        "path": "tests/test_bad.py",
                        "content": "def test_bad():\n    assert False, 'quebrado de propósito'\n",
                    },
                )
            ]
        return [ToolCall("w2", "done", {"summary": "tentei"})]

    ctx = make_ctx(factory, with_worker(worker))
    bad = seed_story(ctx, "História ruim")
    good = seed_story(ctx, "História boa")
    await Scheduler(ctx).run()
    bad_state, good_state = load_state(ctx, bad), load_state(ctx, good)
    assert good_state.stage == Stage.AWAITING_FOUNDER and good_state.blocked_reason == "delivery"
    assert (
        bad_state.stage == Stage.AWAITING_FOUNDER
        and bad_state.blocked_reason == "persistent_failure"
    )
    assert bad_state.attempts_tier2 == 2 and bad_state.attempts_tier1 == 1
    msg = ctx.store.get_message(bad_state.blocked_message_id)
    assert msg.kind == "blocked" and msg.requires_action and msg.executive_audit() == []
    assert "quebrado de propósito" not in msg.context and msg.technical_ref.endswith(f"{bad}.log")
    assert (factory.paths.logs / f"{bad}.log").read_text().count("quebrado de propósito") >= 1
    # founder: retry with guidance -> attempts reset, resumes at DEV
    state = Scheduler(ctx).answer(
        msg.id, FounderAnswer(option_key="retry", text="pode remover esse teste")
    )
    assert state.stage == Stage.DEV and state.attempts_tier2 == 0 and state.current_tier == "tier2"
    # founder: skip -> backlog with low priority; drop -> cancelled and worktree removed
    ctx.store.update_story(
        bad,
        stage="AWAITING_FOUNDER",
        state={
            **state.model_dump(mode="json"),
            "stage": "AWAITING_FOUNDER",
            "blocked_reason": "persistent_failure",
        },
    )
    ctx.store.put_message(msg.model_copy(update={"status": MessageStatus.PENDING, "answer": None}))
    state = Scheduler(ctx).answer(msg.id, FounderAnswer(option_key="skip"))
    assert state.stage == Stage.BACKLOG and ctx.store.get_story(bad)["priority"] == 900
    ctx.store.update_story(
        bad,
        stage="AWAITING_FOUNDER",
        state={
            **state.model_dump(mode="json"),
            "stage": "AWAITING_FOUNDER",
            "blocked_reason": "persistent_failure",
        },
    )
    ctx.store.put_message(msg.model_copy(update={"status": MessageStatus.PENDING, "answer": None}))
    state = Scheduler(ctx).answer(msg.id, FounderAnswer(option_key="drop"))
    assert state.stage == Stage.CANCELLED and ctx.worktrees.get(bad) is None
    ctx.close()


# ------------------------------------------------------------------- questions/kaizen


async def test_worker_question_pauses_and_resumes_with_guidance(factory: Factory):
    def worker(model: str, messages: list[Message]) -> Any:
        notes = "Orientações do Founder" in messages[1].content
        if not notes:
            return [
                ToolCall(
                    "q",
                    "blocked",
                    {
                        "reason": "Devo usar e-mail ou SMS para o código?",
                        "options": ["E-mail", "SMS"],
                    },
                )
            ]
        if not tool_results(messages):
            return [
                ToolCall(
                    "l",
                    "note_learning",
                    {
                        "title": "Função de envio duplicada em dois módulos",
                        "kind": "tech_debt",
                        "detail": "consolidar",
                    },
                ),
                ToolCall(
                    "w",
                    "write_file",
                    {
                        "path": "tests/test_reset.py",
                        "content": "def test_reset():\n    assert True\n",
                    },
                ),
            ]
        return [
            ToolCall(
                "d",
                "done",
                {"summary": "usei " + ("SMS" if "SMS" in messages[1].content else "e-mail")},
            )
        ]

    ctx = make_ctx(factory, with_worker(worker))
    sid = seed_story(ctx, "Recuperar senha")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert (
        state.stage == Stage.AWAITING_FOUNDER
        and state.blocked_reason == "question"
        and state.resume_stage == Stage.DEV
    )
    msg = ctx.store.get_message(state.blocked_message_id)
    assert (
        msg.executive_audit() == []
        and [o.label for o in msg.options][:2] == ["E-mail", "SMS"]
        or msg.options
    )
    state = Scheduler(ctx).answer(msg.id, FounderAnswer(option_key=msg.options[1].key))
    assert state.stage == Stage.DEV and state.founder_notes
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    # Kaizen captured the discovery: learnings.md, a backlog card, a store row and the memory index
    learnings = factory.paths.learnings.read_text()
    assert "Função de envio duplicada" in learnings and "Débito técnico" in learnings
    cards = [s for s in ctx.store.list_stories(factory.slug) if s["origin"] == "kaizen"]
    assert (
        len(cards) == 1
        and cards[0]["stage"] == "BACKLOG"
        and cards[0]["priority"] == 500
        and cards[0]["title"].startswith("[Débito técnico]")
    )
    # kaizen cards wait for the founder; once promoted they run like any story
    assert Scheduler(ctx).runnable() == []
    Scheduler(ctx).promote(cards[0]["id"])
    await Scheduler(ctx).run()
    assert load_state(ctx, cards[0]["id"]).stage == Stage.AWAITING_FOUNDER
    assert ctx.store.list_learnings()[0]["created_story_id"] == cards[0]["id"]
    assert ctx.memory.search("envio duplicado", kinds=("learning",))
    ctx.close()


async def test_product_decision_blocks_at_spec(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "product" and "Orientações do Founder" not in messages[1].content:
            return json.dumps(
                {
                    "needs_decision": True,
                    "question": "Cobrar por assento ou por uso?",
                    "context": "Os dois modelos mudam o desenho da cobrança.",
                    "options": ["Por assento", "Por uso"],
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Cobrança")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert (
        state.stage == Stage.AWAITING_FOUNDER
        and state.blocked_reason == "question"
        and state.resume_stage == Stage.SPEC
    )
    msg = ctx.store.get_message(state.blocked_message_id)
    assert "assento" in msg.title.lower() or "assento" in msg.context.lower()
    state = Scheduler(ctx).answer(msg.id, FounderAnswer(text="por uso, com franquia"))
    assert state.stage == Stage.SPEC and "por uso, com franquia" in state.founder_notes[0]
    await Scheduler(ctx).run()
    assert (
        load_state(ctx, sid).stage == Stage.AWAITING_FOUNDER
        and load_state(ctx, sid).blocked_reason == "delivery"
    )
    ctx.close()


# --------------------------------------------------------------------- robustness


async def test_node_crash_isolates_story_and_scheduler_survives(factory: Factory):
    calls = {"n": 0}

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "architect":
            calls["n"] += 1
            raise LLMError("provedor fora do ar")
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    ctx.router.max_retries = 0
    sid = seed_story(ctx, "Qualquer coisa")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert (
        state.stage == Stage.AWAITING_FOUNDER
        and state.blocked_reason == "persistent_failure"
        and state.resume_stage == Stage.PLAN
    )
    msg = ctx.store.get_message(state.blocked_message_id)
    assert msg.executive_audit() == [] and "LLMError" not in msg.context
    assert any(e["type"] == "story.error" for e in ctx.store.events_since(0))
    ctx.close()


async def test_budget_exhaustion_pauses_dispatch(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    ctx.config.budget.monthly_cap_usd = 0.000001
    ctx.store.record_usage(
        factory=factory.slug,
        agent="x",
        role="worker",
        provider="p",
        model="m",
        tier="tier2",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.01,
    )
    sid = seed_story(ctx, "Nada")
    await Scheduler(ctx).run(until_idle=True, max_cycles=1)
    assert load_state(ctx, sid).stage == Stage.BACKLOG
    events = [e["type"] for e in ctx.store.events_since(0)]
    assert "scheduler.paused" in events
    alert = [m for m in ctx.store.list_messages(factory.slug) if m.kind == "finance"]
    assert len(alert) == 1 and alert[0].executive_audit() == []
    state = Scheduler(ctx).answer(alert[0].id, FounderAnswer(option_key="raise_10"))
    assert state is None and Factory.open(factory.root).config.budget.monthly_cap_usd > 10
    ctx.close()


async def test_resume_from_checkpoint_after_interruption(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    sid = seed_story(ctx, "Retomável")
    runner_state = load_state(ctx, sid)
    from loompa.engine import StoryRunner

    runner = StoryRunner(ctx, sid)
    runner_state = await runner.step(runner_state)  # intake
    runner_state = await runner.step(runner_state)  # spec
    assert runner_state.stage == Stage.PLAN and ctx.store.get_story(sid)["stage"] == "PLAN"
    ctx.close()
    # "restart": a fresh context continues from the persisted stage
    ctx2 = make_ctx(factory, dry_run=True)
    await Scheduler(ctx2).run()
    state = load_state(ctx2, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and [
        c["node"] for c in ctx2.store.checkpoints(sid)
    ][:3] == ["node_intake", "node_spec", "node_plan"]
    ctx2.close()


def test_end_of_day_report_is_executive(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    seed_story(ctx, "A")
    msg = MasterAgent(ctx).end_of_day_report()
    assert (
        msg.kind == "info"
        and "Resumo do dia" in msg.title
        and "No backlog: 1" in msg.context
        and msg.executive_audit() == []
    )
    assert Store(factory.paths.state_db).list_messages(factory.slug)[0].id == msg.id
    ctx.close()


async def test_red_baseline_is_not_blamed_on_story(factory: Factory):
    # main already has a failing test: the story must still deliver, and the debt becomes a learning
    (factory.root / "tests" / "test_legacy.py").write_text("def test_legacy():\n    assert False\n")
    git("add", ".", cwd=factory.root)
    git("commit", "-qm", "test: legacy red", cwd=factory.root)
    ctx = make_ctx(factory, dry_run=True)
    sid = seed_story(ctx, "Entrega com base vermelha")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    assert state.extra["baseline"]["failing"] == ["test_legacy"]
    assert any("já falha" in row["title"] for row in ctx.store.list_learnings())
    assert any(e["type"] == "inspector.baseline_red" for e in ctx.store.events_since(0))
    ctx.close()
