"""Async scheduler over the LangGraph runtime: N stories at once, one thread per story.

A paused story (AWAITING_FOUNDER) simply ends its graph run; the scheduler picks the next
runnable one. Nothing ever blocks the line.

A runner that crashes outside a node (checkpointer, projection, resume) is handed to the Ops
Loompa like any other incident: transient causes are retried after a backoff, anything else
blocks the story with a plain-language inbox note. A story is never dispatched twice at once
and never re-dispatched in a tight loop.
"""

from __future__ import annotations

import asyncio
import logging
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from loompa.agents.ops import INCIDENT_KEY, OpsAgent, triage
from loompa.comms import FounderAnswer, FounderMessage
from loompa.engine.context import EngineContext
from loompa.engine.graph import BlockedReason, apply_founder_answer, block
from loompa.engine.langgraph_engine import GraphRuntime
from loompa.engine.state import PAUSED, TERMINAL, Stage, StoryState
from loompa.finance import BudgetStatus

log = logging.getLogger("loompa.scheduler")


def load_state(ctx: EngineContext, story_id: str) -> StoryState:
    row = ctx.store.get_story(story_id)
    if row is None:
        raise KeyError(story_id)
    return StoryState.from_row(row)


def save_state(ctx: EngineContext, state: StoryState, node: str) -> None:
    ctx.store.update_story(
        state.story_id,
        stage=state.stage.value,
        state=state.model_dump(mode="json"),
        attempts_tier2=state.attempts_tier2,
        attempts_tier1=state.attempts_tier1,
        branch=state.branch,
        worktree=state.worktree,
        blocked_message_id=state.blocked_message_id,
    )
    ctx.store.checkpoint(state.story_id, node, state.stage.value, state.model_dump(mode="json"))
    ctx.emit("story.stage", story_id=state.story_id, stage=state.stage.value, node=node)


def runtime_for(ctx: EngineContext) -> GraphRuntime:
    rt = getattr(ctx, "_graph_runtime", None)
    if rt is None:
        rt = GraphRuntime(ctx)
        ctx._graph_runtime = rt  # type: ignore[attr-defined]
    return rt


class StoryRunner:
    """Runs one story's LangGraph thread until it pauses or finishes."""

    def __init__(self, ctx: EngineContext, story_id: str):
        self.ctx = ctx
        self.story_id = story_id

    async def run(self) -> StoryState:
        state = load_state(self.ctx, self.story_id)
        return await runtime_for(self.ctx).run_story(state)


@dataclass
class Scheduler:
    ctx: EngineContext
    max_parallel: int | None = None
    running: dict[str, asyncio.Task] = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)
    crashes: dict[str, int] = field(default_factory=dict)  # runner crashes per story (session)
    not_before: dict[str, float] = field(default_factory=dict)  # story -> monotonic retry time

    @property
    def slots(self) -> int:
        return self.max_parallel or self.ctx.config.schedule.max_parallel

    def _eligible(self) -> list[dict[str, Any]]:
        return [
            s
            for s in self.ctx.store.list_stories(self.ctx.slug)
            if s["stage"] not in TERMINAL
            and s["stage"] not in PAUSED
            and s["id"] not in self.running
            and s["stage"] != Stage.BACKLOG  # cards wait for a sprint (or the founder's promote)
        ]

    def runnable(self) -> list[dict[str, Any]]:
        """Stories ready to execute: the ones the Product Owner admitted out of the backlog
        (a sprint start, an epic split, an explicit promote). A story whose runner just crashed
        waits out its Ops backoff first."""
        now = time.monotonic()
        return [s for s in self._eligible() if self.not_before.get(s["id"], 0.0) <= now]

    def waiting_for(self) -> float | None:
        """Seconds until the next crashed story may be retried, or None when nothing waits."""
        now = time.monotonic()
        waits = [
            self.not_before[s["id"]] - now
            for s in self._eligible()
            if self.not_before.get(s["id"], 0.0) > now
        ]
        return max(min(waits), 0.0) if waits else None

    def promote(self, story_id: str) -> None:
        """Founder sends one backlog card straight to work, outside any sprint (a hotfix lane)."""
        from loompa.agents.product_owner import ProductOwnerAgent

        ProductOwnerAgent(self.ctx).admit(story_id)

    def close_sprints(self) -> None:
        """Close sprints whose stories all finished (the Master tells the founder)."""
        from loompa.agents.master import MasterAgent

        MasterAgent(self.ctx).close_finished_sprints()

    def budget_ok(self) -> BudgetStatus:
        st = self.ctx.tracker.status()
        if st.warn:
            self.ctx.tracker.maybe_alert()
        return st

    def _dispatch(self) -> int:
        st = self.budget_ok()
        if st.exhausted:
            self.ctx.emit(
                "scheduler.paused", reason="budget_exhausted", month_cost_usd=st.month_cost_usd
            )
            return 0
        n = 0
        for story in self.runnable():
            if len(self.running) >= self.slots:
                break
            task = asyncio.create_task(
                StoryRunner(self.ctx, story["id"]).run(), name=f"story:{story['id']}"
            )
            self.running[story["id"]] = task
            self.ctx.emit("scheduler.dispatch", story_id=story["id"], running=len(self.running))
            n += 1
        return n

    async def _reap(self, timeout: float) -> None:
        if not self.running:
            await asyncio.sleep(timeout)
            return
        done, _ = await asyncio.wait(
            list(self.running.values()), timeout=timeout, return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            sid = task.get_name().split(":", 1)[1]
            self.running.pop(sid, None)
            if task.cancelled():
                continue
            exc = task.exception()
            if exc is None:
                self.crashes.pop(sid, None)
                self.not_before.pop(sid, None)
                if sid not in self.completed:
                    self.completed.append(sid)
                continue
            await self._on_runner_crash(sid, exc)

    async def _on_runner_crash(self, sid: str, exc: BaseException) -> None:
        """Ops triage for a crash outside the nodes (checkpointer, projection, resume)."""
        log.error("runner for %s crashed: %s", sid, exc, exc_info=exc)
        n = self.crashes.get(sid, 0) + 1
        self.crashes[sid] = n
        ops = OpsAgent(self.ctx)
        t = triage(exc)
        self.ctx.emit("story.error", story_id=sid, node="runner", error=str(exc)[:300])
        if t.transient and n <= ops.max_recoveries:
            wait = ops.wait_for(n)
            self.not_before[sid] = time.monotonic() + wait
            self.ctx.emit(
                "story.retry",
                story_id=sid,
                node="runner",
                wait_s=wait,
                recoveries=n,
                cause=t.cause,
            )
            return
        technical = (
            f"{type(exc).__name__}: {exc}\n" + "".join(traceback.format_exception(exc))[-3000:]
        )
        try:
            state = load_state(self.ctx, sid)
            state.extra[INCIDENT_KEY] = {
                "node": "runner",
                "cause": t.cause,
                "recoveries": n,
                "transient": t.transient,
            }
            state = await block(
                self.ctx,
                state,
                BlockedReason.PERSISTENT_FAILURE,
                technical,
                resume=state.phase or state.stage,  # the phase, not its kanban column
                executive=ops.executive_reason(state),
            )
            state.extra.pop(INCIDENT_KEY, None)
            save_state(self.ctx, state, "runner_crash")
        except Exception:  # noqa: BLE001 - never let the escalation itself kill the line
            log.exception("could not escalate runner crash for %s; parking it", sid)
            self.not_before[sid] = time.monotonic() + ops.wait_for(n)

    async def run(
        self, *, until_idle: bool = True, poll_interval: float = 1.0, max_cycles: int | None = None
    ) -> list[str]:
        """Dispatch and reap until nothing is runnable (or forever when until_idle=False)."""
        cycles = 0
        try:
            while True:
                self._dispatch()
                self.close_sprints()
                if not self.running:
                    wait = self.waiting_for()
                    if wait is not None and until_idle:
                        await asyncio.sleep(min(wait, poll_interval) if wait else 0)
                    elif until_idle or (max_cycles and cycles >= max_cycles):
                        break
                    else:
                        await asyncio.sleep(poll_interval)
                else:
                    await self._reap(poll_interval)
                cycles += 1
                if max_cycles and cycles >= max_cycles:
                    break
        finally:
            for task in self.running.values():
                task.cancel()
            if self.running:
                await asyncio.gather(*self.running.values(), return_exceptions=True)
            self.running.clear()
        return self.completed

    # --------------------------------------------------------------- founder
    async def aanswer(self, message_id: str, answer: FounderAnswer) -> StoryState | None:
        """Apply an inbox reply; returns the updated story state (None for non-story messages)."""
        state = await self._apply_answer(message_id, answer)
        self.close_sprints()  # an approval or a cancel may have finished the sprint
        return state

    def _decide_cards(self, msg: FounderMessage, answer: FounderAnswer) -> None:
        """Apply the side decisions of a batch answer, each on its own. Unknown ids, unknown
        options and cards already decided are ignored; an undecided card simply stays in the
        backlog, so nothing a story found is ever lost."""
        from loompa.agents.product_owner import ProductOwnerAgent

        po = ProductOwnerAgent(self.ctx)
        for decision in msg.decisions:
            choice = answer.decisions.get(decision.id)
            if not choice or decision.chosen or choice not in {o.key for o in decision.options}:
                continue
            po.resolve_finding(decision.id, choice)
            decision.chosen = choice

    async def _apply_answer(self, message_id: str, answer: FounderAnswer) -> StoryState | None:
        msg = self.ctx.store.get_message(message_id)
        if msg is None:
            raise KeyError(message_id)
        if answer.decisions:
            self._decide_cards(msg, answer)
            self.ctx.store.put_message(msg)
            if not answer.option_key and not answer.text:
                # only the side decisions were answered: the message itself still waits
                self.ctx.emit("inbox.decided", story_id=msg.story_id, message_id=message_id)
                return load_state(self.ctx, msg.story_id) if msg.story_id else None
        msg = self.ctx.store.answer_message(message_id, answer)
        self.ctx.emit(
            "inbox.answered", story_id=msg.story_id, message_id=message_id, option=answer.option_key
        )
        if msg.kind.value == "finance" and answer.option_key in ("raise_10", "raise_30"):
            self.ctx.config.budget.monthly_cap_usd += 10 if answer.option_key == "raise_10" else 30
            self.ctx.factory.save()
        if not msg.story_id:
            return None
        state = load_state(self.ctx, msg.story_id)
        if state.stage != Stage.AWAITING_FOUNDER:
            return state
        state = apply_founder_answer(self.ctx, state, msg, answer)
        save_state(self.ctx, state, "founder_answer")
        await runtime_for(self.ctx).inject_founder_answer(state)
        return state

    def answer(self, message_id: str, answer: FounderAnswer) -> StoryState | None:
        """Sync facade for CLI code paths (no running event loop)."""
        return asyncio.run(self.aanswer(message_id, answer))
