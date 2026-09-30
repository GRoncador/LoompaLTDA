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
    ctx, sid, prompts = run_story(
        factory, lambda msgs: [ToolCall(f"c{len(msgs)}", "read_file", {"path": "app/calc.py"})]
    )
    await Scheduler(ctx).run(until_idle=True)
    state = load_state(ctx, sid)
    seen = types(ctx, sid)
    # every attempt stopped inside `dev`: the Inspector never judged a half-built story
    assert "inspector.verdict" not in seen and "worker.dod_incomplete" not in seen
    assert seen.count("story.task_unfinished") == 3  # tier 2 twice, tier 1 once (re-planned)
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
