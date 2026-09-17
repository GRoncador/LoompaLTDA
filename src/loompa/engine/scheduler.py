"""Async scheduler: N stories at once, each an isolated task; a paused story never blocks others."""

from __future__ import annotations

import asyncio
import logging
import traceback
from dataclasses import dataclass, field
from typing import Any

from loompa.comms import FounderAnswer
from loompa.engine.context import EngineContext
from loompa.engine.graph import NODES, apply_founder_answer, block
from loompa.engine.state import PAUSED, TERMINAL, BlockedReason, Stage, StoryState
from loompa.finance import BudgetStatus

log = logging.getLogger("loompa.scheduler")


def load_state(ctx: EngineContext, story_id: str) -> StoryState:
    row = ctx.store.get_story(story_id)
    if row is None:
        raise KeyError(story_id)
    data = dict(row["state"] or {})
    data.update(
        {
            "story_id": story_id,
            "title": row["title"],
            "description": row.get("description", ""),
            "epic": row.get("epic", ""),
            "stage": row["stage"],
        }
    )
    return StoryState.model_validate(data)


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


class StoryRunner:
    def __init__(self, ctx: EngineContext, story_id: str):
        self.ctx = ctx
        self.story_id = story_id

    async def step(self, state: StoryState) -> StoryState:
        node = NODES.get(state.stage)
        if node is None:
            return state
        name = node.__name__
        try:
            state = await node(self.ctx, state)
        except Exception as exc:  # noqa: BLE001 - any crash isolates this story only
            log.exception("story %s failed in %s", state.story_id, name)
            technical = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-3000:]}"
            self.ctx.emit("story.error", story_id=state.story_id, node=name, error=str(exc)[:300])
            state = await block(
                self.ctx, state, BlockedReason.PERSISTENT_FAILURE, technical, resume=state.stage
            )
        save_state(self.ctx, state, name)
        return state

    async def run(self) -> StoryState:
        state = load_state(self.ctx, self.story_id)
        while state.stage not in TERMINAL and state.stage not in PAUSED:
            state = await self.step(state)
        return state


@dataclass
class Scheduler:
    ctx: EngineContext
    max_parallel: int | None = None
    running: dict[str, asyncio.Task] = field(default_factory=dict)
    completed: list[str] = field(default_factory=list)

    @property
    def slots(self) -> int:
        return self.max_parallel or self.ctx.config.schedule.max_parallel

    def runnable(self) -> list[dict[str, Any]]:
        """Stories ready to execute. Kaizen-discovered cards wait in BACKLOG for the Founder's go."""
        return [
            s
            for s in self.ctx.store.list_stories(self.ctx.slug)
            if s["stage"] not in TERMINAL
            and s["stage"] not in PAUSED
            and s["id"] not in self.running
            and not (s["origin"] == "kaizen" and s["stage"] == Stage.BACKLOG)
        ]

    def promote(self, story_id: str) -> None:
        """Founder approves a backlog card (e.g. a Kaizen discovery) for execution."""
        state = load_state(self.ctx, story_id)
        if state.stage == Stage.BACKLOG:
            state.stage = Stage.SPEC
            save_state(self.ctx, state, "promote")
            self.ctx.emit("story.promoted", story_id=story_id)

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
            self.completed.append(sid)
            if task.exception():
                log.error("runner for %s crashed: %s", sid, task.exception())

    async def run(
        self, *, until_idle: bool = True, poll_interval: float = 1.0, max_cycles: int | None = None
    ) -> list[str]:
        """Dispatch and reap until nothing is runnable (or forever when until_idle=False)."""
        cycles = 0
        try:
            while True:
                self._dispatch()
                if not self.running:
                    if until_idle or (max_cycles and cycles >= max_cycles):
                        break
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
    def answer(self, message_id: str, answer: FounderAnswer) -> StoryState | None:
        """Apply an inbox reply; returns the updated story state (None for non-story messages)."""
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
        return state
