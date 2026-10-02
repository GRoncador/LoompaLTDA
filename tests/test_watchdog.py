"""The stall watchdog (Fase 8.1): a running story silent for too long is an Ops incident, and a
sleeping machine is noticed as a sleep, never taken for a hang."""

from __future__ import annotations

import asyncio
import os
import time
from pathlib import Path

from loompa.aci import run_command
from loompa.engine import Scheduler, Stage, load_state
from loompa.engine.scheduler import StoryRunner
from loompa.factory import Factory
from test_engine import factory, make_ctx, seed_story  # noqa: F401


def event_types(ctx, sid: str | None = None) -> list[str]:
    return [
        e["type"]
        for e in ctx.store.events_since(0, limit=5000)
        if sid is None or e["story_id"] == sid
    ]


async def test_a_silent_story_is_restarted_by_ops_and_then_asks_the_founder(
    factory: Factory, monkeypatch
):
    factory.config.schedule.stall_minutes = 0.005  # 0.3 s
    factory.config.schedule.ops_max_recoveries = 2
    factory.config.schedule.ops_retry_base_s = 0.01
    ctx = make_ctx(factory, dry_run=True)
    sid = seed_story(ctx, "Somar números")
    runs: list[str] = []

    async def hang(self: StoryRunner):
        runs.append(self.story_id)
        await asyncio.sleep(3600)  # a step that neither finishes nor says anything

    monkeypatch.setattr(StoryRunner, "run", hang)
    await asyncio.wait_for(Scheduler(ctx).run(until_idle=True, poll_interval=0.02), timeout=30)
    seen = event_types(ctx, sid)
    assert seen.count("story.stalled") == 3 and len(runs) == 3  # restarted twice, then asked
    retries = [
        e["payload"]
        for e in ctx.store.events_since(0, limit=5000)
        if e["type"] == "story.retry" and e["story_id"] == sid
    ]
    assert len(retries) == 2 and "parada" in retries[0]["cause"]
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER
    assert state.blocked_reason.value == "persistent_failure"
    msg = ctx.store.get_message(state.blocked_message_id)
    assert "parada" in msg.context and msg.executive_audit() == []
    await ctx.aclose()


async def test_a_story_that_keeps_saying_something_is_never_stalled(factory: Factory, monkeypatch):
    factory.config.schedule.stall_minutes = 0.005
    ctx = make_ctx(factory, dry_run=True)
    sid = seed_story(ctx, "Somar números")

    async def busy(self: StoryRunner):
        for _ in range(12):  # 1.2 s of work, never 0.3 s without an event
            ctx.emit("tool.call", story_id=self.story_id, agent="Worker Loompa", tool="read_file")
            await asyncio.sleep(0.1)
        state = load_state(ctx, self.story_id)
        state.stage = Stage.DONE
        ctx.store.update_story(self.story_id, stage=Stage.DONE.value)
        return state

    monkeypatch.setattr(StoryRunner, "run", busy)
    await asyncio.wait_for(Scheduler(ctx).run(until_idle=True, poll_interval=0.02), timeout=30)
    assert "story.stalled" not in event_types(ctx, sid)
    await ctx.aclose()


async def test_a_sleeping_machine_is_told_once_and_is_not_a_stall(factory: Factory):
    factory.config.schedule.stall_minutes = 20
    ctx = make_ctx(factory, dry_run=True)
    sched = Scheduler(ctx)
    task = asyncio.create_task(asyncio.sleep(3600), name="story:S-001")
    sched.running["S-001"] = task
    sched.last_seen["S-001"] = time.monotonic()
    # 15 minutes passed on the wall clock and none on the clock that stops in sleep
    sched._tick = (time.time() - 900, time.monotonic())
    sched._watch()
    assert event_types(ctx) == ["engine.slept", "inbox.new"] and not task.cancelled()
    (note,) = ctx.store.list_messages(ctx.slug, status="pending")
    assert "computador" in note.title and note.executive_audit() == []
    sched._watch()  # the next tick sees no new sleep: nothing more is said
    assert event_types(ctx) == ["engine.slept", "inbox.new"]
    # another sleep while the note is unread adds to it (tamagotchi-retro got four in an hour)
    sched._tick = (time.time() - 600, time.monotonic())
    sched._watch()
    (note,) = ctx.store.list_messages(ctx.slug, status="pending")
    assert "2 vezes, somando cerca de 25 minutos" in note.context and note.executive_audit() == []
    task.cancel()
    await ctx.aclose()


async def test_a_network_failure_right_after_a_wake_does_not_spend_a_recovery(factory: Factory):
    """tamagotchi-retro: three wakes in a row, the network still down each time, three
    recoveries spent and the story sent to the founder as a persistent failure."""
    from loompa.agents.ops import RETRY_AFTER_WAKE_S, OpsAgent
    from loompa.engine.state import StoryState
    from loompa.llm import LLMError

    ctx = make_ctx(factory, dry_run=True)
    ops, state = OpsAgent(ctx), StoryState(story_id="S-1", title="t")
    down = LLMError(
        "todos os modelos do tier falharam: openrouter: falha de rede (ConnectError)",
        retryable=True,
    )
    ctx.woke_at = time.monotonic()
    for _ in range(5):
        assert ops.on_failure(state, "dev", down) == RETRY_AFTER_WAKE_S
    assert not state.extra.get("ops_incident")
    ctx.woke_at = time.monotonic() - 3600  # long awake: a network failure is an incident again
    assert ops.on_failure(state, "dev", down) is not None
    assert state.extra["ops_incident"]["recoveries"] == 1
    await ctx.aclose()


async def test_cancelling_a_command_kills_what_it_started(tmp_path: Path):
    pid_file = tmp_path / "child.pid"
    job = asyncio.create_task(
        run_command(f"sh -c 'sleep 60 & echo $! > {pid_file}; wait'", tmp_path, timeout=120)
    )
    for _ in range(100):
        if pid_file.is_file() and pid_file.read_text().strip():
            break
        await asyncio.sleep(0.05)
    child = int(pid_file.read_text())
    job.cancel()
    try:
        await job
    except asyncio.CancelledError:
        pass
    for _ in range(100):  # SIGKILL is immediate; reaping may take a moment
        try:
            os.kill(child, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.05)
    else:
        raise AssertionError("the command's child process survived the cancel")
