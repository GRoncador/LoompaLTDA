"""Async scheduler over the LangGraph runtime: N stories at once, one thread per story.

A paused story (AWAITING_FOUNDER) simply ends its graph run; the scheduler picks the next
runnable one. Nothing ever blocks the line.

A runner that crashes outside a node (checkpointer, projection, resume) is handed to the Ops
Loompa like any other incident: transient causes are retried after a backoff, anything else
blocks the story with a plain-language inbox note. A story is never dispatched twice at once
and never re-dispatched in a tight loop.

A watchdog (Fase 8.1) covers what no timeout inside a story can: a running story that emits
nothing for `schedule.stall_minutes` is cancelled and handed to the Ops the same way (it resumes
from its last checkpoint). Silence is measured on the monotonic clock, which stops while the
machine sleeps, so a sleeping Mac is never taken for a hang; the sleep itself is noticed by the
wall clock running ahead of the monotonic one, and told once.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from loompa.agents.ops import INCIDENT_KEY, OpsAgent, StoryStalled, triage
from loompa.comms import FounderAnswer, FounderMessage, MessageKind
from loompa.engine.context import EngineContext
from loompa.engine.graph import BlockedReason, apply_founder_answer, back_to_backlog, block
from loompa.engine.langgraph_engine import GraphRuntime
from loompa.engine.lock import EngineLock
from loompa.engine.phases import goto
from loompa.engine.state import PAUSED, TERMINAL, Stage, StoryState
from loompa.finance import BudgetStatus
from loompa.models_sync import ModelSync

log = logging.getLogger("loompa.scheduler")

# The wall clock ran this far ahead of the monotonic one between two ticks: the machine slept.
SLEPT_S = 60.0
# ...and a sleep this long, with stories running, is worth one note to the founder.
SLEEP_NOTE_S = 300.0
_WATCHDOG_EVENTS = frozenset({"story.stalled"})  # never count as the story moving


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
    _downgraded: bool = False  # the budget put every role on free models, and it was said once
    # the watchdog: each running story's last event (monotonic time, and what it was)
    last_seen: dict[str, float] = field(default_factory=dict)
    last_event: dict[str, str] = field(default_factory=dict)
    stalled: dict[str, float] = field(default_factory=dict)  # cancelled story -> minutes silent
    _tick: tuple[float, float] | None = None  # (wall, monotonic) at the last watch

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
            and s["stage"] != Stage.BACKLOG  # cards wait for a sprint
        ]

    def runnable(self) -> list[dict[str, Any]]:
        """Stories ready to execute: the ones the Product Owner admitted out of the backlog
        (a sprint start, a card joining the running sprint, an epic split). A story whose runner
        just crashed waits out its Ops backoff first."""
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

    def close_sprints(self) -> None:
        """Close sprints whose stories all finished (the Master tells the founder)."""
        from loompa.agents.master import MasterAgent

        MasterAgent(self.ctx).close_finished_sprints()
        ModelSync(self.ctx).apply_deferred()  # an approved model swap waits for the work to end

    def budget_ok(self) -> BudgetStatus:
        st = self.ctx.tracker.status()
        if st.warn:
            self.ctx.tracker.maybe_alert()
        return st

    def _dispatch(self) -> int:
        st = self.budget_ok()
        if st.exhausted:
            self.ctx.emit(
                "scheduler.paused",
                reason="budget_exhausted",
                period=st.period,
                period_cost_usd=st.period_cost_usd,
            )
            return 0
        if st.downgrade and not self._downgraded:
            # The cap is spent but the founder chose to keep going on free models; the router
            # forces tier 3 on every call, so dispatch continues. Said once, not every tick.
            self.ctx.emit(
                "scheduler.downgraded",
                reason="budget_exhausted",
                period=st.period,
                period_cost_usd=st.period_cost_usd,
            )
        self._downgraded = st.downgrade
        n = 0
        for story in self.runnable():
            if len(self.running) >= self.slots:
                break
            task = asyncio.create_task(
                StoryRunner(self.ctx, story["id"]).run(), name=f"story:{story['id']}"
            )
            self.running[story["id"]] = task
            self.last_seen[story["id"]] = time.monotonic()
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
            self.last_seen.pop(sid, None)
            last = self.last_event.pop(sid, "")
            if task.cancelled():
                minutes = self.stalled.pop(sid, None)
                if minutes is not None:  # the watchdog's doing: an Ops incident like a crash
                    await self._on_runner_crash(sid, StoryStalled(sid, minutes, last))
                continue
            exc = task.exception()
            if exc is None:
                self.crashes.pop(sid, None)
                self.not_before.pop(sid, None)
                if sid not in self.completed:
                    self.completed.append(sid)
                continue
            await self._on_runner_crash(sid, exc)

    # --------------------------------------------------------------- watchdog
    def _heard(self, event: dict[str, Any]) -> None:
        """Listener on every event: a running story that emits one is alive."""
        sid = event.get("story_id")
        if sid in self.running and event.get("type") not in _WATCHDOG_EVENTS:
            self.last_seen[sid] = time.monotonic()
            self.last_event[sid] = " · ".join(
                str(x) for x in (event.get("type"), event.get("agent")) if x
            )

    def _watch(self) -> None:
        """One tick of the watchdog: a sleep of the machine, then stories silent for too long."""
        now_wall, now_mono = time.time(), time.monotonic()
        if self._tick is not None:
            slept = (now_wall - self._tick[0]) - (now_mono - self._tick[1])
            if slept >= SLEPT_S:
                self._on_sleep(slept)
        self._tick = (now_wall, now_mono)
        limit = self.ctx.config.schedule.stall_minutes * 60
        if limit <= 0:
            return
        for sid, task in list(self.running.items()):
            silent = now_mono - self.last_seen.get(sid, now_mono)
            if silent < limit or sid in self.stalled or task.done():
                continue
            self.stalled[sid] = silent / 60
            self.ctx.emit(
                "story.stalled",
                story_id=sid,
                agent=OpsAgent.display,
                silent_min=round(silent / 60, 1),
                last_event=self.last_event.get(sid, ""),
            )
            task.cancel()  # `_reap` hands it to the Ops: restart from the last checkpoint

    def _on_sleep(self, slept: float) -> None:
        """The machine slept: stories stopped with it (`contas`, 2026-09-30, twice). Told once per
        sleep, and only when there was work in flight."""
        running = sorted(self.running)
        self.ctx.emit("engine.slept", minutes=round(slept / 60, 1), running=running)
        if not running or slept < SLEEP_NOTE_S:
            return
        self.ctx.inbox(
            FounderMessage(
                factory=self.ctx.slug,
                kind=MessageKind.INFO,
                sender=OpsAgent.display,
                title="A fábrica ficou parada enquanto o computador dormia",
                context=(
                    f"O computador entrou em repouso por cerca de {slept / 60:.0f} minutos e as "
                    "entregas em andamento pararam nesse tempo. Elas retomaram sozinhas quando ele "
                    "voltou."
                ),
                impact=(
                    "Nada se perdeu. Para a fábrica trabalhar sem pausas, deixe o computador na "
                    "tomada e sem repouso automático enquanto ela roda."
                ),
                allow_free_text=False,
            )
        )

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
        """Dispatch and reap until nothing is runnable (or forever when until_idle=False).
        Raises `EngineBusy` when another engine already runs this factory."""
        cycles = 0
        lock = EngineLock(self.ctx.factory.paths.loompa / "engine.lock")
        lock.acquire()
        self.ctx.listeners.append(self._heard)
        try:
            while True:
                self._watch()
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
            if self._heard in self.ctx.listeners:
                self.ctx.listeners.remove(self._heard)
            lock.release()
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
            weekly = self.ctx.config.budget.period == "weekly"
            small, large = (5.0, 10.0) if weekly else (10.0, 30.0)
            self.ctx.config.budget.cap_usd += small if answer.option_key == "raise_10" else large
            self.ctx.factory.save()
        ModelSync(self.ctx).on_answer(msg, answer)
        if not msg.story_id:
            return None
        state = load_state(self.ctx, msg.story_id)
        if state.stage != Stage.AWAITING_FOUNDER:
            return state
        state = apply_founder_answer(self.ctx, state, msg, answer)
        save_state(self.ctx, state, "founder_answer")
        await runtime_for(self.ctx).inject_founder_answer(state)
        return state

    async def reopen_delivery(self, story_id: str, reason: str) -> StoryState | None:
        """A reviewer other than the founder (CodeRabbit on the PR) asked for changes on a
        delivery waiting for review: back to `dev` with the review as a finding, and the
        delivery message leaves the inbox. The founder's own notes stay untouched."""
        state = load_state(self.ctx, story_id)
        if state.stage != Stage.AWAITING_FOUNDER or state.blocked_reason != BlockedReason.DELIVERY:
            return None
        if state.blocked_message_id:
            self.ctx.store.archive_message(state.blocked_message_id)
        state.failure_history.append(reason)
        state.tasks_done = []
        state.blocked_reason = None
        state.blocked_message_id = None
        state.resume_stage = None
        state.resume_phase = None
        goto(state, "dev")
        save_state(self.ctx, state, "review_feedback")
        await runtime_for(self.ctx).inject_founder_answer(state)
        self.ctx.emit("story.reopened", story_id=story_id, stage=state.stage.value)
        return state

    async def restart_story(self, story_id: str, reason: str = "") -> StoryState:
        """The founder starts a story over inside its sprint: spec, plan, code and branch are
        discarded and it goes back to intake with only its request and the founder's notes.
        Needed when the factory itself changed (new templates, gates) and a half-done story
        would otherwise finish on artefacts the old version wrote."""
        if story_id in self.running:
            raise RuntimeError(f"{story_id} está rodando agora; pare a esteira antes")
        old = load_state(self.ctx, story_id)
        if old.stage in TERMINAL:
            raise ValueError(f"{story_id} já está encerrada")
        if old.blocked_message_id:
            self.ctx.store.archive_message(old.blocked_message_id)
        # A branch is only ever deleted by the Deployer (ADR-0008).
        self.ctx.worktrees.as_role("deployer").remove(story_id, delete_branch=True)
        specs = self.ctx.factory.paths.specs / story_id
        if specs.is_dir():
            shutil.rmtree(specs)
        state = StoryState(
            story_id=old.story_id,
            title=old.title,
            description=old.description,
            epic=old.epic,
            founder_notes=[*old.founder_notes, *([reason] if reason else [])],
        )
        goto(state, "intake")
        state.stage = Stage.SPEC  # like an admitted card: out of the backlog, intake classifies
        save_state(self.ctx, state, "restart")
        await runtime_for(self.ctx).inject_founder_answer(state)
        self.ctx.emit("story.restarted", story_id=story_id, from_stage=old.stage.value)
        return state

    async def withdraw_story(self, story_id: str, *, leave_sprint: bool = True) -> StoryState:
        """The founder takes a story out of the running sprint (ADR-0018): it waits in the
        backlog again, like "deixar para depois", and any question it had for the founder is
        withdrawn with it. Never while this engine is running it."""
        if story_id in self.running:
            raise RuntimeError(f"{story_id} está rodando agora; pare a esteira antes")
        state = load_state(self.ctx, story_id)
        if state.stage in TERMINAL:
            raise ValueError(f"{story_id} já está encerrada")
        if state.blocked_message_id:
            self.ctx.store.archive_message(state.blocked_message_id)
        back_to_backlog(self.ctx, state, leave_sprint=leave_sprint)
        save_state(self.ctx, state, "withdrawn")
        await runtime_for(self.ctx).inject_founder_answer(state)
        self.ctx.emit("story.withdrawn", story_id=story_id)
        return state

    def answer(self, message_id: str, answer: FounderAnswer) -> StoryState | None:
        """Sync facade for CLI code paths (no running event loop)."""
        return asyncio.run(self.aanswer(message_id, answer))
