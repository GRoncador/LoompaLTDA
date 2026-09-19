"""The story phases: intake, spec, spec_review, plan, dev, test, review (ADR-0006).

    intake -> spec -> spec_review -> plan -> dev -> test -> review -> AWAITING_FOUNDER -> DONE
                |        | (rewrite)  |       ^      |                     |
                v        +------------+       |      v (fail: tier2 x2, tier1 x1; WAIVED)
          AWAITING_FOUNDER (question)         +------+-- AWAITING_FOUNDER

Every node is `async (ctx, state) -> state` and ends with `advance()` or `goto(phase)`; the
runner checkpoints after each one. Which phases a story visits is its `route`, built at intake.
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
    OpenCodeWorker,
    ProductAgent,
    ProductOwnerAgent,
    WorkerAgent,
)
from loompa.comms import FounderAnswer, FounderMessage, MessageKind, Option
from loompa.engine.context import EngineContext
from loompa.engine.phases import Phase, advance, build_route, goto, phase_stage, register
from loompa.engine.state import BlockedReason, QAVerdict, Stage, StoryState
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
    resume: str | Stage | None = None,
    executive: str | None = None,
    message: FounderMessage | None = None,
) -> StoryState:
    """Isolate the story: write an executive inbox message and pause. Never blocks the line.
    `resume` is the phase to continue from after the founder answers (defaults to the current)."""
    ref = _write_tech_log(ctx, state, technical)
    master = MasterAgent(ctx)
    if message is not None:
        message.technical_ref = message.technical_ref or ref
        msg = ctx.inbox(message)
    elif reason == BlockedReason.CONFLICT:
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
    if isinstance(resume, Stage):  # legacy callers
        resume = {v: k for k, v in _STAGE_OF_PHASE.items()}.get(resume, state.phase)
    state.resume_phase = resume or state.phase
    state.resume_stage = phase_stage(state.resume_phase) if state.resume_phase else state.stage
    state.stage = Stage.AWAITING_FOUNDER
    ctx.emit("story.blocked", story_id=state.story_id, reason=reason.value, message_id=msg.id)
    return state


_STAGE_OF_PHASE = {
    "intake": Stage.BACKLOG,
    "spec": Stage.SPEC,
    "spec_review": Stage.SPEC,
    "plan": Stage.PLAN,
    "dev": Stage.DEV,
    "test": Stage.TEST,
    "review": Stage.REVIEW,
}


# ------------------------------------------------------------------------------ nodes


async def node_intake(ctx: EngineContext, state: StoryState) -> StoryState:
    """Classify the request (kind, complexity) and build its route. Requests too big for one
    story become an epic whose child stories run independently."""
    master = MasterAgent(ctx)
    verdict = await master.classify(state)
    state.kind = verdict.kind
    state.complexity = verdict.complexity
    if len(verdict.children) > 1:
        ids = master.split_epic(state, verdict.children)
        state.delivery_summary = f"Desmembrada em {len(ids)} histórias: {', '.join(ids)}"
        state.extra["children"] = ids
        state.route = ["intake"]
        state.stage = Stage.DONE
        state.phase = ""
        ctx.emit("story.split", story_id=state.story_id, children=ids)
        return state
    state.route = build_route(state.kind, state.complexity)
    ctx.emit(
        "story.classified",
        story_id=state.story_id,
        kind=state.kind.value,
        complexity=state.complexity.value,
        route=state.route,
    )
    return goto(state, state.next_phase("intake") or "spec")


async def node_spec(ctx: EngineContext, state: StoryState) -> StoryState:
    res = await ProductAgent(ctx).run(state)
    if res.blocked_reason:
        return await block(
            ctx,
            state,
            BlockedReason.QUESTION,
            res.blocked_reason,
            options=res.blocked_options,
            resume="spec",
        )
    return advance(state)


async def node_spec_review(ctx: EngineContext, state: StoryState) -> StoryState:
    """Product Owner reviews the spec ("No Invention" gate). One rewrite round at most; after
    that the concerns travel with the story instead of holding the line."""
    review = await ProductOwnerAgent(ctx).review_spec(state)
    state.spec_review_rounds += 1
    if not review.ok and state.spec_review_rounds < 2:
        state.hand_off(review.summary, phase="spec_review")
        ctx.emit("spec.rejected", story_id=state.story_id, issues=review.summary[:500])
        return goto(state, "spec")
    if not review.ok:
        state.review_notes = (state.review_notes + "\n" + review.summary).strip()
        state.learnings.append(
            {
                "kind": "opportunity",
                "title": "Spec seguiu com ressalvas do Product Owner",
                "detail": review.summary[:500],
            }
        )
    ctx.emit("spec.approved", story_id=state.story_id, rounds=state.spec_review_rounds)
    return advance(state)


async def node_plan(ctx: EngineContext, state: StoryState) -> StoryState:
    await ArchitectAgent(ctx).run(state)
    return advance(state)


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
            resume="dev",
        )
    if "baseline" not in state.extra:
        state.extra["baseline"] = await InspectorAgent(ctx).baseline(state, wt)
    tier = "tier1" if state.current_tier == "tier1" else None
    opencode = ctx.config.worker.backend == "opencode"
    worker_cls = OpenCodeWorker if opencode else WorkerAgent
    label = " ".join(
        p for p in ("Worker Loompa", "Sr" if tier else "", "(OpenCode)" if opencode else "") if p
    )
    worker = worker_cls(ctx, name=label, tier_override=tier)
    res = await worker.run(state, wt)
    KaizenAgent(ctx).capture(state)
    if res.blocked_reason:
        return await block(
            ctx,
            state,
            BlockedReason.QUESTION,
            res.blocked_reason,
            options=res.blocked_options,
            resume="dev",
        )
    return advance(state)


async def node_test(ctx: EngineContext, state: StoryState) -> StoryState:
    """Graded quality gate: PASS / CONCERNS move on, FAIL climbs the escalation ladder,
    WAIVED asks the founder (serious finding the tests do not catch)."""
    wt = _ensure_worktree(ctx, state)
    res = await InspectorAgent(ctx).run(state, wt)
    verdict = QAVerdict((res.data or {}).get("verdict") or ("PASS" if res.ok else "FAIL"))
    findings = list((res.data or {}).get("findings") or [])
    state.qa_verdict = verdict
    state.qa_findings = findings
    if verdict in (QAVerdict.PASS, QAVerdict.CONCERNS):
        if verdict == QAVerdict.CONCERNS and findings:
            KaizenAgent(ctx).capture(
                state,
                items=[
                    {
                        "kind": "opportunity",
                        "title": f"{f.get('id', 'QA')}: {str(f.get('text', ''))[:100]}",
                        "detail": f"Ressalva do Inspector ({f.get('severity', 'low')}) em {state.story_id}: {f.get('text', '')}",
                    }
                    for f in findings
                ],
            )
        if state.failure_history and state.current_tier == "tier1":
            # fixed after escalation: record the resolution and harden the constitution
            KaizenAgent(ctx).record_resolution(
                state, state.failure_history[-1], state.worker_summary
            )
            await ArchitectAgent(ctx).incorporate_lesson(
                state, state.failure_history[-1], state.worker_summary
            )
        return advance(state)
    if verdict == QAVerdict.WAIVED:
        serious = [f for f in findings if f.get("severity") == "high"] or findings
        detail = "; ".join(str(f.get("text", ""))[:160] for f in serious[:3])
        msg = FounderMessage(
            factory=ctx.slug,
            story_id=state.story_id,
            kind=MessageKind.DECISION,
            sender="Inspector Loompa",
            title=f"“{state.title}” passou nos testes, mas o Inspector viu um risco sério",
            context=f"O código funciona e os testes estão verdes, porém a revisão de qualidade apontou: {detail}.",
            impact="Posso aceitar o risco e entregar assim, ou devolver para a equipe corrigir antes de seguir.",
            options=[
                Option(key="fix", label="Corrigir antes de entregar", recommended=True),
                Option(key="waive", label="Aceitar o risco e entregar assim"),
                Option(key="drop", label="Cancelar esta entrega"),
            ],
        )
        state.hand_off("Corrigir os apontamentos do Inspector: " + detail, phase="test")
        return await block(ctx, state, BlockedReason.WAIVER, res.summary, resume="dev", message=msg)
    state.failure_history.append(res.summary)
    sched = ctx.config.schedule
    if state.current_tier == "tier2":
        state.attempts_tier2 += 1
        if state.attempts_tier2 < sched.tier2_max_attempts:
            ctx.emit(
                "story.retry", story_id=state.story_id, tier="tier2", attempt=state.attempts_tier2
            )
            return goto(state, "dev")
        state.current_tier = "tier1"
        ctx.emit(
            "story.escalated",
            story_id=state.story_id,
            to_tier="tier1",
            after_attempts=state.attempts_tier2,
        )
        return goto(state, "dev")
    state.attempts_tier1 += 1
    if state.attempts_tier1 < sched.tier1_max_attempts:
        ctx.emit("story.retry", story_id=state.story_id, tier="tier1", attempt=state.attempts_tier1)
        return goto(state, "dev")
    executive = (
        "As verificações automáticas continuam falhando após várias tentativas, inclusive com o especialista sênior. "
        "O detalhe técnico ficou registrado para a equipe."
    )
    return await block(
        ctx,
        state,
        BlockedReason.PERSISTENT_FAILURE,
        res.summary,
        resume="dev",
        executive=executive,
    )


async def node_review(ctx: EngineContext, state: StoryState) -> StoryState:
    wt = _ensure_worktree(ctx, state)
    res = await DeployerAgent(ctx).run(state, wt)
    if not res.ok:
        return await block(ctx, state, BlockedReason.CONFLICT, res.summary, resume="review")
    state.blocked_reason = BlockedReason.DELIVERY
    state.resume_stage = Stage.REVIEW
    state.resume_phase = "review"
    state.stage = Stage.AWAITING_FOUNDER
    return state


# --------------------------------------------------------------------- phase registry

register(
    Phase("intake", node_intake, Stage.BACKLOG, owner="master", description="classify, build route")
)
register(Phase("spec", node_spec, Stage.SPEC, owner="product", reviewer="product_owner"))
register(
    Phase(
        "spec_review",
        node_spec_review,
        Stage.SPEC,
        owner="product_owner",
        description="No Invention gate",
    )
)
register(Phase("plan", node_plan, Stage.PLAN, owner="architect", reviewer="product_owner"))
register(Phase("dev", node_dev, Stage.DEV, owner="worker", description="DoD checklist per task"))
register(Phase("test", node_test, Stage.TEST, owner="inspector", description="graded QA gate"))
register(Phase("review", node_review, Stage.REVIEW, owner="deployer", reviewer="founder"))

NODES: dict[Stage, Node] = {  # legacy view kept for callers that index by stage
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
    resume = state.resume_phase or (
        {v: k for k, v in _STAGE_OF_PHASE.items()}.get(state.resume_stage)
        if state.resume_stage
        else None
    )
    if key == "drop":
        state.stage = Stage.CANCELLED
        state.phase = ""
        ctx.worktrees.remove(state.story_id)
    elif key == "skip":
        goto(state, "intake" if not state.route else state.route[0])
        state.stage = Stage.BACKLOG
        ctx.store.update_story(state.story_id, priority=900)
    elif reason == BlockedReason.DELIVERY:
        if key in ("approve", "approved", "ok", "yes", "sim"):
            wt = ctx.worktrees.get(state.story_id)
            if wt is not None:
                try:
                    DeployerAgent(ctx).merge(state, wt)
                except GitError as exc:
                    state.note(f"Aprovado, mas a integração falhou: {exc}")
                    goto(state, "review")
                    state.blocked_reason = None
                    return state
            state.stage = Stage.DONE
            state.phase = ""
        else:
            state.note(guidance or "Founder pediu ajustes na entrega.")
            goto(state, "dev")
            state.tasks_done = []
            state.failure_history.append("Founder pediu ajustes: " + (guidance or "(sem detalhes)"))
    elif reason == BlockedReason.WAIVER:
        if key == "waive":
            state.qa_verdict = QAVerdict.WAIVED
            state.note("Founder aceitou o risco apontado pelo Inspector.")
            state.learnings.append(
                {
                    "kind": "risk",
                    "title": "Risco aceito pelo Founder",
                    "detail": state.handoff.get("test", "")[:500],
                }
            )
            goto(state, "review")
        else:  # fix (default)
            if guidance:
                state.note(guidance)
            state.failure_history.append(
                "Inspector apontou: " + state.handoff.get("test", "")[:800]
            )
            goto(state, "dev")
    elif reason == BlockedReason.PERSISTENT_FAILURE:
        if guidance and key not in ("retry",):
            state.note(guidance)
        state.attempts_tier2 = 0
        state.attempts_tier1 = 0
        state.current_tier = "tier2"
        goto(state, resume or "dev")
    elif reason == BlockedReason.CONFLICT:
        goto(state, "review")
    else:  # QUESTION
        if guidance:
            state.note(guidance)
        goto(state, resume or "spec")
    state.blocked_reason = None
    state.resume_stage = None
    state.resume_phase = None
    ctx.emit(
        "story.resumed", story_id=state.story_id, stage=state.stage.value, answer=key or "text"
    )
    return state
