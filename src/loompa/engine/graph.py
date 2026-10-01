"""The story phases: intake, spec, spec_review, plan, dev, test, review (ADR-0006).

    intake -> spec -> spec_review -> plan -> dev -> test -> review -> AWAITING_FOUNDER -> DONE
                |        | (rewrite)  |       ^      |                     |
                v        +------------+       |      v (fail: tier2 x2, tier1 x1; WAIVED)
          AWAITING_FOUNDER (question)         +------+-- AWAITING_FOUNDER

Every node is `async (ctx, state) -> state` and ends with `advance()` or `goto(phase)`; the
runner checkpoints after each one. Which phases a story visits is its `route`, built at intake.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from pathlib import Path

from loompa.agents import (
    AnalystAgent,
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
from loompa.agents.architect import REPLANNED_KEY
from loompa.agents.inspector import test_origin
from loompa.agents.product_owner import CRITERIA_REVIEW_KEY
from loompa.agents.worker import FOUNDER_CHANGES, unfinished
from loompa.backlog import TRIAGE_KEY
from loompa.comms import (
    FounderAnswer,
    FounderMessage,
    MessageKind,
    Option,
    compose_blocked_message,
    compose_research_message,
    sanitize_for_founder,
)
from loompa.engine.context import EngineContext
from loompa.engine.phases import (
    Phase,
    advance,
    autonomy_for,
    build_route,
    goto,
    phase_stage,
    register,
    with_preflight,
)
from loompa.engine.state import (
    Autonomy,
    BlockedReason,
    QAVerdict,
    Stage,
    StoryKind,
    StoryState,
)
from loompa.hygiene import is_test_path
from loompa.llm import LLMError
from loompa.llm.router import TIER_ABOVE
from loompa.risk import needs_preflight
from loompa.sprints import SprintBoard
from loompa.worktrees import GitError, Worktree

Node = Callable[[EngineContext, StoryState], Awaitable[StoryState]]

SKIPPED_PRIORITY = 900  # "deixar para depois": back of the queue


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
    repeats = _count_repeat(state, reason, technical)
    if repeats > 1 and message is None and reason in _RETRYABLE_BLOCKS:
        message = _repeated_block_message(ctx, state, master, executive or technical, repeats)
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


# Blocks whose default answer is "try again": when the same one comes back, saying "try again"
# a third time helps nobody, so the founder is told it repeated and offered the ways out.
_RETRYABLE_BLOCKS = (BlockedReason.PERSISTENT_FAILURE, BlockedReason.CONFLICT)
LAST_BLOCK_KEY = "last_block"
AMEND_KEY = "amend_plan"
CONFLICT_RETRIES_KEY = "conflict_retries"
SELFHEAL_KEY = "self_healed"  # a WAIVED verdict already got its Worker round
_VOLATILE = re.compile(r"[0-9a-f]{7,}|\d+")


def _count_repeat(state: StoryState, reason: BlockedReason, technical: str) -> int:
    """How many times in a row this story stopped for the same thing at the same phase. The
    fingerprint drops numbers and hashes, which change between attempts of the same failure."""
    fp = "|".join((state.phase, reason.value, _VOLATILE.sub("#", (technical or "").lower())[:160]))
    last = state.extra.get(LAST_BLOCK_KEY) or {}
    count = int(last.get("count", 0)) + 1 if last.get("fp") == fp else 1
    state.extra[LAST_BLOCK_KEY] = {"fp": fp, "count": count}
    return count


def _repeated_block_message(
    ctx: EngineContext, state: StoryState, master: MasterAgent, cause: str, repeats: int
) -> FounderMessage:
    cause = sanitize_for_founder(cause).strip().rstrip(".")
    msg = compose_blocked_message(
        factory=ctx.slug,
        story_id=state.story_id,
        story_title=state.title,
        reason=f"Tentei de novo e o mesmo problema voltou ({repeats}ª vez seguida). {cause}.",
        impact="Insistir do mesmo jeito dificilmente resolve. As demais entregas seguem "
        "normalmente enquanto você decide.",
        options=[
            Option(key="skip", label="Deixar para depois (volta ao backlog)", recommended=True),
            Option(key="drop", label="Cancelar esta entrega"),
            Option(
                key="retry",
                label="Tentar mais uma vez",
                description="Se você escrever uma orientação, ela vai junto para quem tentar.",
            ),
        ],
    )
    msg.sender = master.name
    msg.title = f"O mesmo problema voltou em “{state.title}”"
    return msg


_STAGE_OF_PHASE = {
    "intake": Stage.BACKLOG,
    "spec": Stage.SPEC,
    "spec_review": Stage.SPEC,
    "plan": Stage.PLAN,
    "preflight": Stage.PLAN,
    "dev": Stage.DEV,
    "test": Stage.TEST,
    "review": Stage.REVIEW,
    "research": Stage.SPEC,
    "research_review": Stage.REVIEW,
}


# ------------------------------------------------------------------------------ nodes


async def node_intake(ctx: EngineContext, state: StoryState) -> StoryState:
    """Classify the request (kind, complexity) and build its route. Requests too big for one
    story become an epic whose child stories run independently."""
    master = MasterAgent(ctx)
    verdict = await master.classify(state)
    # the kind the Product Owner gave the card when it entered the backlog is the card's (the
    # founder already sees it on the board); intake still decides complexity and any split
    triaged = (state.extra.get(TRIAGE_KEY) or {}).get("kind")
    state.kind = StoryKind(triaged) if triaged in {k.value for k in StoryKind} else verdict.kind
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
    state.autonomy = autonomy_for(state.complexity, ctx.config.schedule.autonomy)
    state.route = build_route(state.kind, state.complexity, state.autonomy)
    ctx.emit(
        "story.classified",
        story_id=state.story_id,
        kind=state.kind.value,
        complexity=state.complexity.value,
        autonomy=state.autonomy.value,
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
    res = await ArchitectAgent(ctx).run(state)
    if not res.ok and state.handoff.get("plan"):
        state.spec_review_rounds = 0  # the rewritten spec gets its own review
        return goto(state, "spec")
    if (
        state.autonomy != Autonomy.PREFLIGHT
        and ctx.config.schedule.autonomy == "auto"
        and needs_preflight(state.allowed_paths)
    ):
        # a schema, a migration or a public contract: planned twice, whatever the complexity
        state.autonomy = Autonomy.PREFLIGHT
        state.route = with_preflight(state.route)
        ctx.emit("story.autonomy", story_id=state.story_id, autonomy=state.autonomy.value)
    return advance(state)


async def node_preflight(ctx: EngineContext, state: StoryState) -> StoryState:
    res = await ArchitectAgent(ctx).preflight(state)
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


def _ensure_worktree(ctx: EngineContext, state: StoryState) -> Worktree:
    if not ctx.worktrees.get(state.story_id) and not ctx.worktrees.head_is_valid():
        DeployerAgent(ctx).bootstrap_repo()
    wt = ctx.worktrees.get(state.story_id) or ctx.worktrees.create(
        state.story_id, title=state.title
    )
    state.worktree = str(wt.path)
    state.branch = wt.branch
    return wt


async def _resolve_with_retries(
    ctx: EngineContext, state: StoryState, wt: Worktree, conflicts: list[str]
) -> bool:
    """Resolve the conflicts of merging the base into the story: the first try on the story's
    tier, then `ops_max_recoveries` (3) more — one on the same tier, the others on the tier above
    (ADR-0016 §5) — each told which files the try before left with markers. False leaves the
    story on its old base, as before; the delivery then reports the conflict."""
    deployer = DeployerAgent(ctx)
    start = "tier1" if state.current_tier == "tier1" else "tier2"
    left: list[str] = []
    for n in range(ctx.config.schedule.ops_max_recoveries + 1):
        tier = start if n <= 1 else TIER_ABOVE[start]
        if n:
            ctx.emit("worktree.sync_retry", story_id=state.story_id, attempt=n, tier=tier)
            conflicts = deployer.sync_with_base(state, wt) or []  # the last try was aborted
            if not conflicts:
                return True
        await WorkerAgent(ctx, tier_override=tier).resolve_conflicts(
            state, wt, conflicts, left_before=left
        )
        left = deployer.git.has_conflict_markers(wt, conflicts)
        if deployer.finish_sync(state, wt, conflicts):
            return True
    return False


async def node_dev(ctx: EngineContext, state: StoryState) -> StoryState:
    # A worktree that cannot be prepared raises: the Ops Loompa tries again (three times, ADR-0016
    # §5) before the founder hears of it, as with any other failure of a step.
    wt = _ensure_worktree(ctx, state)
    if "baseline" not in state.extra:
        state.extra["baseline"] = await InspectorAgent(ctx).baseline(state, wt)
    elif (conflicts := DeployerAgent(ctx).sync_with_base(state, wt)) is not None:
        merged = not conflicts or await _resolve_with_retries(ctx, state, wt, conflicts)
        if merged:
            # the base moved under a story going back to work: "already failing" moved too
            with ctx.worktrees.base_checkout(wt.base) as base_path:
                state.extra["baseline"] = await InspectorAgent(ctx).baseline(
                    state, wt, at=base_path
                )
    guidance = state.extra.pop(AMEND_KEY, None)
    if guidance:
        try:
            await ArchitectAgent(ctx).amend(state, guidance)
        except LLMError as exc:  # the Worker still gets the guidance as a note
            ctx.emit("plan.amend_failed", story_id=state.story_id, error=str(exc)[:200])
    tier = "tier1" if state.current_tier == "tier1" else None
    opencode = ctx.config.worker.backend == "opencode"
    worker_cls = OpenCodeWorker if opencode else WorkerAgent
    label = " ".join(
        p for p in ("Worker Loompa", "Sr" if tier else "", "(OpenCode)" if opencode else "") if p
    )
    worker = worker_cls(ctx, name=label, tier_override=tier)
    res = await worker.run(state, wt)
    await KaizenAgent(ctx).capture(state)
    if res.blocked_reason:
        return await block(
            ctx,
            state,
            BlockedReason.QUESTION,
            res.blocked_reason,
            options=res.blocked_options,
            resume="dev",
        )
    if unfinished(res):
        ctx.emit(
            "story.task_unfinished",
            story_id=state.story_id,
            task=(res.data or {}).get("unfinished"),
            ended_by=(res.data or {}).get("ended_by"),
        )
        return await climb(ctx, state, res.summary, executive=UNFINISHED_EXECUTIVE)
    return advance(state)


async def node_test(ctx: EngineContext, state: StoryState) -> StoryState:
    """Graded quality gate: PASS / CONCERNS move on, FAIL climbs the escalation ladder,
    WAIVED asks the founder (serious finding the tests do not catch)."""
    wt = _ensure_worktree(ctx, state)
    res = await InspectorAgent(ctx).run(state, wt)
    if (res.data or {}).get("lint_only") and await WorkerAgent(ctx).autofix_lint(state, wt):
        # the linter's own fixes, at no model cost, before a failure is counted (7.2)
        ctx.emit("story.lint_autofixed", story_id=state.story_id)
        res = await InspectorAgent(ctx).run(state, wt)
    verdict = QAVerdict((res.data or {}).get("verdict") or ("PASS" if res.ok else "FAIL"))
    findings = list((res.data or {}).get("findings") or [])
    state.qa_verdict = verdict
    state.qa_findings = findings
    if verdict in (QAVerdict.PASS, QAVerdict.CONCERNS):
        state.extra.pop(SELFHEAL_KEY, None)
        worth_a_card = [f for f in findings if f.get("severity") in ("medium", "high")]
        if verdict == QAVerdict.CONCERNS and worth_a_card:
            await KaizenAgent(ctx).capture(
                state,
                items=[
                    {
                        "kind": "opportunity",
                        "title": f"{f.get('id', 'QA')}: {str(f.get('text', ''))[:100]}",
                        "detail": f"Ressalva do Inspector ({f.get('severity', 'low')}) em {state.story_id}: {f.get('text', '')}",
                    }
                    for f in worth_a_card
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
        if not state.extra.get(SELFHEAL_KEY) and "dev" in state.route:
            # one focused Worker round on the serious findings before the founder is asked
            state.extra[SELFHEAL_KEY] = True
            state.failure_history.append("Inspector found (high severity): " + detail)
            ctx.emit("story.self_healing", story_id=state.story_id, findings=len(serious))
            return goto(state, "dev")
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
    failing = list((res.data or {}).get("failing") or [])
    origin = test_origin(ctx.worktrees, wt, failing) if failing else None
    own_only = bool(
        origin
        and (res.data or {}).get("tests_only")
        and origin["own"]
        and not origin["existing"]
        and not origin["unknown"]
    )
    if own_only:
        own = sorted(origin["own"])  # type: ignore[index]
        again = state.extra.get(OWN_FAILURES_KEY) == own
        state.extra[OWN_FAILURES_KEY] = own
        if again and state.acceptance and not state.extra.get(CRITERIA_REVIEW_KEY):
            # The same tests of the story's own failed twice while the product's pass: before a
            # stronger model is paid to meet it, the Product Owner looks at the criterion itself.
            review = await ProductOwnerAgent(ctx).review_criteria(
                state, own, res.summary, _test_changes(ctx, wt)
            )
            state.extra[CRITERIA_REVIEW_KEY] = review.data if review.ok else True
            if review.ok:
                # the criterion was the problem, not the code: the Worker adapts without a tier
                state.failure_history.append(review.summary)
                state.extra.pop(OWN_FAILURES_KEY, None)
                return goto(state, "dev")
    else:
        state.extra.pop(OWN_FAILURES_KEY, None)
    facts, executive = _failure_story(origin)
    return await climb(ctx, state, facts + res.summary, executive=executive)


OWN_FAILURES_KEY = "own_failures"  # the story's own failing tests at the last test run
GENERIC_EXECUTIVE = (
    "As verificações automáticas continuam falhando após várias tentativas, inclusive com o "
    "especialista sênior. O detalhe técnico ficou registrado para a equipe."
)


def _failure_story(origin: dict[str, list[str]] | None) -> tuple[str, str]:
    """What is established about a failing suite, measured in code: a `[facts]` line the next
    attempt and the Master's rewrite read (English), and the founder's fallback text (pt-BR).
    The factory's own new tests failing is not the product breaking: `contas` S-030 told the
    founder "users can't see the help" when only the story's new test failed."""
    if origin and origin["own"] and not origin["existing"]:
        return (
            "[facts] Every failing test was written by the factory for this story; every test the "
            "product already had passes. Nothing was merged: the product the founder uses is "
            "unchanged.\n",
            "Os testes que a equipe escreveu para esta entrega continuam falhando, mesmo com o "
            "especialista sênior. Os testes que o produto já tinha continuam passando: nada do "
            "que já funcionava foi afetado e nada foi integrado à versão principal. Pode ser que o "
            "pedido, como está escrito, não combine com o comportamento atual do produto.",
        )
    if origin and origin["existing"]:
        return (
            "[facts] Tests that existed before this story now fail: "
            + ", ".join(origin["existing"][:5])
            + ". Nothing was merged: the product the founder uses is unchanged.\n",
            "Esta mudança faz falhar verificações de partes do produto que já funcionavam, e as "
            "tentativas de correção, inclusive com o especialista sênior, não resolveram. Nada foi "
            "integrado à versão principal: o produto que você usa continua como estava.",
        )
    return "", GENERIC_EXECUTIVE


def _test_changes(ctx: EngineContext, wt: Worktree) -> str:
    """The story's changes to test files, for the Product Owner to read what they assert."""
    diff = ctx.worktrees.diff(wt, max_chars=60_000)
    blocks = re.split(r"(?=^diff --git )", diff, flags=re.M)
    return "".join(
        b
        for b in blocks
        if b.startswith("diff --git") and is_test_path(b.split(" b/", 1)[-1].split("\n", 1)[0])
    )


UNFINISHED_EXECUTIVE = (
    "Uma das tarefas desta entrega não chegou ao fim dentro do limite de passos, nem com o "
    "especialista sênior. O detalhe técnico ficou registrado para a equipe."
)


async def climb(
    ctx: EngineContext, state: StoryState, failure: str, *, executive: str
) -> StoryState:
    """A failed attempt climbs the escalation ladder: tier 2 once more, then tier 1 twice (re-planned
    on the way up), then the founder — three tries after the first failure (ADR-0016 §5), each
    with the failure before it in hand. Failed means the Inspector's FAIL, or a Worker that could not finish a task
    (Fase 8.5) — which never costs an Inspector run on a half-built story."""
    state.failure_history.append(failure)
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
        if "plan" in state.route and not state.extra.get(REPLANNED_KEY):
            # Two failed attempts may mean the plan, not the code: its `files` fence the Worker
            # away from the real cause (`contas` S-005 could not touch the module that broke the
            # test). Once per story, the Architect re-plans with the failures in hand.
            state.extra[REPLANNED_KEY] = True
            ctx.emit("story.replanned", story_id=state.story_id)
            return goto(state, "plan")
        return goto(state, "dev")
    state.attempts_tier1 += 1
    # ADR-0016 §5: three tries after the first failure — one more on tier 2, two on tier 1. A
    # story that was on tier 1 from the start has all three there.
    tier1_limit = sched.tier1_max_attempts + (
        sched.tier2_max_attempts if state.attempts_tier2 == 0 else 0
    )
    if state.attempts_tier1 < tier1_limit:
        ctx.emit("story.retry", story_id=state.story_id, tier="tier1", attempt=state.attempts_tier1)
        return goto(state, "dev")
    return await block(
        ctx,
        state,
        BlockedReason.PERSISTENT_FAILURE,
        failure,
        resume="dev",
        executive=executive,
    )


MERGE_FAILED_KEY = "approved_merge_failed"

MERGE_FAILED_EXECUTIVE = (
    "Você aprovou a entrega, mas não consegui integrá-la à versão principal do produto. "
    "{tried}Preferi pedir sua orientação em vez de arriscar mexer em algo que é seu."
)


async def _merge_after_approval(
    ctx: EngineContext, state: StoryState, wt: Worktree, error: str
) -> StoryState:
    """The founder approved and the merge into the base failed. The Deployer diagnoses the
    failure and the code runs the action it picks (GIT_ACTIONS), three tries at most — the first
    on its tier, the others on the tier above — before the founder hears of it (ADR-0016 §5)."""
    deployer = DeployerAgent(ctx)
    tried = 0
    for n in range(ctx.config.schedule.ops_max_recoveries):
        action = await deployer.diagnose(
            state, wt, "merge the approved story into the base", error, attempt=n
        )
        if action == "ask_founder":
            break
        tried += 1
        try:
            if action == "clean_main_merge":
                deployer.git.abort_base_merge()
            elif action == "resync":
                conflicts = deployer.sync_with_base(state, wt)
                if conflicts and not await _resolve_with_retries(ctx, state, wt, conflicts):
                    error = "merging the base into the story left conflicts unresolved"
                    continue
            deployer.merge(state, wt)
        except GitError as exc:
            error = str(exc)[-2000:]
            continue
        state.stage = Stage.DONE
        state.phase = ""
        return state
    said = (
        f"Tentei resolver sozinho {tried} vez(es)"
        + (", incluindo com um modelo de IA mais forte. " if tried >= 2 else ". ")
        if tried
        else ""
    )
    return await block(
        ctx,
        state,
        BlockedReason.PERSISTENT_FAILURE,
        f"Approved, but merging into the base failed: {error}",
        resume="review",
        executive=MERGE_FAILED_EXECUTIVE.format(tried=said),
    )


async def node_review(ctx: EngineContext, state: StoryState) -> StoryState:
    wt = _ensure_worktree(ctx, state)
    failed = state.extra.pop(MERGE_FAILED_KEY, None)
    if failed:
        return await _merge_after_approval(ctx, state, wt, str(failed))
    await KaizenAgent(ctx).capture(
        state
    )  # findings never get lost: sweep what no earlier phase filed
    res = await DeployerAgent(ctx).run(state, wt)
    if not res.ok:
        if (
            "dev" in state.route
            and state.extra.get(CONFLICT_RETRIES_KEY, 0) < ctx.config.schedule.ops_max_recoveries
        ):
            # the base moved while the story was in test: `dev` merges it in and resolves the
            # conflict, then the story is tested again. The founder hears only if that fails.
            state.extra[CONFLICT_RETRIES_KEY] = state.extra.get(CONFLICT_RETRIES_KEY, 0) + 1
            ctx.emit("story.reintegrating", story_id=state.story_id)
            return goto(state, "dev")
        return await block(ctx, state, BlockedReason.CONFLICT, res.summary, resume="review")
    state.extra.pop(CONFLICT_RETRIES_KEY, None)
    state.blocked_reason = BlockedReason.DELIVERY
    state.resume_stage = Stage.REVIEW
    state.resume_phase = "review"
    state.stage = Stage.AWAITING_FOUNDER
    return state


async def node_research(ctx: EngineContext, state: StoryState) -> StoryState:
    """The Analyst researches the request (repository, memory and, when configured, the web)."""
    res = await AnalystAgent(ctx).run(state)
    if res.blocked_reason:
        return await block(
            ctx,
            state,
            BlockedReason.QUESTION,
            res.blocked_reason,
            options=res.blocked_options,
            resume="research",
        )
    return advance(state)


async def node_research_review(ctx: EngineContext, state: StoryState) -> StoryState:
    """The Product Owner reviews the report (sources, honesty); one rewrite round at most, then
    the concerns travel with the delivery. The founder reads the result: no code, no merge."""
    review = await ProductOwnerAgent(ctx).review_research(state)
    state.research_review_rounds += 1
    if not review.ok and state.research_review_rounds < 2:
        state.hand_off(review.summary, phase="research_review")
        ctx.emit("research.rejected", story_id=state.story_id, issues=review.summary[:500])
        return goto(state, "research")
    report = state.extra.setdefault("research", {})
    if not review.ok:
        report["review_concerns"] = review.summary
        state.review_notes = (state.review_notes + "\n" + review.summary).strip()
    else:
        report.pop("review_concerns", None)
    ctx.emit("research.approved", story_id=state.story_id, rounds=state.research_review_rounds)
    await KaizenAgent(ctx).capture(state)  # follow-ups become cards the founder decides on
    story = ctx.store.get_story(state.story_id) or {}
    state.delivery_summary = str(report.get("summary") or state.title)
    msg = ctx.inbox(
        compose_research_message(
            factory=ctx.slug,
            story_id=state.story_id,
            story_title=state.title,
            summary=state.delivery_summary,
            recommendation=str(report.get("recommendation") or ""),
            limitations=list(report.get("limitations") or []),
            sources=len(report.get("sources") or []),
            cost_usd=float(story.get("cost_usd") or 0.0),
            cards=KaizenAgent(ctx).suggested_cards(state),
            concerns=str(report.get("review_concerns") or ""),
        )
    )
    ctx.emit(
        "story.delivered", story_id=state.story_id, agent="Analyst Loompa", pr_url=None, commits=0
    )
    state.blocked_message_id = msg.id
    state.blocked_reason = BlockedReason.DELIVERY
    state.resume_stage = Stage.REVIEW
    state.resume_phase = "research_review"
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
register(
    Phase(
        "preflight",
        node_preflight,
        Stage.PLAN,
        owner="architect",
        reviewer="product_owner",
        description="brownfield risk report and mitigations before the first commit",
    )
)
register(Phase("dev", node_dev, Stage.DEV, owner="worker", description="DoD checklist per task"))
register(Phase("test", node_test, Stage.TEST, owner="inspector", description="graded QA gate"))
register(Phase("review", node_review, Stage.REVIEW, owner="deployer", reviewer="founder"))
register(
    Phase(
        "research",
        node_research,
        Stage.SPEC,
        owner="analyst",
        reviewer="product_owner",
        description="repository, memory and web research; sources checked in code",
    )
)
register(
    Phase(
        "research_review",
        node_research_review,
        Stage.REVIEW,
        owner="product_owner",
        reviewer="founder",
        description="sources and honesty gate, then the report goes to the founder",
    )
)

NODES: dict[Stage, Node] = {  # legacy view kept for callers that index by stage
    Stage.BACKLOG: node_intake,
    Stage.SPEC: node_spec,
    Stage.PLAN: node_plan,
    Stage.DEV: node_dev,
    Stage.TEST: node_test,
    Stage.REVIEW: node_review,
}


# ----------------------------------------------------------------------------- resume


def back_to_backlog(
    ctx: EngineContext,
    state: StoryState,
    *,
    priority: int | None = None,
    leave_sprint: bool = True,
) -> None:
    """The story leaves the pipeline and waits in the backlog again ("deixar para depois", or
    taken out of a sprint in a meeting). Its branch stays; when a sprint admits it again it
    starts over at its first phase. It leaves the running sprint too: a card waiting in the
    backlog would hold that sprint open forever, and only one sprint runs at a time. A
    cancelled sprint keeps its list as the record of what it held (`leave_sprint=False`)."""
    if leave_sprint:
        board = SprintBoard(ctx.store, ctx.slug)
        running = board.running()
        if running is not None and state.story_id in running.story_ids:
            board.withdraw(state.story_id)
    po = ProductOwnerAgent(ctx)
    po.set_status(state.story_id, Stage.BACKLOG)
    if priority is not None:
        po.set_priority(state.story_id, priority)
    goto(state, "intake" if not state.route else state.route[0])
    state.stage = Stage.BACKLOG
    state.blocked_reason = None
    state.blocked_message_id = None
    state.resume_stage = None
    state.resume_phase = None


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
        ProductOwnerAgent(ctx).set_status(state.story_id, Stage.CANCELLED)
        state.stage = Stage.CANCELLED
        state.phase = ""
        ctx.worktrees.remove(state.story_id)
    elif key == "skip":
        back_to_backlog(ctx, state, priority=SKIPPED_PRIORITY)
    elif reason == BlockedReason.DELIVERY:
        if key in ("approve", "approved", "ok", "yes", "sim"):
            wt = ctx.worktrees.get(state.story_id)
            if wt is not None:
                try:
                    DeployerAgent(ctx).merge(state, wt)
                except GitError as exc:
                    # approved: `review` diagnoses and merges without asking again (ADR-0016 §5)
                    state.note(f"Aprovado, mas a integração falhou: {exc}")
                    state.extra[MERGE_FAILED_KEY] = str(exc)[-2000:]
                    goto(state, "review")
                    state.blocked_reason = None
                    return state
            state.stage = Stage.DONE
            state.phase = ""
        elif state.kind == StoryKind.RESEARCH:
            state.note(guidance or "Founder pediu mais aprofundamento na pesquisa.")
            state.research_review_rounds = 0  # the new report gets its own review rounds
            goto(state, "research")
        else:
            state.note(guidance or "Founder pediu ajustes na entrega.")
            goto(state, "dev")
            state.failure_history.append(f"{FOUNDER_CHANGES}: " + (guidance or "(no details)"))
            if guidance and "plan" in state.route:
                # the request may need files the plan never listed: the Architect amends it and
                # the Worker does the new tasks, not the whole story again
                state.extra[AMEND_KEY] = guidance
            else:
                state.tasks_done = []
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
            state.failure_history.append("Inspector found: " + state.handoff.get("test", "")[:800])
            goto(state, "dev")
    elif reason == BlockedReason.PERSISTENT_FAILURE:
        # "Try again" is not guidance by itself, but what the founder wrote alongside it is.
        note = (answer.text or "").strip() if key == "retry" else guidance
        if note:
            state.note(note)
        state.attempts_tier2 = 0
        state.attempts_tier1 = 0
        state.current_tier = "tier2"
        goto(state, resume or "dev")
        if note and state.phase == "dev" and "plan" in state.route:
            # what the founder wrote may withdraw part of the plan: the Architect amends the
            # tasks, or the Worker's self-check keeps asking for it (`contas` S-030)
            state.extra[AMEND_KEY] = note
    elif reason == BlockedReason.CONFLICT:
        # back through `dev`: the base is merged in there and conflicts go to the Worker;
        # retrying the delivery's rebase alone only ever met the same conflict again
        goto(state, "dev" if "dev" in state.route else "review")
    else:  # QUESTION
        if guidance:
            state.note(guidance)
        goto(state, resume or "spec")
        if guidance and state.phase == "dev" and "plan" in state.route:
            # the Worker asked because its fence or its tasks did not cover something
            state.extra[AMEND_KEY] = guidance
    state.blocked_reason = None
    state.resume_stage = None
    state.resume_phase = None
    ctx.emit(
        "story.resumed", story_id=state.story_id, stage=state.stage.value, answer=key or "text"
    )
    return state
