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
import json
import logging
import re
import shutil
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

from loompa.agents.deployer import DeployerAgent
from loompa.agents.ops import INCIDENT_KEY, OpsAgent, StoryStalled, triage
from loompa.comms import FounderAnswer, FounderMessage, MessageKind
from loompa.company import ensure_templates
from loompa.dependencies import WAIT_KEY, in_chain, spec_passed, still_waiting
from loompa.engine.awake import Awake
from loompa.engine.context import EngineContext
from loompa.engine.graph import (
    PARK_KEY,
    BlockedReason,
    apply_founder_answer,
    back_to_backlog,
    block,
)
from loompa.engine.langgraph_engine import GraphRuntime
from loompa.engine.line import LineWatch
from loompa.engine.lock import DryRunOnLiveFactory, EngineLock
from loompa.engine.merge_queue import waits_its_turn
from loompa.engine.phases import goto
from loompa.engine.state import PAUSED, TERMINAL, Stage, StoryState
from loompa.factory import ensure_gitignore
from loompa.finance import BudgetStatus
from loompa.finance.story_cap import story_spent
from loompa.growth import on_answer as growth_answer
from loompa.growth import redo_pending as growth_redo
from loompa.lifecycle import Lifecycle
from loompa.llm import EnginePaused
from loompa.memory.sources import answer_document
from loompa.models_sync import ModelSync
from loompa.store import now_iso

log = logging.getLogger("loompa.scheduler")
SIDE_TASKS: set[asyncio.Task] = set()  # work an answer started that outlives the reply

# The wall clock ran this far ahead of the monotonic one between two ticks: the machine slept.
SLEPT_S = 60.0
# ...and a sleep this long, with stories running, is worth one note to the founder.
SLEEP_NOTE_S = 300.0
SLEEP_TITLE = "A fábrica ficou parada enquanto o computador dormia"


def _sleep_counts(context: str) -> tuple[float, int]:
    """Minutes and times a pending sleep note already counts (read back from its own text)."""
    times = re.search(r"(\d+) vezes", context)
    minutes = re.search(r"cerca de (\d+) minutos", context)
    return (float(minutes.group(1)) if minutes else 0.0, int(times.group(1)) if times else 1)


def _sleep_context(minutes: float, times: int) -> str:
    when = (
        f"por cerca de {minutes:.0f} minutos"
        if times == 1
        else f"{times} vezes, somando cerca de {minutes:.0f} minutos"
    )
    return (
        f"O computador entrou em repouso {when} e as entregas em andamento pararam nesse tempo. "
        "Elas retomaram sozinhas quando ele voltou."
    )


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
        runtime = runtime_for(self.ctx)
        if state.extra.pop(WAIT_KEY, None) is not None:
            # parked before `plan` and the chain is ready now (ADR-0021): resume at `plan` from
            # the checkpoint, the way a founder answer resumes a paused story
            save_state(self.ctx, state, "gate_open")
            await runtime.inject_founder_answer(state)
        return await runtime.run_story(state)


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
    # the founder's pause (12.x): nothing new is dispatched and no model call starts; the calls in
    # flight finish and each story stops right after, resuming later where it was. `pause_reason`
    # is "founder" or "budget" (a budget pause lifts itself when the budget allows again).
    pausing: bool = False
    pause_reason: str = ""
    _paused_said: bool = False
    started: dict[str, float] = field(default_factory=dict)  # story -> wall time dispatched
    _line: asyncio.Task | None = None  # the base's suite running after the base moved (§4)

    def pause(self, reason: str = "founder") -> None:
        if self.pausing:
            return
        self.pausing, self.pause_reason, self._paused_said = True, reason, False
        self.ctx.router.holding = True
        self.ctx.emit("engine.pausing", reason=reason, running=sorted(self.running))

    def resume(self) -> None:
        if not self.pausing:
            return
        self.pausing, self.pause_reason = False, ""
        self.ctx.router.holding = False
        self.ctx.emit("engine.resumed")

    @property
    def state(self) -> str:
        """running | pausing (calls in flight still finishing) | paused"""
        if not self.pausing:
            return "running"
        return "pausing" if self.running else "paused"

    def in_flight(self) -> list[dict[str, Any]]:
        """What a stop would interrupt: each running story, since when, and what it last did."""
        now = time.time()
        return [
            {
                "story": sid,
                "minutes": round((now - self.started.get(sid, now)) / 60, 1),
                "doing": self.last_event.get(sid, ""),
            }
            for sid in sorted(self.running)
        ]

    @property
    def slots(self) -> int:
        return self.max_parallel or self.ctx.config.schedule.max_parallel

    def _eligible(self) -> list[dict[str, Any]]:
        """Admitted stories not running now. A story parked before `plan` (ADR-0021) is left out
        while its chain still says wait; among the rest, chain stories whose spec is not approved
        go first, so the whole chain leaves the spec phase quickly; then the backlog order."""
        rows = {s["id"]: s for s in self.ctx.store.list_stories(self.ctx.slug)}
        out = [
            s
            for s in rows.values()
            if s["stage"] not in TERMINAL
            and s["stage"] not in PAUSED
            and s["id"] not in self.running
            and s["stage"] != Stage.BACKLOG  # cards wait for a sprint
            and not still_waiting(rows, s)  # parked before `plan` or before `review` (11.5)
            and not waits_its_turn(rows, s["id"], self.running)  # the merge queue, head first
        ]
        return sorted(out, key=lambda s: 0 if in_chain(rows, s["id"]) and not spec_passed(s) else 1)

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

    def _watch_line(self) -> None:
        """ADR-0036 §4: once the base moved, its suite runs once, beside the dispatch loop (a
        suite can take minutes; the stories in flight go on meanwhile)."""
        if self._line is not None:
            if not self._line.done():
                return
            if not self._line.cancelled() and (exc := self._line.exception()) is not None:
                log.warning("checking the base's suite failed: %s", exc)
            self._line = None
        watch = LineWatch(self.ctx)
        if watch.due():
            self._line = asyncio.create_task(watch.check(), name="line")

    async def close_sprints(self) -> None:
        """Close sprints whose stories all finished (the Master reports to the founder)."""
        from loompa.agents.master import MasterAgent

        closed = await MasterAgent(self.ctx).close_finished_sprints()
        ModelSync(self.ctx).apply_deferred()
        if closed:
            await self._review_phase()

    async def _review_phase(self) -> None:
        """Once per closed sprint, the product manager reads the phase's exit criterion against
        the founder's facts; when it holds, the founder is asked about the move (16.3)."""
        from loompa.agents.product_manager import ProductManagerAgent

        lc = Lifecycle(self.ctx)
        cur = lc.current()
        if cur is None or not cur["exit_criterion"] or lc.next_phase() is None:
            return
        try:
            review = await ProductManagerAgent(self.ctx).review_phase()
        except Exception:  # noqa: BLE001 - a review is advice; the line never waits on it
            log.exception("phase review failed")
            return
        self.ctx.emit("lifecycle.reviewed", phase=cur["key"], exit_met=review["exit_met"])
        if review["exit_met"] == "yes":
            lc.propose_move(
                review["evidence"], review["missing"]
            )  # an approved model swap waits for the work to end

    def budget_ok(self) -> BudgetStatus:
        st = self.ctx.tracker.status()
        if st.warn:
            self.ctx.tracker.maybe_alert()
        return st

    def _dispatch(self) -> int:
        st = self.budget_ok()
        if st.exhausted:
            if not self.pausing:  # the same pause as the founder's: in-flight calls finish first
                self.ctx.emit(
                    "scheduler.paused",
                    reason="budget_exhausted",
                    period=st.period,
                    period_cost_usd=st.period_cost_usd,
                )
                self.pause("budget")
            return 0
        if self.pause_reason == "budget":
            self.resume()  # the founder raised the cap
        if self.pausing:
            if not self.running and not self._paused_said:
                self._paused_said = True
                self.ctx.emit("engine.paused", reason=self.pause_reason)
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
            self.started[story["id"]] = time.time()
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
            self.started.pop(sid, None)
            exc = task.exception()
            if isinstance(exc, EnginePaused):
                # paused between two model calls: nothing failed. It resumes at its last
                # checkpoint; what it wrote is in its worktree and its finished tasks are marked
                self.ctx.emit("story.paused", story_id=sid, after=last)
                continue
            if exc is None:
                self.crashes.pop(sid, None)
                self.not_before.pop(sid, None)
                if sid not in self.completed:
                    self.completed.append(sid)
                await self._park(sid)
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
        self.ctx.woke_at = time.monotonic()  # the network may still be down: see OpsAgent
        self.ctx.emit("engine.slept", minutes=round(slept / 60, 1), running=running)
        if not running or slept < SLEEP_NOTE_S:
            return
        # one note while the founder has not read it, with the minutes added up: a Mac that
        # dark-wakes for a moment and sleeps again sent four of these in an hour (tamagotchi-retro)
        pending = next(
            (
                m
                for m in self.ctx.store.list_messages(self.ctx.slug, "pending")
                if m.title == SLEEP_TITLE
            ),
            None,
        )
        if pending is not None:
            before_min, before_times = _sleep_counts(pending.context)
            pending.context = _sleep_context(before_min + slept / 60, before_times + 1)
            self.ctx.store.put_message(pending)
            return
        self.ctx.inbox(
            FounderMessage(
                factory=self.ctx.slug,
                kind=MessageKind.INFO,
                sender=OpsAgent.display,
                title=SLEEP_TITLE,
                context=_sleep_context(slept / 60, 1),
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
        Raises `EngineBusy` when another engine already runs this factory, and
        `DryRunOnLiveFactory` when a simulated engine would write into a factory with real work
        (its stories, checkpoints and costs are shared with the real engine)."""
        if self.ctx.dry_run and self.ctx.store.has_live_usage(self.ctx.slug):
            raise DryRunOnLiveFactory()
        cycles = 0
        lock = EngineLock(self.ctx.factory.paths.loompa / "engine.lock")
        lock.acquire()
        ensure_gitignore(self.ctx.root)  # older factories get the newer lines (plan 8.6.6)
        ensure_templates(self.ctx.factory.paths.loompa)  # and the company templates (13.2)
        if not self.ctx.dry_run:
            DeployerAgent(self.ctx).record_company()  # the founder's edits go into git
        self.ctx.listeners.append(self._heard)
        for row in self.ctx.store.list_stories(self.ctx.slug):
            # a run that asked to wait in the backlog and stopped before it could (§2)
            if row["stage"] == Stage.AWAITING_FOUNDER and (row.get("state") or {}).get(
                "extra", {}
            ).get(PARK_KEY):
                await self._park(row["id"])
        awake = Awake(enabled=None if not self.ctx.dry_run else False)
        try:
            while True:
                self._watch()
                self._dispatch()
                if not self.pausing:
                    self._watch_line()
                awake.hold(bool(self.running))
                await self.close_sprints()
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
            if self._line is not None and not self._line.done():
                self._line.cancel()  # the next run checks the same base again
                await asyncio.gather(self._line, return_exceptions=True)
            for task in self.running.values():
                task.cancel()
            if self.running:
                await asyncio.gather(*self.running.values(), return_exceptions=True)
            self.running.clear()
            awake.release()
            if self._heard in self.ctx.listeners:
                self.ctx.listeners.remove(self._heard)
            lock.release()
        return self.completed

    # --------------------------------------------------------------- founder
    async def aanswer(self, message_id: str, answer: FounderAnswer) -> StoryState | None:
        """Apply an inbox reply; returns the updated story state (None for non-story messages)."""
        state = await self._apply_answer(message_id, answer)
        await self.close_sprints()  # an approval or a cancel may have finished the sprint
        return state

    def _remember_answer(self, msg: FounderMessage) -> None:
        """The answer is a precedent from now on, not from the next `loompa run` (plan 13.3)."""
        doc = answer_document(msg)
        if doc is None:
            return
        doc_id, kind, title, text = doc
        try:
            self.ctx.memory.upsert_document(doc_id, text, kind=kind, title=title)
        except Exception:  # noqa: BLE001 - memory is best-effort
            log.exception("could not index the answer to %s", msg.id)

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
            if po.resolve_finding(decision.id, choice):  # e.g. the sprint ended meanwhile
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
        self._remember_answer(msg)
        if msg.kind.value == "finance" and answer.option_key in ("raise_10", "raise_30"):
            weekly = self.ctx.config.budget.period == "weekly"
            small, large = (5.0, 10.0) if weekly else (10.0, 30.0)
            self.ctx.config.budget.cap_usd += small if answer.option_key == "raise_10" else large
            self.ctx.factory.save()
        ModelSync(self.ctx).on_answer(msg, answer)
        Lifecycle(self.ctx).on_answer(msg, answer)  # phases, epics, discovery (plan 16)
        if (piece := growth_answer(self.ctx, msg, answer)) and piece["status"] == "adjusting":
            # a new round calls models: it runs beside the reply, which returns at once
            task = asyncio.create_task(growth_redo(self.ctx))
            SIDE_TASKS.add(task)  # held here: a Scheduler made for one reply does not outlive it
            task.add_done_callback(SIDE_TASKS.discard)
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

    def has_run(self, story_id: str) -> bool:
        """A model already worked on the story or it already cost something: its history and
        cost are data for analysis, and a reset would cut them (the founder's rule, 12.1)."""
        calls = self.ctx.store.usage_totals(self.ctx.slug, story_id=story_id).get("calls") or 0
        return bool(calls) or story_spent(self.ctx, story_id) > 0

    async def restart_story(
        self, story_id: str, reason: str = "", *, keep_notes: bool = False
    ) -> StoryState:
        """The founder starts a story over. The rule for anything reset to its start: when a
        model already ran on it or it cost something, the original is cancelled with its outcome
        recorded and keeps its history, cost, spec and branch for analysis, and a fresh copy
        (S-061 → S-061B) takes its place in the sprint and starts at intake; when nothing ran,
        the same id is reset in place (worktree, branch and spec discarded). Returns the state
        of the story that goes on."""
        from loompa.agents.product_owner import ProductOwnerAgent

        if story_id in self.running:
            raise RuntimeError(f"{story_id} está rodando agora; pare a esteira antes")
        old = load_state(self.ctx, story_id)
        if old.stage in TERMINAL:
            raise ValueError(f"{story_id} já está encerrada")
        if old.blocked_message_id:
            self.ctx.store.archive_message(old.blocked_message_id)
        if self.has_run(story_id):
            # the worktree goes (the copy gets its own); the branch stays for analysis
            self.ctx.worktrees.as_role("deployer").remove(story_id, delete_branch=False)
            new_id = ProductOwnerAgent(self.ctx).restart_as_copy(
                story_id, reason, keep_notes=keep_notes
            )
            return load_state(self.ctx, new_id)
        # nothing ran: reuse the id, back to the start (a branch only the Deployer deletes)
        self.ctx.worktrees.as_role("deployer").remove(story_id, delete_branch=True)
        specs = self.ctx.factory.paths.specs / story_id
        if specs.is_dir():
            shutil.rmtree(specs)
        state = StoryState(
            story_id=old.story_id,
            title=old.title,
            description=old.description,
            epic=old.epic,
            founder_notes=[
                *(old.founder_notes if keep_notes else []),
                *([reason] if reason else []),
            ],
        )
        state.kind = old.kind
        goto(state, "intake")
        state.stage = Stage.SPEC  # like an admitted card: out of the backlog, intake classifies
        save_state(self.ctx, state, "restart")
        await runtime_for(self.ctx).inject_founder_answer(state)
        self.ctx.emit(
            "story.restarted", story_id=story_id, from_stage=old.stage.value, as_id=story_id
        )
        return state

    async def restart_sprint(self, sprint_id: str, reason: str = "") -> str:
        """The founder starts a sprint over, by the same rule as a story: when any of its stories
        already ran a model or cost something, the sprint is cancelled with its outcome recorded
        (kv `sprint_outcome:<id>`) and keeps what it did, and a new sprint (SP-009 → SP-009B)
        starts with each unfinished story restarted by its own rule; finished stories stay
        delivered in the original. When nothing ran, the same sprint goes on with its stories
        reset in place. Returns the id of the sprint that runs."""
        from loompa.sprints import SprintBoard, SprintError, SprintStatus

        board = SprintBoard(self.ctx.store, self.ctx.slug)
        sprint = board.get(sprint_id)
        if sprint is None or sprint.status != SprintStatus.RUNNING:
            raise SprintError(f"{sprint_id} não está em andamento")
        busy = [sid for sid in sprint.story_ids if sid in self.running]
        if busy:
            raise RuntimeError(f"{', '.join(busy)} está rodando agora; pare a esteira antes")
        unfinished = [
            sid
            for sid in sprint.story_ids
            if (row := self.ctx.store.get_story(sid)) and row["stage"] not in TERMINAL
        ]
        if not any(self.has_run(sid) for sid in sprint.story_ids):
            for sid in unfinished:
                await self.restart_story(sid, reason)
            self.ctx.emit("sprint.restarted", sprint_id=sprint_id, as_id=sprint_id)
            return sprint_id
        new_id = self.ctx.store.next_restart_id(sprint_id, table="sprints")
        board.close(sprint_id, cancelled=True)
        self.ctx.store.set(
            f"sprint_outcome:{sprint_id}",
            json.dumps({"kind": "restarted", "as": new_id, "reason": reason, "at": now_iso()}),
        )
        members = [(await self.restart_story(sid, reason)).story_id for sid in unfinished]
        fresh = sprint.model_copy(
            update={
                "id": new_id,
                "status": SprintStatus.OPEN,
                "story_ids": members,
                "created_at": now_iso(),
                "started_at": None,
                "closed_at": None,
            }
        )
        self.ctx.store.put_sprint(fresh.model_dump())
        board.start(new_id, sprint.goal)
        self.ctx.emit("sprint.restarted", sprint_id=sprint_id, as_id=new_id, stories=members)
        return new_id

    async def _park(self, story_id: str) -> None:
        """A run that ended asking for its story to wait in the backlog (`PARK_KEY`, ADR-0037
        §2): done here, once the graph has stopped, the way `withdraw_story` does it."""
        state = load_state(self.ctx, story_id)
        why = state.extra.pop(PARK_KEY, None)
        if not why or state.stage in TERMINAL:
            return
        back_to_backlog(self.ctx, state)
        save_state(self.ctx, state, "parked")
        await runtime_for(self.ctx).inject_founder_answer(state)
        self.ctx.emit("story.parked", story_id=story_id, why=why)

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
