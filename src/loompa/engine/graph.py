"""The story graph: nodes per stage, escalation ladder, blocking and founder resume.

    BACKLOG -> SPEC -> PLAN -> DEV -> TEST -> REVIEW -> AWAITING_FOUNDER -> DONE
                 |        |      ^      |                       |
                 v        v      |      v (fail: tier2 x2, tier1 x1)      v (approve/changes)
           AWAITING_FOUNDER      +------+-- AWAITING_FOUNDER (persistent failure)

Every node is `async (ctx, state) -> state`; the runner checkpoints after each one.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path

from loompa.agents import (
    ArchitectAgent,
    DeployerAgent,
    InspectorAgent,
    KaizenAgent,
    MasterAgent,
    ProductAgent,
    WorkerAgent,
)
from loompa.comms import FounderAnswer, FounderMessage, MessageKind, Option
from loompa.engine.context import EngineContext
from loompa.engine.state import BlockedReason, Stage, StoryState
from loompa.worktrees import GitError, Worktree

Node = Callable[[EngineContext, StoryState], Awaitable[StoryState]]


def _write_tech_log(ctx: EngineContext, state: StoryState, text: str) -> str:
    logs = ctx.factory.paths.logs
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"{state.story_id}.log"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(f"\n===== {state.stage} =====\n{text}\n")
    return str(Path(".loompa/logs") / path.name)


async def block(
    ctx: EngineContext,
    state: StoryState,
    reason: BlockedReason,
    technical: str,
    *,
    options: list[str] | None = None,
    resume: Stage | None = None,
    executive: str | None = None,
) -> StoryState:
    """Isolate the story: write an executive inbox message and pause. Never blocks the line."""
    ref = _write_tech_log(ctx, state, technical)
    master = MasterAgent(ctx)
    if reason == BlockedReason.CONFLICT:
        msg = FounderMessage(
            factory=ctx.slug,
            story_id=state.story_id,
            kind=MessageKind.BLOCKED,
            sender=master.name,
            title=f"“{state.title}” conflita com mudanças recentes na versão principal",
            context="Outra entrega alterou as mesmas partes do produto. Preciso de uma orientação para integrar com segurança.",
            impact="A entrega está pronta, apenas aguardando integração. As demais frentes seguem normalmente.",
            options=[
                Option(key="retry", label="Refazer a integração automaticamente", recommended=True),
                Option(key="skip", label="Deixar para depois"),
                Option(key="drop", label="Cancelar esta entrega"),
            ],
            technical_ref=ref,
        )
        ctx.inbox(msg)
    else:
        msg = await master.blocked_message(
            state, technical, options=options, technical_ref=ref, executive=executive
        )
        ctx.inbox(msg)
    state.blocked_reason = reason
    state.blocked_message_id = msg.id
    state.resume_stage = resume or state.stage
    state.stage = Stage.AWAITING_FOUNDER
    ctx.emit("story.blocked", story_id=state.story_id, reason=reason.value, message_id=msg.id)
    return state


# ------------------------------------------------------------------------------ nodes


async def node_intake(ctx: EngineContext, state: StoryState) -> StoryState:
    state.stage = Stage.SPEC
    return state


async def node_spec(ctx: EngineContext, state: StoryState) -> StoryState:
    res = await ProductAgent(ctx).run(state)
    if res.blocked_reason:
        return await block(
            ctx,
            state,
            BlockedReason.QUESTION,
            res.blocked_reason,
            options=res.blocked_options,
            resume=Stage.SPEC,
        )
    state.stage = Stage.PLAN
    return state


async def node_plan(ctx: EngineContext, state: StoryState) -> StoryState:
    await ArchitectAgent(ctx).run(state)
    state.stage = Stage.DEV
    return state


def _ensure_worktree(ctx: EngineContext, state: StoryState) -> Worktree:
    wt = ctx.worktrees.get(state.story_id) or ctx.worktrees.create(
        state.story_id, title=state.title
    )
    state.worktree = str(wt.path)
    state.branch = wt.branch
    return wt


async def node_dev(ctx: EngineContext, state: StoryState) -> StoryState:
    try:
        wt = _ensure_worktree(ctx, state)
    except GitError as exc:
        return await block(
            ctx,
            state,
            BlockedReason.PERSISTENT_FAILURE,
            f"Não foi possível preparar o ambiente isolado: {exc}",
            resume=Stage.DEV,
        )
    if "baseline" not in state.extra:
        state.extra["baseline"] = await InspectorAgent(ctx).baseline(state, wt)
    tier = "tier1" if state.current_tier == "tier1" else None
    worker = WorkerAgent(
        ctx, name=f"Worker Loompa {'Sr' if tier else ''}".strip(), tier_override=tier
    )
    res = await worker.run(state, wt)
    KaizenAgent(ctx).capture(state)
    if res.blocked_reason:
        return await block(
            ctx,
            state,
            BlockedReason.QUESTION,
            res.blocked_reason,
            options=res.blocked_options,
            resume=Stage.DEV,
        )
    state.stage = Stage.TEST
    return state


async def node_test(ctx: EngineContext, state: StoryState) -> StoryState:
    wt = _ensure_worktree(ctx, state)
    res = await InspectorAgent(ctx).run(state, wt)
    if res.ok:
        if state.failure_history and state.current_tier == "tier1":
            # fixed after escalation: record the resolution and harden the constitution
            KaizenAgent(ctx).record_resolution(
                state, state.failure_history[-1], state.worker_summary
            )
            await ArchitectAgent(ctx).incorporate_lesson(
                state, state.failure_history[-1], state.worker_summary
            )
        state.stage = Stage.REVIEW
        return state
    state.failure_history.append(res.summary)
    sched = ctx.config.schedule
    if state.current_tier == "tier2":
        state.attempts_tier2 += 1
        if state.attempts_tier2 < sched.tier2_max_attempts:
            ctx.emit(
                "story.retry", story_id=state.story_id, tier="tier2", attempt=state.attempts_tier2
            )
            state.stage = Stage.DEV
            return state
        state.current_tier = "tier1"
        ctx.emit(
            "story.escalated",
            story_id=state.story_id,
            to_tier="tier1",
            after_attempts=state.attempts_tier2,
        )
        state.stage = Stage.DEV
        return state
    state.attempts_tier1 += 1
    if state.attempts_tier1 < sched.tier1_max_attempts:
        ctx.emit("story.retry", story_id=state.story_id, tier="tier1", attempt=state.attempts_tier1)
        state.stage = Stage.DEV
        return state
    executive = (
        "As verificações automáticas continuam falhando após várias tentativas, inclusive com o especialista sênior. "
        "O detalhe técnico ficou registrado para a equipe."
    )
    return await block(
        ctx,
        state,
        BlockedReason.PERSISTENT_FAILURE,
        res.summary,
        resume=Stage.DEV,
        executive=executive,
    )


async def node_review(ctx: EngineContext, state: StoryState) -> StoryState:
    wt = _ensure_worktree(ctx, state)
    res = await DeployerAgent(ctx).run(state, wt)
    if not res.ok:
        return await block(ctx, state, BlockedReason.CONFLICT, res.summary, resume=Stage.REVIEW)
    state.blocked_reason = BlockedReason.DELIVERY
    state.resume_stage = Stage.REVIEW
    state.stage = Stage.AWAITING_FOUNDER
    return state


NODES: dict[Stage, Node] = {
    Stage.BACKLOG: node_intake,
    Stage.SPEC: node_spec,
    Stage.PLAN: node_plan,
    Stage.DEV: node_dev,
    Stage.TEST: node_test,
    Stage.REVIEW: node_review,
}


# ----------------------------------------------------------------------------- resume


def apply_founder_answer(
    ctx: EngineContext, state: StoryState, msg: FounderMessage, answer: FounderAnswer
) -> StoryState:
    """Translate an inbox reply into the next stage. Pure state logic, no LLM."""
    key = (answer.option_key or "").lower()
    label = next((o.label for o in msg.options if o.key == answer.option_key), None)
    guidance = " / ".join(x for x in (label, answer.text) if x)
    reason = state.blocked_reason
    state.blocked_message_id = None
    if key == "drop":
        state.stage = Stage.CANCELLED
        ctx.worktrees.remove(state.story_id)
    elif key == "skip":
        state.stage = Stage.BACKLOG
        state.resume_stage = None
        ctx.store.update_story(state.story_id, priority=900)
    elif reason == BlockedReason.DELIVERY:
        if key in ("approve", "approved", "ok", "yes", "sim"):
            wt = ctx.worktrees.get(state.story_id)
            if wt is not None:
                try:
                    DeployerAgent(ctx).merge(state, wt)
                except GitError as exc:
                    state.note(f"Aprovado, mas a integração falhou: {exc}")
                    state.stage = Stage.REVIEW
                    state.blocked_reason = None
                    return state
            state.stage = Stage.DONE
        else:
            state.note(guidance or "Founder pediu ajustes na entrega.")
            state.stage = Stage.DEV
            state.tasks_done = []
            state.failure_history.append("Founder pediu ajustes: " + (guidance or "(sem detalhes)"))
    elif reason == BlockedReason.PERSISTENT_FAILURE:
        if guidance and key not in ("retry",):
            state.note(guidance)
        state.attempts_tier2 = 0
        state.attempts_tier1 = 0
        state.current_tier = "tier2"
        state.stage = state.resume_stage or Stage.DEV
    elif reason == BlockedReason.CONFLICT:
        state.stage = Stage.REVIEW
    else:  # QUESTION
        if guidance:
            state.note(guidance)
        state.stage = state.resume_stage or Stage.SPEC
    state.blocked_reason = None
    state.resume_stage = None
    ctx.emit(
        "story.resumed", story_id=state.story_id, stage=state.stage.value, answer=key or "text"
    )
    return state
