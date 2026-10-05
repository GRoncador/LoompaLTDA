"""Pause, stop and play (12.x, the founder's design): a pause lets the model calls in flight
finish and stops each story right after; nothing failed and nothing is redone. No network."""

from __future__ import annotations

from typing import Any

from loompa.agents.dryrun import dry_run_script, role_of
from loompa.engine import Scheduler, Stage, load_state
from loompa.factory import Factory
from loompa.llm import EnginePaused, Message
from test_engine import factory, make_ctx, seed_story  # noqa: F401


def events(ctx, type_: str) -> list[dict]:
    return [e for e in ctx.store.events_since(0, limit=10_000) if e["type"] == type_]


async def test_a_pause_lets_the_call_in_flight_finish_and_stops_the_story(factory: Factory):  # noqa: F811
    calls: list[str] = []
    sched: Scheduler | None = None

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        calls.append(role_of(messages))
        if len(calls) == 3:  # the founder presses pause while this call is in flight
            sched.pause()
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Página de login")
    sched = Scheduler(ctx)
    await sched.run()  # until idle: paused with nothing running
    assert len(calls) == 3  # the third call finished; no fourth one started
    assert events(ctx, "story.paused") and not events(ctx, "story.error")
    assert sched.state == "paused" and events(ctx, "engine.paused")
    paused_at = load_state(ctx, sid).stage
    assert paused_at not in (Stage.AWAITING_FOUNDER, Stage.DONE)  # not blocked, not failed

    sched.resume()
    await sched.run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    await ctx.aclose()


def test_the_pause_is_not_an_exception_an_agent_swallows():
    assert not issubclass(EnginePaused, Exception)


async def test_an_exhausted_budget_pauses_the_same_way_and_lifts_itself(factory: Factory):  # noqa: F811
    ctx = make_ctx(factory)
    sched = Scheduler(ctx)
    ctx.config.budget.cap_usd = 0.0001
    from loompa.finance.tracker import UsageRecord

    ctx.tracker.record(
        UsageRecord(agent="a", role="worker", provider="p", model="m", tier="tier2",
                    input_tokens=1, output_tokens=1, reported_cost=1.0)
    )  # fmt: skip
    sched._dispatch()
    assert sched.pausing and sched.pause_reason == "budget" and ctx.router.holding
    ctx.config.budget.cap_usd = 1000.0
    sched._dispatch()
    assert not sched.pausing and not ctx.router.holding
    await ctx.aclose()
