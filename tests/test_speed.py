"""Fase 8.5, the first speed fixes: a task that goes in circles stops with a diagnosis, and a task
that did not reach `done` is not counted as done — the attempt fails before the Inspector."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

from conftest import git
from loompa.aci import ACI
from loompa.agents import ProductOwnerAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.agents.loopguard import LoopGuard
from loompa.agents.toolbox import PROFILES, Toolbox
from loompa.engine import EngineContext, Scheduler, Stage, load_state
from loompa.factory import Factory, bootstrap_factory
from loompa.llm import Message, MockProvider, ModelRouter, ToolCall
from loompa.speckit import story_dir, tasks_from_markdown

PYTEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider'


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    return tmp_path


# --------------------------------------------------------------------------- the guard


async def test_a_streak_of_repeats_with_nothing_changed_stops_the_task(tmp_path: Path):
    aci = ACI(_repo(tmp_path))
    guard = LoopGuard(aci, repeat_limit=2)
    args = {"path": "app/calc.py"}

    async def read() -> None:
        res = guard.before("read_file", args) or await aci.call("read_file", args)
        guard.after("read_file", args, res, Message("tool", res.output))

    await read()
    await read()  # 1st repeat
    assert not guard.stuck
    await aci.call("write_file", {"path": "app/other.py", "content": "x = 1\n"})
    await read()  # the tree changed: a real read, and the streak starts over
    assert guard.streak == 0
    await read()
    await read()
    assert guard.stuck and guard.streak == 2
    assert guard.summary() == "read_file path=app/calc.py (x3)"


async def test_the_tool_loop_ends_as_a_loop_when_the_guard_says_so(git_repo: Path, hub):
    from loompa.agents.worker import WorkerAgent

    root = _repo(git_repo)
    f = bootstrap_factory(root, name="Loop", store=hub).factory
    provider = MockProvider(
        "mock", script=lambda *a: [ToolCall("c", "read_file", {"path": "app/calc.py"})]
    )
    ctx = EngineContext.build(
        f, router=ModelRouter(f.config, providers=dict.fromkeys(f.config.providers, provider))
    )
    aci = ACI(root)
    guard = LoopGuard(aci, repeat_limit=3)
    loop = await WorkerAgent(ctx).tool_loop(
        [Message("system", "s"), Message("user", "u")],
        Toolbox(aci, PROFILES["worker"]),
        max_iterations=20,
        terminal=("done",),
        guard=guard,
    )
    assert loop.ended_by == "loop" and loop.tool_calls == 4  # one real read, three repeats
    await ctx.aclose()


# ------------------------------------------------------- light thinking (ADR-0016)


def test_the_signs_of_trouble_that_send_a_light_loop_back_to_the_default(tmp_path: Path):
    from loompa.aci.tools import ToolResult
    from loompa.agents.base import trouble

    guard = LoopGuard(ACI(_repo(tmp_path)))
    failing = ToolResult(True, "[tests] FAIL (1 failed)\n...")
    assert trouble("run_tests", failing, "", guard, 0) == "tests failing"
    assert trouble("run_tests", failing, "", guard, 0, red_tests_ok=True) == ""  # a reproducer
    assert trouble("run_tests", ToolResult(True, "[tests] PASS (3 passed)"), "", guard, 0) == ""
    wrote = ToolResult(True, "wrote x.py\n\n[quick check] problems in what you just wrote:\n- x")
    assert trouble("write_file", wrote, "", guard, 0) == "a problem in what it just wrote"
    assert trouble("read_file", ToolResult(True, "1| x"), "Stop reading", guard, 0)
    assert trouble("read_file", ToolResult(False, "error: no"), "", guard, 1) == ""
    assert trouble("read_file", ToolResult(False, "error: no"), "", guard, 2) == "tool errors"


async def test_a_light_loop_thinks_at_the_default_after_the_first_trouble(git_repo: Path, hub):
    from loompa.agents.worker import WorkerAgent

    root = _repo(git_repo)
    f = bootstrap_factory(root, name="Raise", store=hub).factory
    rounds = iter(
        [
            [ToolCall("w", "write_file", {"path": "app/bad.py", "content": "def broken(:\n"})],
            [ToolCall("d", "done", {"summary": "ok"})],
        ]
    )
    provider = MockProvider("mock", script=lambda *a: next(rounds))
    ctx = EngineContext.build(
        f, router=ModelRouter(f.config, providers=dict.fromkeys(f.config.providers, provider))
    )
    loop = await WorkerAgent(ctx).tool_loop(
        [Message("system", "s"), Message("user", "u")],
        Toolbox(ACI(root), PROFILES["worker"]),
        terminal=("done",),
        reasoning_effort="low",
        raise_on_trouble=True,
    )
    assert loop.ended_by == "done" and loop.raised == "a problem in what it just wrote"
    assert [c["reasoning_effort"] for c in provider.calls] == ["low", ""]
    assert [c["max_tokens"] for c in provider.calls] == [16384, 96000]
    raised = [e for e in ctx.store.events_since(0) if e["type"] == "llm.reasoning_raised"]
    assert len(raised) == 1 and raised[0]["payload"]["round"] == 1
    await ctx.aclose()


# --------------------------------------------------------------------- the whole story


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
    f = bootstrap_factory(git_repo, name="Speed", store=hub).factory
    f.config.quality.test_command = PYTEST_CMD
    f.config.quality.lint_command = ""
    f.config.schedule.ops_retry_base_s = 0.01
    f.config.schedule.worker_repeat_limit = 2
    f.save()
    return Factory.open(git_repo)


def run_story(factory: Factory, worker: Any) -> tuple[EngineContext, str, list[list[Message]]]:
    prompts: list[list[Message]] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "worker" and tools:
            prompts.append(list(messages))
            return worker(messages)
        return dry_run_script(model, messages, tools)

    provider = MockProvider("mock", script=script)
    router = ModelRouter(
        factory.config, providers=dict.fromkeys(factory.config.providers, provider)
    )
    ctx = EngineContext.build(factory, router=router)
    po = ProductOwnerAgent(ctx)
    sid = po.add_item("Somar números", "A calculadora soma dois números.").story_id
    po.admit(sid)
    return ctx, sid, prompts


def types(ctx: EngineContext, sid: str) -> list[str]:
    return [e["type"] for e in ctx.store.events_since(0, limit=5000) if e["story_id"] == sid]


async def test_a_task_going_in_circles_is_not_done_and_never_reaches_the_inspector(
    factory: Factory,
):
    # the base policy: later attempts think at the default (the effort experiment is off here)
    factory.config.schedule.effort_ab_low_share = 0.0
    ctx, sid, prompts = run_story(
        factory, lambda msgs: [ToolCall(f"c{len(msgs)}", "read_file", {"path": "app/calc.py"})]
    )
    await Scheduler(ctx).run(until_idle=True)
    state = load_state(ctx, sid)
    seen = types(ctx, sid)
    # every attempt stopped inside `dev`: the Inspector never judged a half-built story
    assert "inspector.verdict" not in seen and "worker.dod_incomplete" not in seen
    # the first attempt and three tries (ADR-0016 §5): tier 2 twice, tier 1 twice (re-planned)
    assert seen.count("story.task_unfinished") == 4
    assert "story.escalated" in seen and "story.replanned" in seen
    assert (
        state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason.value == "persistent_failure"
    )
    tasks = tasks_from_markdown(story_dir(factory.root, sid).tasks.read_text())
    assert not any(t.done for t in tasks) and state.tasks_done == []
    failure = state.failure_history[-1]
    assert "stopped before `done`" in failure and "read_file path=app/calc.py" in failure
    # the next attempt read the diagnosis of the one before
    later = [p for p in prompts if any("stopped before `done`" in m.content for m in p)]
    assert later, "the diagnosis never reached the next attempt"
    finished = [
        e["payload"]
        for e in ctx.store.events_since(0, limit=5000)
        if e["type"] == "worker.task_finished" and e["story_id"] == sid
    ]
    assert {f["outcome"] for f in finished} == {"unfinished"}
    msg = ctx.store.get_message(state.blocked_message_id)
    assert "não chegou ao fim" in f"{msg.title} {msg.context}" or msg.title
    # ADR-0016: the first attempt started light and went back to the default when it began to
    # repeat itself; every later attempt thought at the default from the start
    runs = [
        e["payload"]
        for e in ctx.store.events_since(0, limit=5000)
        if e["type"] == "worker.task" and e["story_id"] == sid
    ]
    assert runs[0]["effort"] == "low" and runs[0]["raised"] == "lookups answered from memory"
    assert {r["effort"] for r in runs[1:]} == {"default"}
    assert not any(r.get("raised") for r in runs[1:])
    await ctx.aclose()


async def test_a_task_cut_by_the_tool_call_limit_stays_pending(factory: Factory):
    factory.config.schedule.worker_max_iterations = 3
    factory.config.schedule.tier2_max_attempts = 1
    factory.config.schedule.tier1_max_attempts = 1
    factory.save()
    factory = Factory.open(factory.root)
    reads = iter(range(1, 1000))
    ctx, sid, _ = run_story(
        factory,
        # a different page each time: no repeat, the loop simply runs out of rounds
        lambda msgs: [ToolCall("c", "read_file", {"path": "app/calc.py", "start": next(reads)})],
    )
    await Scheduler(ctx).run(until_idle=True)
    state = load_state(ctx, sid)
    task_events = [
        e["payload"]
        for e in ctx.store.events_since(0, limit=5000)
        if e["type"] == "worker.task" and e["story_id"] == sid
    ]
    assert task_events and {t["ended_by"] for t in task_events} == {"limit"}
    assert "inspector.verdict" not in types(ctx, sid)
    assert state.tasks_done == [] and "tool calls a task may make" in state.failure_history[0]
    await ctx.aclose()


async def test_a_task_stopped_after_its_work_is_green_counts_as_done(factory: Factory):
    """contas S-041: the fix was in and the suite green when the Worker looped writing extra
    tests; two tier-1 attempts and a question to the founder followed. A task stopped by the
    limit that changed something, with the suite green and nothing missing, is finished."""
    factory.config.schedule.worker_max_iterations = 4
    factory.save()
    factory = Factory.open(factory.root)
    test = "from app.calc import add\n\n\ndef test_soma_dois():\n    assert add(2, 2) == 4\n"
    reads = iter(range(1, 1000))

    def worker(msgs: list[Message]) -> Any:
        if not any(m.role == "tool" for m in msgs):
            return [
                ToolCall("w", "write_file", {"path": "tests/test_soma_dois.py", "content": test})
            ]
        return [ToolCall("r", "read_file", {"path": "app/calc.py", "start": next(reads)})]

    ctx, sid, _ = run_story(factory, worker)
    await Scheduler(ctx).run(until_idle=True)
    state = load_state(ctx, sid)
    seen = types(ctx, sid)
    assert "worker.salvaged" in seen and "story.task_unfinished" not in seen
    assert state.blocked_reason.value == "delivery" and state.tasks_done == [1]
    assert "story.escalated" not in seen
    await ctx.aclose()
