"""The story phases: intake, spec, spec_review, plan, plan_review, dev, test, review (ADR-0006).

    intake -> spec -> spec_review -> plan -> plan_review -> dev -> test -> review -> DONE
                |        | (rewrite)  |  ^      | (re-plan)  ^      |          |
                v        +------------+  +------+            |      v (fail: tier2 x2, tier1 x1)
          AWAITING_FOUNDER (question)                        +------+-- AWAITING_FOUNDER

Every node is `async (ctx, state) -> state` and ends with `advance()` or `goto(phase)`; the
runner checkpoints after each one. Which phases a story visits is its `route`, built at intake.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

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
from loompa.agents.architect import (
    FULL_PLAN_KEY,
    LEAN_KEY,
    REPLANNED_KEY,
    TASKS_WITHDRAWN_KEY,
    withdraw_tasks,
)
from loompa.agents.base import AgentResult
from loompa.agents.inspector import test_origin
from loompa.agents.product_owner import CRITERIA_ALIGNED_KEY, CRITERIA_REVIEW_KEY
from loompa.agents.worker import FOUNDER_CHANGES, unfinished
from loompa.backlog import OUTCOME_KEY, TRIAGE_KEY
from loompa.comms import (
    FounderAnswer,
    FounderMessage,
    MessageKind,
    Option,
    compose_blocked_message,
    compose_research_message,
    sanitize_for_founder,
)
from loompa.comms.answers import RESOLVED, RESPEC, action_of, executable_options
from loompa.dependencies import (
    BUILT_ON_KEY,
    INTEGRATION_KEY,
    WAIT_KEY,
    Gate,
    base_branch_for,
    dependents,
    deps_of,
    gone_under,
    merge_gate,
    merge_parked_note,
    parent_in_rework,
    plan_gate,
    rows_by_id,
    sync_refs,
)
from loompa.engine.context import EngineContext
from loompa.engine.line import forget_resolved, line_targets
from loompa.engine.merge_queue import MergeLock, dequeue, enqueue, merge_queue, queued
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
    Complexity,
    QAVerdict,
    Stage,
    StoryKind,
    StoryState,
)
from loompa.finance.story_cap import CAP_BASE_KEY, over_cap
from loompa.hygiene import is_test_path
from loompa.llm import LLMError
from loompa.llm.router import TIER_ABOVE
from loompa.plan_check import PlanReport, check_plan, record_tasks, records_in_criteria
from loompa.risk import needs_preflight
from loompa.speckit import story_dir, tasks_from_markdown
from loompa.speckit.artifacts import TaskItem
from loompa.sprints import SprintBoard
from loompa.stack_check import STACK_KEY, foreign_files, mismatch_note
from loompa.store import now_iso
from loompa.worktrees import GitError, MergeConflict, Worktree

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
    cause: str | None = None,
) -> StoryState:
    """Isolate the story: write an executive inbox message and pause. Never blocks the line.
    `resume` is the phase to continue from after the founder answers (defaults to the current).
    `cause` names what stopped it in code terms (the files the fence refused): the same cause
    twice in a row is a repeat whatever words the model used (ADR-0036 §6)."""
    ref = _write_tech_log(ctx, state, technical)
    master = MasterAgent(ctx)
    # the same point in code terms (open task, fence, work committed), whatever the words: the
    # third question in a row there is not asked (ADR-0037 §2, tamagotchi S-094 asked eight)
    stalls = _stalled(ctx, state) if reason == BlockedReason.QUESTION else 0
    if stalls >= STALL_LIMIT:
        return _park_stalled(ctx, state, stalls, ref)
    repeats = _count_repeat(state, reason, cause or technical)
    if repeats > 1 and message is None and (reason in _RETRYABLE_BLOCKS or cause):
        message = _repeated_block_message(ctx, state, master, executive or technical, repeats)
    if message is not None:
        message.technical_ref = message.technical_ref or ref
        if stalls > 1:
            message.context = f"{REPEAT_NOTE} {message.context}"
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
        if stalls > 1:
            msg.context = f"{REPEAT_NOTE} {msg.context}"
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


async def gate_plan(ctx: EngineContext, state: StoryState) -> StoryState | None:
    """The gate before `plan` (ADR-0021). None: plan now. Otherwise the state to keep: parked
    (the chain is not ready; the run ends and the slot frees) or blocked for the founder (a
    dependency was cancelled or left the sprint)."""
    rows = rows_by_id(ctx.store, ctx.slug)
    me = rows.get(state.story_id)
    if me is None or not (deps_of(me) or dependents(rows, state.story_id)):
        state.extra.pop(WAIT_KEY, None)
        return None
    gate = plan_gate(rows, state.story_id)
    if gate.gate == Gate.OPEN and gate.integrate:
        blocked = await _integrate_dependencies(ctx, state, rows, gate.integrate)
        if blocked is not None:
            return blocked
    if gate.gate == Gate.OPEN:
        if state.extra.pop(WAIT_KEY, None) is not None:
            ctx.emit("story.unparked", story_id=state.story_id)
        return None
    if gate.gate == Gate.WAIT:
        first = WAIT_KEY not in state.extra
        state.extra[WAIT_KEY] = gate.as_dict()
        if first:
            ctx.emit(
                "story.waiting",
                story_id=state.story_id,
                pending=gate.pending,
                chain=gate.chain,
                founder=gate.founder,
            )
        return state
    state.extra.pop(WAIT_KEY, None)
    titles = [f"{d} (“{(rows.get(d) or {}).get('title', d)}”)" for d in gate.gone]
    one = len(titles) == 1
    msg = FounderMessage(
        factory=ctx.slug,
        story_id=state.story_id,
        kind=MessageKind.BLOCKED,
        sender=MasterAgent.display,
        title=f"“{state.title}” dependia de {'uma história que saiu' if one else 'histórias que saíram'} do sprint",
        context=(
            f"Esta história foi pensada em cima de {', '.join(titles)}, que "
            f"{'foi cancelada ou voltou' if one else 'foram canceladas ou voltaram'} ao backlog. "
            "A especificação dela já está pronta; o plano ainda não foi feito."
        ),
        impact="Nada foi construído em cima disso ainda. As demais entregas seguem normalmente.",
        options=[
            Option(key="skip", label="Devolver ao backlog", recommended=True),
            Option(
                key="detach",
                label="Seguir sem a dependência",
                description="A especificação é refeita sem contar com ela.",
            ),
            Option(key="drop", label="Cancelar esta também"),
        ],
    )
    return await block(
        ctx,
        state,
        BlockedReason.DEPENDENCY,
        f"dependencies gone: {', '.join(gate.gone)}",
        resume="plan",
        message=msg,
    )


# Blocks whose default answer is "try again": when the same one comes back, saying "try again"
# a third time helps nobody, so the founder is told it repeated and offered the ways out.
_RETRYABLE_BLOCKS = (BlockedReason.PERSISTENT_FAILURE, BlockedReason.CONFLICT)
LAST_BLOCK_KEY = "last_block"
AMEND_KEY = "amend_plan"
RESOLVE_KEY = "resolve"  # {"why", "resume"}: the founder said the story is already resolved
ALIGN_KEY = "align_criteria"  # the founder's request for changes, for the Product Owner first
FENCE_AMEND_KEY = (
    "fence_amend"  # files the Worker asked for once; the Architect widens the plan once
)
CONFLICT_RETRIES_KEY = "conflict_retries"
SELFHEAL_KEY = "self_healed"  # a WAIVED verdict already got its Worker round
_VOLATILE = re.compile(r"[0-9a-f]{7,}|\d+")


STALL_KEY = "stalled_asks"  # {"at": where the story stands, "count": questions in a row there}
PARK_KEY = "park"  # set by a node: the Scheduler puts the story back in the backlog after the run
STALL_LIMIT = 3
REPEAT_NOTE = "É a segunda vez seguida que a equipe para neste mesmo ponto, sem conseguir avançar."


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:10]


def _open_task(ctx: EngineContext, state: StoryState) -> TaskItem | None:
    """The task the Worker would do next: the first one neither checked nor done."""
    paths = story_dir(ctx.root, state.story_id)
    if not paths.tasks.is_file():
        return None
    done = set(state.tasks_done)
    return next(
        (
            t
            for t in tasks_from_markdown(paths.tasks.read_text(encoding="utf-8"))
            if not t.done and t.number not in done
        ),
        None,
    )


def _progress(ctx: EngineContext, state: StoryState) -> str:
    """Where a story stands, in code terms: the phase, the task it would do next and its text,
    the paths it may touch, the criteria, and how much it has done. Two questions asked with the
    same answer here were asked about the same thing, whatever the model wrote (ADR-0037 §2)."""
    nxt = _open_task(ctx, state)
    open_task = f"T{nxt.number}:{_digest(nxt.text)}" if nxt is not None else ""
    return "|".join(
        (
            state.phase,
            open_task,
            _digest("\n".join(sorted(state.allowed_paths))),
            _digest("\n".join(state.acceptance)),
            str(len(state.commits)),
            str(len(state.tasks_done)),
            str(bool(state.spec_ready)),
            str(bool(state.plan_ready)),
        )
    )


def _stalled(ctx: EngineContext, state: StoryState) -> int:
    """How many questions in a row this story asked from the same point (see `_progress`)."""
    at = _progress(ctx, state)
    last = state.extra.get(STALL_KEY) or {}
    count = int(last.get("count", 0)) + 1 if last.get("at") == at else 1
    state.extra[STALL_KEY] = {"at": at, "count": count}
    return count


def _park_stalled(ctx: EngineContext, state: StoryState, count: int, ref: str) -> StoryState:
    """The third question in a row from the same point is not asked (ADR-0037 §2). The story goes
    back to the backlog with all it did and was told, the way the founder withdraws one: the
    Scheduler does it once this run ends (`PARK_KEY`), and the founder gets one plain note."""
    ctx.inbox(
        FounderMessage(
            factory=ctx.slug,
            story_id=state.story_id,
            kind=MessageKind.INFO,
            sender=MasterAgent.display,
            title=f"“{state.title}” voltou ao backlog",
            context=(
                "A equipe parou três vezes seguidas no mesmo ponto desta história, mesmo com as "
                "suas respostas, sem conseguir avançar. Para não tomar mais o seu tempo, ela "
                "voltou ao backlog com tudo o que já foi feito e respondido."
            ),
            impact=(
                "As demais entregas seguem normalmente. Ela pode voltar a um sprint pela reunião "
                "de sprint."
            ),
            technical_ref=ref,
            allow_free_text=False,
        )
    )
    state.extra.pop(STALL_KEY, None)
    state.extra[PARK_KEY] = "stalled"
    state.blocked_reason = BlockedReason.QUESTION
    state.blocked_message_id = None
    state.resume_phase = state.phase
    state.resume_stage = state.stage
    state.stage = Stage.AWAITING_FOUNDER
    ctx.emit("story.stalled_asks", story_id=state.story_id, count=count)
    return state


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


def _records_block_message(ctx: EngineContext, state: StoryState) -> FounderMessage:
    """The plan still asks to write the factory's records after the Architect revised it once
    (ADR-0036 §6). Said plainly, with the ways out the code can carry out."""
    msg = compose_blocked_message(
        factory=ctx.slug,
        story_id=state.story_id,
        story_title=state.title,
        reason=(
            "Um passo do plano pede para gravar um documento na pasta interna da fábrica, que "
            "não faz parte do produto e não pode ser liberada para uma história. O plano já foi "
            "revisto uma vez e o passo continuou lá."
        ),
        impact="Nada foi integrado ao produto. As demais entregas seguem normalmente.",
        options=[
            Option(key="skip", label="Deixar para depois (volta ao backlog)", recommended=True),
            Option(key="drop", label="Cancelar esta entrega"),
            Option(
                key="retry",
                label="Tentar de novo sem esse passo",
                description="Se você escrever uma orientação, ela vai junto para quem tentar.",
            ),
        ],
    )
    msg.sender = MasterAgent.display
    msg.title = f"Um passo de “{state.title}” pede algo que não pode ser liberado"
    return msg


_STAGE_OF_PHASE = {
    "intake": Stage.BACKLOG,
    "spec": Stage.SPEC,
    "spec_review": Stage.SPEC,
    "plan": Stage.PLAN,
    "plan_review": Stage.PLAN,
    "preflight": Stage.PLAN,
    "dev": Stage.DEV,
    "test": Stage.TEST,
    "review": Stage.REVIEW,
    "research": Stage.SPEC,
    "research_review": Stage.REVIEW,
}


# ------------------------------------------------------------------------------ nodes


async def _integrate_dependencies(
    ctx: EngineContext, state: StoryState, rows: dict[str, dict[str, Any]], deps: list[str]
) -> StoryState | None:
    """This story depends on deliveries that are built and green, none of them in the base and
    none carrying the others: the Deployer merges them into one integration branch, which the
    story's worktree then starts from (ADR-0027). None: the branch is ready, plan now.

    A conflict between two deliveries the founder has not accepted is not the factory's to settle
    — resolving it would reconcile, inside a third story's branch, code that may still be sent
    back — so the story stops and asks. Accepting one of the deliveries settles it: one of them is
    then in the base and this story starts from the other."""
    branches = {d: str((rows.get(d) or {}).get("branch") or "") for d in deps}
    missing = [d for d, b in branches.items() if not b]
    if missing:  # nothing to start from: wait as before
        ctx.emit("story.integration_skipped", story_id=state.story_id, without_branch=missing)
        return None
    conflicts = DeployerAgent(ctx).integrate_dependencies(state, branches)
    if not conflicts:
        return None
    titles = [f"“{(rows.get(d) or {}).get('title', d)}”" for d in deps]
    return await block(
        ctx,
        state,
        BlockedReason.INTEGRATION,
        f"deliveries {', '.join(deps)} conflict in {', '.join(conflicts[:5])}",
        resume="plan",
        message=FounderMessage(
            factory=ctx.slug,
            story_id=state.story_id,
            kind=MessageKind.BLOCKED,
            sender=MasterAgent.display,
            title=f"“{state.title}” depende de duas entregas que se desencontram",
            context=(
                f"Esta história é feita em cima de {' e '.join(titles)}, e as duas mexeram no "
                "mesmo lugar do produto. Juntar as duas sem você ter aceito nenhuma seria decidir "
                "no seu lugar qual das duas vale."
            ),
            impact="A entrega está pausada; as demais seguem normalmente.",
            options=[
                Option(
                    key="wait",
                    label="Vou aceitar uma das entregas primeiro",
                    recommended=True,
                    description="Esta história espera e segue sozinha depois do seu aceite.",
                ),
                Option(key="skip", label="Deixar para depois (volta ao backlog)"),
                Option(key="drop", label="Cancelar esta entrega"),
            ],
        ),
    )


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
    # a part of an epic is never split again: contas Sprint 2 split a
    # child into three more that duplicated two of its siblings
    is_part = (ctx.store.get_story(state.story_id) or {}).get("origin") == "epic"
    if len(verdict.children) > 1 and not is_part:
        ids = master.split_epic(state, verdict.children)
        state.delivery_summary = f"Desmembrada em {len(ids)} histórias: {', '.join(ids)}"
        state.extra["children"] = ids
        state.route = ["intake"]
        state.stage = Stage.DONE
        state.phase = ""
        ctx.emit("story.split", story_id=state.story_id, children=ids)
        return state
    state.autonomy = autonomy_for(state.complexity, ctx.config.schedule.autonomy)
    mode = gated(ctx, verdict.screen) if state.kind != StoryKind.RESEARCH else ""
    if mode:
        state.extra[DESIGN_KEY] = {"mode": mode, "alternatives": opens_area(ctx, state, mode)}
    state.route = build_route(state.kind, state.complexity, state.autonomy, design=bool(mode))
    ctx.emit(
        "story.classified",
        story_id=state.story_id,
        kind=state.kind.value,
        complexity=state.complexity.value,
        autonomy=state.autonomy.value,
        route=state.route,
        design=mode or None,
    )
    return goto(state, state.next_phase("intake") or "spec")


SPEC_RECORDS_KEY = "spec_records"  # the criteria naming the factory's records, sent back once
PLAN_RECORDS_KEY = "plan_records"  # a re-plan still writing them: the spec was sent back once
RECORDS_RULE = (
    "The factory's own records under .loompa/ (its decisions, specs, lessons and constitution) "
    "are not part of the product: the factory writes them itself and leaves them out of every "
    "story's branch at merge. No criterion may ask to create, edit or read them, and no test may "
    "read them. When a test reads them, the fix is that test: rewrite it to check what the "
    "product does, or remove it. "
)


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
    state.handoff.pop("spec_check", None)  # read by the spec just written
    if bad := records_in_criteria(state.acceptance):
        # ADR-0037 §5, tamagotchi S-094: ten criteria asked to create two decision documents
        # and to change no test, and the plan gate could only refuse the tasks that followed
        if not state.extra.get(SPEC_RECORDS_KEY):
            state.extra[SPEC_RECORDS_KEY] = [i for i, _ in bad]
            state.hand_off(
                RECORDS_RULE
                + "Rewrite these criteria: "
                + "; ".join(f"criterion {i} names {path}" for i, path in bad),
                phase="spec_check",
            )
            state.spec_ready = False
            ctx.emit("spec.records", story_id=state.story_id, criteria=[i for i, _ in bad])
            return goto(state, "spec")
        if len(bad) == len(state.acceptance):
            # rewritten once and every criterion is still a record: nothing for the product
            state.extra[RESOLVE_KEY] = {
                "why": "a história só pedia registros internos da fábrica",
                "resume": "spec",
            }
            return goto(state, "resolve")
        state.failure_history.append(
            "Criteria still naming the factory's records after one rewrite: "
            + ", ".join(str(i) for i, _ in bad)
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
                "process": True,  # about how the story went, not the product (ADR-0037 §8)
                "detail": review.summary[:500],
            }
        )
    ctx.emit("spec.approved", story_id=state.story_id, rounds=state.spec_review_rounds)
    return advance(state)


async def node_plan(ctx: EngineContext, state: StoryState) -> StoryState:
    # a story written on top of a delivery that is not in the base yet plans inside its own
    # worktree, which starts from that branch: otherwise the Architect would plan against a
    # repository without the code the story depends on (11.5)
    on = base_branch_for(rows_by_id(ctx.store, ctx.slug), state.story_id)
    at = _ensure_worktree(ctx, state).path if on else None
    res = await ArchitectAgent(ctx).run(state, at=at)
    if not res.ok and state.handoff.get("plan"):
        state.spec_review_rounds = 0  # the rewritten spec gets its own review
        return goto(state, "spec")
    lean = state.extra.pop(LEAN_KEY, False)
    if lean and (state.extra.get(FULL_PLAN_KEY) or needs_preflight(state.allowed_paths)):
        # the short form left out exactly what this plan needs: the impact on what uses the code
        # it changes, the rollback of a schema or a contract, the constitution it has to argue
        # with. The story is planned again, in full, over the same spec (ADR-0028).
        why = state.extra.get(FULL_PLAN_KEY) or "o plano toca schema, migração ou contrato"
        state.extra[FULL_PLAN_KEY] = why
        ctx.emit("plan.not_simple", story_id=state.story_id, why=why)
        return goto(state, "plan")
    if (
        state.autonomy != Autonomy.PREFLIGHT
        and ctx.config.schedule.autonomy == "auto"
        and needs_preflight(state.allowed_paths)
    ):
        # a schema, a migration or a public contract: planned twice, whatever the complexity
        state.autonomy = Autonomy.PREFLIGHT
        state.route = with_preflight(state.route)
        ctx.emit("story.autonomy", story_id=state.story_id, autonomy=state.autonomy.value)
    languages = ctx.config.stack.languages
    foreign = foreign_files(state.allowed_paths, languages)
    checked = state.extra.get(STACK_KEY)
    if foreign and checked != "accepted":
        ctx.emit(
            "plan.foreign_language",
            story_id=state.story_id,
            languages=sorted(foreign),
            stack=languages,
            replanned=bool(checked),
        )
        if not checked:
            # once: the Architect plans again with the mismatch named (architect.STACK_NOTE)
            state.extra[STACK_KEY] = mismatch_note(foreign, languages)
            return goto(state, "plan")
        names = ", ".join(sorted(foreign))
        state.extra[STACK_KEY] = "asked"
        return await block(
            ctx,
            state,
            BlockedReason.STACK,
            f"plan writes {names} source files; the factory stack is {', '.join(languages)}",
            resume=state.next_phase() or "dev",
            message=FounderMessage(
                factory=ctx.slug,
                story_id=state.story_id,
                kind=MessageKind.BLOCKED,
                sender="Architect Loompa",
                title=f"“{state.title}” foi planejada em outra linguagem",
                context=(
                    f"O produto é feito em {', '.join(languages)}, mas o plano desta entrega "
                    f"escreve código em {names}, mesmo depois de eu pedir um novo plano. Isso "
                    "costuma gerar uma segunda versão de algo que já existe."
                ),
                impact="A entrega está pausada; as demais seguem normalmente.",
                options=[
                    Option(key="proceed", label=f"Seguir com o plano em {names}"),
                    Option(key="skip", label="Deixar para depois (volta ao backlog)"),
                    Option(key="drop", label="Cancelar esta entrega"),
                ],
            ),
        )
    return advance(state)


async def node_plan_review(ctx: EngineContext, state: StoryState) -> StoryState:
    """The plan's gate (ADR-0026). Code first: `check_plan` measures in the repository what a
    plan can get wrong (a criterion with no planned test, a task that names no file, a path that
    does not exist) and a plan carrying any of it is re-planned once, at no token cost. The
    Product Owner is called only when the plan asks for it: a COMPLEX story, a route that already
    earned its pre-flight, a plan whose own size says the story is bigger than its label, a
    re-plan after failures, or problems the one round did not clear. A SIMPLE or plain STANDARD
    plan that passes the checks goes to the Worker without a review call.

    Why the asymmetry: the only check of the acceptance criteria today is the Inspector's judge,
    which runs after the whole implementation exists; a criterion the plan forgot costs a full
    development cycle to find there, and this review costs one call."""
    paths = story_dir(ctx.root, state.story_id)
    tasks = (
        [t.text for t in tasks_from_markdown(paths.tasks.read_text(encoding="utf-8"))]
        if paths.tasks.is_file()
        else []
    )
    # the worktree, not the main checkout: a story built on a delivery not merged yet plans
    # against that delivery's files (ADR-0027), and a size or a path is only true there
    tree = Path(state.worktree) if state.worktree and Path(state.worktree).is_dir() else ctx.root
    report = check_plan(
        tree,
        acceptance=state.acceptance,
        files=state.allowed_paths,
        tasks=tasks,
        plan_md=paths.plan.read_text(encoding="utf-8") if paths.plan.is_file() else "",
    )
    state.plan_review_rounds += 1
    ctx.emit(
        "plan.checked",
        story_id=state.story_id,
        problems=report.problems[:8],
        signals=report.signals,
        rounds=state.plan_review_rounds,
    )
    if report.problems and state.plan_review_rounds < 2:
        state.hand_off(
            "The plan was refused by the automatic checks. Fix exactly these, keeping the rest of "
            "the plan: " + "; ".join(report.problems[:8]),
            phase="plan_review",
        )
        return goto(state, "plan")
    if report.problems and (records := record_tasks(report.problems)):
        # ADR-0037 §5: a re-plan that still writes or tests the factory's records never builds:
        # those tasks are refused by the fence and the Worker asks the founder (S-094, eight
        # times). The criteria likely ask for it, so the spec is written again first; if the plan
        # still carries them after that, those tasks are withdrawn.
        why = [p for p in report.problems if record_tasks([p])]
        if not state.extra.get(PLAN_RECORDS_KEY):
            state.extra[PLAN_RECORDS_KEY] = records
            state.hand_off(
                RECORDS_RULE + "The plan for these criteria still did it: " + "; ".join(why[:6]),
                phase="spec_check",
            )
            state.spec_ready = False
            state.plan_ready = False
            state.spec_review_rounds = 0
            state.plan_review_rounds = 0
            ctx.emit("plan.records_to_spec", story_id=state.story_id, tasks=records)
            return goto(state, "spec")
        paths.tasks.write_text(
            withdraw_tasks(paths.tasks.read_text(encoding="utf-8"), records), encoding="utf-8"
        )
        state.tasks_done = sorted({*state.tasks_done, *records})
        gone = state.extra.setdefault(TASKS_WITHDRAWN_KEY, [])
        gone.extend(n for n in records if n not in gone)
        state.failure_history.append(
            "Tasks about the factory's records withdrawn: " + "; ".join(why[:6])
        )
        ctx.emit("plan.records_withdrawn", story_id=state.story_id, tasks=records)
        if _open_task(ctx, state) is None and not state.commits:
            state.extra[RESOLVE_KEY] = {
                "why": "o plano só tinha passos sobre registros internos da fábrica",
                "resume": "plan",
            }
            return goto(state, "resolve")
    elif report.problems:
        # the second plan still carries them: the Architect and the Worker see them as the story's
        # last failure instead of the founder hearing about paths and criterion numbers
        # never "a file is missing": the Worker made an empty stub to satisfy it (12.1, S-061)
        kept = [p for p in report.problems if "not in the repository" not in p]
        if kept:
            state.failure_history.append("Plan checks still failing: " + "; ".join(kept[:8]))
    _promote_from_plan(ctx, state, report.signals)
    state.handoff.pop("plan_review", None)  # consumed by the re-plan that just passed
    if not _plan_needs_review(state, report):
        return advance(state)
    review = await ProductOwnerAgent(ctx).review_plan(state, report.problems)
    if not review.ok and state.plan_review_rounds < 2:
        state.hand_off(review.summary, phase="plan_review")
        ctx.emit("plan.rejected", story_id=state.story_id, issues=review.summary[:500])
        return goto(state, "plan")
    if not review.ok:
        # one round only, as with the spec: the concerns travel with the story instead of holding
        # the line, and the Inspector still judges the criteria at `test`
        state.failure_history.append(f"Plan reviewed with concerns: {review.summary[:400]}")
        state.learnings.append(
            {
                "kind": "opportunity",
                "title": "Plano seguiu com ressalvas do Product Owner",
                "process": True,  # about how the story went, not the product (ADR-0037 §8)
                "detail": sanitize_for_founder(review.summary)[:500],
            }
        )
    ctx.emit("plan.approved", story_id=state.story_id, rounds=state.plan_review_rounds)
    return advance(state)


def _promote_from_plan(ctx: EngineContext, state: StoryState, signals: list[str]) -> None:
    """The plan is better evidence of size than the guess intake made before anyone had read the
    code (`master.classify` falls back to STANDARD whenever it is unsure). A plan that comes back
    too big promotes the story to COMPLEX and earns the pre-flight, the same lift `node_plan`
    already does from the plan's own risky paths."""
    if not signals or state.complexity == Complexity.COMPLEX:
        return
    if ctx.config.schedule.autonomy != "auto":  # the founder set the ceremony by hand
        return
    state.complexity = Complexity.COMPLEX
    state.autonomy = Autonomy.PREFLIGHT
    state.route = with_preflight(state.route)
    ctx.emit(
        "story.complexity",
        story_id=state.story_id,
        complexity=state.complexity.value,
        autonomy=state.autonomy.value,
        why=signals,
    )


def _plan_needs_review(state: StoryState, report: PlanReport) -> bool:
    """Who pays for a review call: by evidence, not by label (ADR-0026)."""
    return bool(
        report.problems
        or report.signals
        or state.complexity == Complexity.COMPLEX
        or "preflight" in state.route
        or state.extra.get(REPLANNED_KEY)
        or state.failure_history
    )


async def cost_cap_block(ctx: EngineContext, state: StoryState, spent: float) -> StoryState:
    """The story spent its cap without finishing (finance/story_cap.py): it waits for the founder,
    and going on grants another cap from what it has spent now."""
    cap = ctx.config.budget.story_cap_usd
    state.extra[CAP_BASE_KEY] = spent
    ctx.emit("story.cost_cap", story_id=state.story_id, spent=round(spent, 4), cap=cap)
    return await block(
        ctx,
        state,
        BlockedReason.COST_CAP,
        f"story spent US$ {spent:.4f}; cap US$ {cap:.2f}",
        resume="dev",
        message=FounderMessage(
            factory=ctx.slug,
            story_id=state.story_id,
            kind=MessageKind.BLOCKED,
            sender="Finance Loompa",
            title=f"“{state.title}” já custou US$ {spent:.2f} sem terminar",
            context=(
                f"Esta entrega passou do teto de US$ {cap:.2f} por história. Quando isso "
                "acontece, em geral a abordagem não está funcionando, e seguir pode gastar mais "
                "sem resultado."
            ),
            impact="A entrega está pausada; as demais seguem normalmente.",
            options=[
                Option(key="continue", label=f"Continuar (libera mais US$ {cap:.2f})"),
                Option(key="skip", label="Deixar para depois (volta ao backlog)"),
                Option(key="drop", label="Cancelar esta entrega"),
            ],
        ),
    )


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


def _sync_refs(ctx: EngineContext, state: StoryState, wt: Worktree) -> list[str]:
    """What `node_dev` brings into the story's branch before it works again: the deliveries it was
    written on top of that are not in the base yet, and then the base. The branch the worktree
    started from is a snapshot of when it was created, so a fix the founder asked for in one of
    those deliveries only reaches this story through this list (ADR-0027)."""
    return sync_refs(
        rows_by_id(ctx.store, ctx.slug), state.story_id, ctx.worktrees.default_branch()
    )


def _worktree_base(ctx: EngineContext, state: StoryState) -> str:
    """The branch this story's worktree starts from: its integration branch when its pending
    deliveries needed one (ADR-0027), else the single pending delivery's branch (11.5), else ""
    for the base. Read from `state` and not from the row, because the integration is recorded in
    the same run that creates the worktree."""
    integration = state.extra.get(INTEGRATION_KEY) or {}
    branch = str(integration.get("branch") or "")
    return branch or base_branch_for(rows_by_id(ctx.store, ctx.slug), state.story_id)


def _ensure_worktree(ctx: EngineContext, state: StoryState) -> Worktree:
    if not ctx.worktrees.get(state.story_id) and not ctx.worktrees.head_is_valid():
        DeployerAgent(ctx).bootstrap_repo()
    existing = ctx.worktrees.get(state.story_id)
    on = _worktree_base(ctx, state)
    if existing is None and on:
        # a dependency whose delivery the founder has not accepted yet is not in the base: the
        # story starts from its branch, so it is written on top of that code (11.5)
        rows = rows_by_id(ctx.store, ctx.slug)
        # remembered: after the founder asks for changes the dependency stops being "awaiting",
        # but this story still carries its commits and must not merge before it (11.5). With an
        # integration branch the parents are already recorded in it, so there is no single one.
        dep = next(
            (
                d
                for d in deps_of(rows.get(state.story_id))
                if (rows.get(d) or {}).get("branch") == on
            ),
            "",
        )
        if dep:
            state.extra[BUILT_ON_KEY] = dep
        ctx.emit("story.built_on", story_id=state.story_id, branch=on, dependency=dep)
    wt = existing or ctx.worktrees.create(state.story_id, title=state.title, base=on or None)
    if existing is not None and on and wt.base != on:
        # `WorktreeManager.get` cannot know what a worktree was created from, and it answers with
        # the default branch. Everything that reads `wt.base` — the baseline the Inspector takes
        # on the base, a sync — would then look at a tree without the code this story was written
        # on top of.
        wt = replace(wt, base=on)
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
            # the last try was aborted; the same refs, so a parent's fixes are not dropped
            conflicts = deployer.sync_with_base(state, wt, _sync_refs(ctx, state, wt)) or []
            if not conflicts:
                return True
        await WorkerAgent(ctx, tier_override=tier).resolve_conflicts(
            state, wt, conflicts, left_before=left
        )
        left = deployer.git.has_conflict_markers(wt, conflicts)
        if deployer.finish_sync(state, wt, conflicts):
            return True
    return False


async def _align_once(ctx: EngineContext, state: StoryState, guidance: str) -> AgentResult:
    """The Product Owner aligns the criteria with the founder's request once per request. The
    runner checkpoints only when `dev` ends, so an engine restart mid-dev replayed the alignment:
    tamagotchi S-057 had its criteria rewritten eight times, each a little differently, and eight
    copies appended to its spec. The answer is kept in the store under the request and the
    criteria it was given, and a replay applies it as it was."""
    digest = hashlib.sha1("\n".join([guidance, *state.acceptance]).encode()).hexdigest()[:16]
    key = f"align:{ctx.slug}:{state.story_id}:{digest}"
    if saved := ctx.store.get(key):
        kept = json.loads(saved)
        state.acceptance = list(kept["acceptance"])
        return AgentResult(ok=kept["ok"], summary=kept["summary"], data=kept["data"])
    revised = await ProductOwnerAgent(ctx).align_criteria(state, guidance)
    if not (revised.data or {}).get("unavailable"):  # no answer: the next run asks again
        ctx.store.set(
            key,
            json.dumps(
                {
                    "acceptance": state.acceptance,
                    "ok": revised.ok,
                    "summary": revised.summary,
                    "data": revised.data or {},
                },
                ensure_ascii=False,
            ),
        )
    return revised


RECORDS_TASK_KEY = "records_task"  # a task that wrote under .loompa/ went back to the Architect
RECORDS_GUIDANCE = (
    "The Worker stopped because a task writes {paths}, under .loompa/. The factory keeps its "
    "records there itself (spec, plan, checklist, decisions) and leaves that folder out of the "
    "story's branch at merge, so that write can never be allowed, by anyone. Rewrite or drop the "
    "task so neither the product nor its tests need anything under .loompa/: a decision goes in "
    "`adr_proposal`; a test that reads a record there should test what the product does instead, "
    "or its criterion is traced to `review:`."
)


def _records(paths: list[str]) -> list[str]:
    return [p for p in paths if p.removeprefix("./").startswith(".loompa/")]


async def _drop_records_task(ctx: EngineContext, state: StoryState, res: AgentResult) -> bool:
    """The Worker stopped because its task writes the factory's records (ADR-0036 §6). The
    Architect would never open .loompa/ in the fence, and the founder was asked six times in 27
    minutes to "allow the decisions folder" on tamagotchi S-090, each yes changing nothing. The
    Architect rewrites the plan once with the reason; True when the Worker should run again."""
    refused = _records(list((res.data or {}).get("fence_refused") or []))
    if not refused or not res.blocked_reason or state.extra.get(RECORDS_TASK_KEY):
        return False
    state.extra[RECORDS_TASK_KEY] = refused
    try:
        await ArchitectAgent(ctx).amend(
            state, RECORDS_GUIDANCE.format(paths=", ".join(refused)), source="worker"
        )
    except LLMError as exc:
        ctx.emit("plan.amend_failed", story_id=state.story_id, error=str(exc)[:200])
        return False
    ctx.emit("plan.records_task", story_id=state.story_id, paths=refused)
    return True


async def _widen_fence(ctx: EngineContext, state: StoryState, res: AgentResult) -> bool:
    """The Worker stopped because the plan's fence refused a file (contas Sprint 2, S-047: task 4
    needed cli.py, the plan left it out, and it became a finding for the founder). The Architect,
    who owns the plan, reviews it once per story with the Worker's reason; True when it opened a
    file the Worker asked for, and the Worker runs again."""
    refused = [
        f
        for f in (res.data or {}).get("fence_refused") or []
        if f not in state.allowed_paths and not _records([f])  # never opened: _drop_records_task
    ]
    if (
        not refused
        or not (res.blocked_reason or unfinished(res))
        or state.extra.get(FENCE_AMEND_KEY)
    ):
        return False
    state.extra[FENCE_AMEND_KEY] = refused
    reason = res.blocked_reason or res.summary or ""
    guidance = (
        f"The plan's paths refused these files: {', '.join(refused)}. "
        f"What the Worker said when it stopped: {reason[:1500]}"
    )
    try:
        await ArchitectAgent(ctx).amend(state, guidance, source="worker")
    except LLMError as exc:
        ctx.emit("plan.amend_failed", story_id=state.story_id, error=str(exc)[:200])
        return False
    granted = [
        f
        for f in refused
        if any(f == p or f.startswith(p.rstrip("/") + "/") for p in state.allowed_paths)
    ]
    ctx.emit("plan.fence_review", story_id=state.story_id, refused=refused, granted=granted)
    if not granted:
        return False
    # the Worker's notes about a file it can now change are no longer findings
    state.learnings = [
        item
        for item in state.learnings
        if item.get("_captured")
        or not any(f in f"{item.get('title')} {item.get('detail')}" for f in granted)
    ]
    return True


async def _red_suite(ctx: EngineContext, wt: Worktree) -> str:
    """The suite's summary in the story's worktree when it is red, for an amendment to see every
    test the change breaks at once (12.1, S-061); "" when green or not runnable."""
    if ctx.dry_run:
        return ""
    try:
        out = (await ctx.aci_for(wt.path).call("run_tests", {})).output
    except Exception:  # noqa: BLE001 - only context for the Architect
        return ""
    return out if "FAIL" in out[:200] else ""


async def node_dev(ctx: EngineContext, state: StoryState) -> StoryState:
    # A worktree that cannot be prepared raises: the Ops Loompa tries again (three times, ADR-0016
    # §5) before the founder hears of it, as with any other failure of a step.
    rows = rows_by_id(ctx.store, ctx.slug)
    if moving := parent_in_rework(rows, state.story_id):
        # parked in `dev` until the delivery it stands on is ready again; the plan gate decides
        # when (it waits for a dependency that is neither done nor ready to merge)
        state.extra[WAIT_KEY] = {
            "gate": "wait",
            "pending": [moving],
            "founder": [],
            "gone": [],
            "chain": [],
            "unmerged": [],
            "label": f"aguardando {moving}, que voltou para ajustes",
        }
        ctx.emit("story.waiting", story_id=state.story_id, pending=[moving], parent_rework=True)
        return state
    wt = _ensure_worktree(ctx, state)
    if "baseline" not in state.extra:
        if ctx.worktrees.changed_files(wt):
            # resumed mid-node (engine restart): the worktree already holds the story's own
            # work, whose reproduction tests would count as "already failing on the base"
            with ctx.worktrees.base_checkout(wt.base) as base_path:
                state.extra["baseline"] = await InspectorAgent(ctx).baseline(
                    state, wt, at=base_path
                )
        else:
            state.extra["baseline"] = await InspectorAgent(ctx).baseline(state, wt)
    elif (
        conflicts := DeployerAgent(ctx).sync_with_base(state, wt, _sync_refs(ctx, state, wt))
    ) is not None:
        merged = not conflicts or await _resolve_with_retries(ctx, state, wt, conflicts)
        if merged:
            # the base moved under a story going back to work: "already failing" moved too
            with ctx.worktrees.base_checkout(wt.base) as base_path:
                state.extra["baseline"] = await InspectorAgent(ctx).baseline(
                    state, wt, at=base_path
                )
    if align := state.extra.pop(ALIGN_KEY, None):
        revised = await _align_once(ctx, state, align)
        if revised.ok:
            log_ = state.extra.setdefault(CRITERIA_ALIGNED_KEY, [])
            log_.extend((revised.data or {}).get("changes") or [])
            state.failure_history.append(revised.summary)
    guidance = state.extra.pop(AMEND_KEY, None)
    if guidance:
        try:
            amended = await ArchitectAgent(ctx).amend(
                state, guidance, suite=await _red_suite(ctx, wt)
            )
        except LLMError as exc:  # the Worker still gets the guidance as a note
            ctx.emit("plan.amend_failed", story_id=state.story_id, error=str(exc)[:200])
        else:
            if (
                (amended.data or {}).get("dropped")
                and not state.commits
                and _open_task(ctx, state) is None
            ):
                # the guidance withdrew every step left and nothing was built: what the founder
                # said is "already resolved", checked and closed by `resolve` (ADR-0037 §3)
                state.extra[RESOLVE_KEY] = {"why": guidance, "resume": "dev"}
                return goto(state, "resolve")
    if (spent := over_cap(ctx, state)) is not None:
        return await cost_cap_block(ctx, state, spent)
    tier = "tier1" if state.current_tier == "tier1" else None
    opencode = ctx.config.worker.backend == "opencode"
    worker_cls = OpenCodeWorker if opencode else WorkerAgent
    label = " ".join(
        p for p in ("Worker Loompa", "Sr" if tier else "", "(OpenCode)" if opencode else "") if p
    )
    worker = worker_cls(ctx, name=label, tier_override=tier)
    res = await worker.run(state, wt)
    if await _widen_fence(ctx, state, res) or await _drop_records_task(ctx, state, res):
        res = await worker.run(state, wt)
    await KaizenAgent(ctx).capture(state)
    if (res.data or {}).get("cost_cap"):
        return await cost_cap_block(ctx, state, float(res.data["cost_cap"]))
    if res.blocked_reason:
        refused = sorted((res.data or {}).get("fence_refused") or [])
        return await block(
            ctx,
            state,
            BlockedReason.QUESTION,
            res.blocked_reason,
            options=res.blocked_options,
            resume="dev",
            cause=f"fence refused: {', '.join(refused)}" if refused else None,
            # a write under .loompa/ is never the founder's to allow: no option may offer it
            message=_records_block_message(ctx, state) if _records(refused) else None,
        )
    if unfinished(res):
        ctx.emit(
            "story.task_unfinished",
            story_id=state.story_id,
            task=(res.data or {}).get("unfinished"),
            ended_by=(res.data or {}).get("ended_by"),
        )
        return await climb(ctx, state, res.summary, executive=UNFINISHED_EXECUTIVE, task_level=True)
    return advance(state)


async def node_test(ctx: EngineContext, state: StoryState) -> StoryState:
    """Graded quality gate: PASS / CONCERNS move on, FAIL climbs the escalation ladder,
    WAIVED asks the founder (serious finding the tests do not catch)."""
    wt = _ensure_worktree(ctx, state)
    DeployerAgent(ctx).as_it_lands(state, wt)  # judged as it will be merged (ADR-0037 §5)
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
    judged = [c for c in (res.data or {}).get("failed_criteria") or [] if c.get("text")]
    if judged and not (res.data or {}).get("failing"):
        texts = sorted(" ".join(c["text"].split()).lower()[:160] for c in judged)
        again = state.extra.get(JUDGED_FAILURES_KEY) == texts
        state.extra[JUDGED_FAILURES_KEY] = texts
        if again and state.acceptance and not state.extra.get(JUDGED_REVIEW_KEY):
            # The Inspector failed the same criteria twice with the tests green: before a stronger
            # model is paid to meet it, the Product Owner looks at the criterion itself.
            review = await ProductOwnerAgent(ctx).review_judged_criteria(state, judged)
            state.extra[JUDGED_REVIEW_KEY] = True
            if review.ok:
                state.extra.setdefault(CRITERIA_ALIGNED_KEY, []).extend(
                    (review.data or {}).get("changes") or []
                )
                state.failure_history.append(review.summary)
                state.extra.pop(JUDGED_FAILURES_KEY, None)
                return goto(state, "dev")
    else:
        state.extra.pop(JUDGED_FAILURES_KEY, None)
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
JUDGED_FAILURES_KEY = "judged_failures"  # the criteria the Inspector failed at the last run
JUDGED_REVIEW_KEY = "judged_review"  # the Product Owner reviewed them once already
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


def _fresh_branch(ctx: EngineContext, state: StoryState, label: str) -> str:
    """Put the story's branch back where it left the base, the attempt kept under
    `refs/loompa/attempts/<label>`: what comes next is not built on work done for another plan or
    other criteria. The kept ref, or "" when the branch had nothing of its own."""
    kept = ""
    if (wt := ctx.worktrees.get(state.story_id)) is not None:
        try:
            kept = ctx.worktrees.as_role("deployer").keep_attempt_and_reset(wt, label)
        except GitError as exc:
            ctx.emit("story.reset_failed", story_id=state.story_id, error=str(exc)[:200])
    if kept:
        state.commits = []
        state.tasks_done = []
        state.extra.pop("baseline", None)
    return kept


async def climb(
    ctx: EngineContext,
    state: StoryState,
    failure: str,
    *,
    executive: str,
    task_level: bool = False,
) -> StoryState:
    """A failed attempt climbs the escalation ladder: tier 2 once more, then tier 1 twice (re-planned
    on the way up), then the founder — three tries after the first failure (ADR-0016 §5), each
    with the failure before it in hand. Failed means the Inspector's FAIL, or a Worker that could not finish a task
    (Fase 8.5) — which never costs an Inspector run on a half-built story.

    `task_level`: the Worker could not finish one task (a loop, the call limit). The plan is not
    the suspect, so tier 1 takes over that task on the same branch, keeping the tasks done: in
    tamagotchi SP-013, 7 of 9 stories were re-planned from a clean branch after one task looped,
    discarding US$ 1.59 and ~9.4 h of good work (ADR-0037 §6)."""
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
            **({"task_level": True} if task_level else {}),
        )
        if task_level:
            return goto(state, "dev")
        if "plan" in state.route and not state.extra.get(REPLANNED_KEY):
            # Two failed attempts may mean the plan, not the code: its `files` fence the Worker
            # away from the real cause (`contas` S-005 could not touch the module that broke the
            # test). Once per story, the Architect re-plans with the failures in hand.
            state.extra[REPLANNED_KEY] = True
            state.plan_review_rounds = 0  # the re-plan gets its own gate round (ADR-0026)
            # and a clean branch: the new plan is not built on the old plan's commits (12.1)
            kept = _fresh_branch(ctx, state, f"{state.story_id}-{state.attempts_tier2}")
            ctx.emit("story.replanned", story_id=state.story_id, kept=kept or None)
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


# Why an approved story waits in the merge queue instead of merging on the spot
QUEUE_AHEAD = "approved stories ahead of it are waiting for their merge"
BASE_MOVED = "the base moved since the delivery"
INTEGRATING = "another approved story is being merged"

# how `_diagnose_merge` opens its block: a "try again" on it is still an approved story
MERGE_FAILED_TECHNICAL = "Approved, but merging into the base failed"

MERGE_FAILED_EXECUTIVE = (
    "Você aprovou a entrega, mas não consegui integrá-la à versão principal do produto. "
    "{tried}Preferi pedir sua orientação em vez de arriscar mexer em algo que é seu."
)


async def _bring_up_to_date(ctx: EngineContext, state: StoryState, wt: Worktree) -> str | None:
    """Merge the base into an approved story and re-run the checks that need no model. The
    founder accepted the behaviour on the base it was delivered on; only the combination with
    what landed since is new, so a conflict goes to the Worker (as in `dev`) and the suite and the
    smoke run again — no judge. None: ready to merge (or already up to date). Otherwise why not,
    in English, for the log and the Worker."""
    deployer = DeployerAgent(ctx)
    conflicts = deployer.sync_with_base(state, wt, _sync_refs(ctx, state, wt))
    if conflicts is None:
        return None
    if conflicts and not await _resolve_with_retries(ctx, state, wt, conflicts):
        return "merging the base into the approved story left conflicts unresolved in: " + (
            ", ".join(conflicts[:10])
        )
    ctx.emit("story.brought_up_to_date", story_id=state.story_id, conflicts=len(conflicts))
    # "already failing" moved with the base, as when a story goes back to work
    with ctx.worktrees.base_checkout(wt.base) as base_path:
        state.extra["baseline"] = await InspectorAgent(ctx).baseline(state, wt, at=base_path)
    DeployerAgent(ctx).as_it_lands(state, wt)
    res = await InspectorAgent(ctx).run(state, wt, judge=False)
    if not res.ok:
        return "with the base merged in, the checks failed:\n" + res.summary[-1500:]
    return None


async def _merge_after_approval(
    ctx: EngineContext, state: StoryState, wt: Worktree, error: str
) -> StoryState:
    """The head of the merge queue (`engine/merge_queue.py`): an approved story that could not
    go straight into the base — others were queued ahead of it, the base moved since the
    delivery, another merge was running, or the merge failed. The Scheduler runs one queued story
    at a time, in the order approved, and the whole integration holds the merge lock, so nothing
    reaches the base between its checks and its merge (tamagotchi SP-008 had two interleaved,
    each aborting the other's merge in the main checkout).

    First the path that needs no judgement: the base is merged into the story, conflicts go to
    the Worker, the checks run again, and the story is merged. A combination that fails its
    checks goes back to `dev` like a delivery whose rebase conflicted (the founder then sees the
    new delivery); conflicts the Worker could not resolve reach the founder as a conflict. Only a
    git failure that is not a conflict (a lock, a checkout in the way) goes to the Deployer's
    diagnosis, which picks one of GIT_ACTIONS, three tries at most (ADR-0016 §5)."""
    async with MergeLock.for_repo(ctx.worktrees):
        deployer = DeployerAgent(ctx)
        for _ in range(ctx.config.schedule.ops_max_recoveries):
            problem = await _bring_up_to_date(ctx, state, wt)
            if problem:
                dequeue(state.extra)
                return await _approved_back_to_work(ctx, state, problem)
            if ctx.worktrees.behind_base(wt):
                # the last word before the merge: what goes in is exactly what was checked
                continue
            try:
                deployer.merge(state, wt)
            except MergeConflict as exc:  # the base moved again under the checks
                error = str(exc)
                continue
            except GitError as exc:
                error = str(exc)[-2000:]
                break
            return _merged(state)
        return await _diagnose_merge(ctx, state, wt, error)


def _merged(state: StoryState) -> StoryState:
    dequeue(state.extra)
    state.extra.pop(CONFLICT_RETRIES_KEY, None)
    state.stage = Stage.DONE
    state.phase = ""
    return state


async def _approved_back_to_work(
    ctx: EngineContext, state: StoryState, problem: str, *, after: str = "approval"
) -> StoryState:
    """An approved story that does not hold up on the current base: its code must change, so it
    is delivered again rather than merged on the strength of an approval of other code. The same
    for a story about to be delivered (`after="delivery"`)."""
    unresolved = problem.startswith("merging the base")
    if (
        not unresolved
        and "dev" in state.route
        and state.extra.get(CONFLICT_RETRIES_KEY, 0) < ctx.config.schedule.ops_max_recoveries
    ):
        state.extra[CONFLICT_RETRIES_KEY] = state.extra.get(CONFLICT_RETRIES_KEY, 0) + 1
        state.failure_history.append(problem)
        ctx.emit("story.reintegrating", story_id=state.story_id, after=after)
        return goto(state, "dev")
    return await block(ctx, state, BlockedReason.CONFLICT, problem, resume="review")


async def _diagnose_merge(
    ctx: EngineContext, state: StoryState, wt: Worktree, error: str
) -> StoryState:
    """A merge into the base that failed for a reason other than a conflict: the Deployer
    diagnoses it and the code runs the action it picks (GIT_ACTIONS), three tries at most — the
    first on its tier, the others on the tier above — before the founder hears of it."""
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
            elif action == "resync" and (problem := await _bring_up_to_date(ctx, state, wt)):
                error = problem
                continue
            deployer.merge(state, wt)
        except GitError as exc:
            error = str(exc)[-2000:]
            continue
        return _merged(state)
    said = (
        f"Tentei resolver sozinho {tried} vez(es)"
        + (", incluindo com um modelo de IA mais forte. " if tried >= 2 else ". ")
        if tried
        else ""
    )
    dequeue(state.extra)
    return await block(
        ctx,
        state,
        BlockedReason.PERSISTENT_FAILURE,
        f"{MERGE_FAILED_TECHNICAL}: {error}",
        resume="review",
        executive=MERGE_FAILED_EXECUTIVE.format(tried=said),
    )


async def node_review(ctx: EngineContext, state: StoryState) -> StoryState:
    # the founder's acceptance still holds the merge: delivering this story now would carry a
    # delivery they have not accepted into the base with it (11.5). Waiting is time, not a
    # decision, so nothing reaches the inbox: the story parks and the Scheduler frees its slot.
    rows = rows_by_id(ctx.store, ctx.slug)
    if gone := gone_under(rows, state.story_id):
        # the story it was written on top of left the sprint: its code never reaches the base, so
        # delivering this one as it stands would carry code nobody wants (11.5)
        title = (rows.get(gone) or {}).get("title", gone)
        return await block(
            ctx,
            state,
            BlockedReason.DEPENDENCY,
            f"built on {gone}, which was cancelled or sent back to the backlog",
            resume="review",
            message=FounderMessage(
                factory=ctx.slug,
                story_id=state.story_id,
                kind=MessageKind.BLOCKED,
                sender=MasterAgent.display,
                title=f"“{state.title}” foi construída em cima de “{title}”, que saiu do sprint",
                context=(
                    "O trabalho desta história está pronto, mas ele foi escrito em cima da outra: "
                    "entregá-lo agora levaria junto o código que você decidiu não usar."
                ),
                impact="Nada foi integrado ao produto. As demais frentes seguem normalmente.",
                options=[
                    Option(
                        key="detach",
                        label="Refazer esta história sem a que saiu",
                        recommended=True,
                    ),
                    Option(key="skip", label="Devolver ao backlog"),
                ],
            ),
        )
    waits = merge_gate(rows, state.story_id)
    if waits:
        first = WAIT_KEY not in state.extra
        state.extra[WAIT_KEY] = merge_parked_note(
            waits, f"pronta · aguardando o seu aceite de {', '.join(waits)}"
        )
        state.phase = "review"  # where it resumes when the dependency is merged
        if first:
            ctx.emit("story.waiting", story_id=state.story_id, pending=waits, stage="review")
        return state
    wt = _ensure_worktree(ctx, state)
    # read, not popped: the runner checkpoints when the node ends, and a restart in the middle of
    # the integration must come back to it, not deliver the approved story again
    if entry := queued(state.extra):
        return await _merge_after_approval(ctx, state, wt, str(entry.get("why") or ""))
    await KaizenAgent(ctx).capture(
        state
    )  # findings never get lost: sweep what no earlier phase filed
    if ctx.worktrees.behind_base(wt):
        # The base moved since the story was tested. A rebase here used to deliver a combination
        # nobody had run, and the approval then merged it on the spot because the branch already
        # held the base (tamagotchi S-070 broke S-077's offline cache, 08/10). The base goes in
        # the way the merge queue does it, checks and all (ADR-0036 §3).
        if problem := await _bring_up_to_date(ctx, state, wt):
            return await _approved_back_to_work(ctx, state, problem, after="delivery")
    # no rebase: a base that moves after this is caught by the approval (`_merge_now`)
    res = await DeployerAgent(ctx).run(state, wt, rebase=False)
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


def close_resolved(
    ctx: EngineContext, state: StoryState, why: str, *, by: list[str] | None = None
) -> StoryState:
    """End a story without a delivery because what it asks already holds (ADR-0037 §1). It is
    cancelled with an outcome that says so (the sprint report reads it as resolved, not as
    missing work); its worktree goes, its branch and attempts stay for analysis."""
    ProductOwnerAgent(ctx).set_status(state.story_id, Stage.CANCELLED)
    state.extra.pop(RESOLVE_KEY, None)
    state.extra[OUTCOME_KEY] = {
        "kind": "resolved",
        "reason": (why or "")[:500],
        "by": list(by or []),
        "at": now_iso(),
    }
    state.stage = Stage.CANCELLED
    state.phase = ""
    state.blocked_reason = None
    state.blocked_message_id = None
    state.resume_stage = None
    state.resume_phase = None
    ctx.store.update_story(state.story_id, state=state.model_dump(mode="json"))
    ctx.worktrees.remove(state.story_id)
    forget_resolved(ctx, state.story_id)
    ctx.emit("story.resolved", story_id=state.story_id, by=list(by or []), reason=(why or "")[:300])
    return state


async def node_resolve(ctx: EngineContext, state: StoryState) -> StoryState:
    """Close a story whose goal already holds (ADR-0037 §1): the founder chose "already resolved",
    or the line watch saw the failures a line card was opened for leave the base. What can be
    checked is checked first: those failures run on the base, and while any still fails the
    founder hears it, instead of a card closed over a red base."""
    ask = state.extra.get(RESOLVE_KEY) or {}
    targets = line_targets(ctx, state.story_id)
    if targets:
        with ctx.worktrees.base_checkout(ctx.worktrees.default_branch()) as path:
            checks = await InspectorAgent(ctx).checks_at(path, lint=False)
        still = sorted(set(targets) & set(checks["failing"]))
        if still:
            state.extra.pop(RESOLVE_KEY, None)
            n = len(still)
            msg = compose_blocked_message(
                factory=ctx.slug,
                story_id=state.story_id,
                story_title=state.title,
                reason=(
                    f"A história foi encerrada como já resolvida, mas {n} verificação(ões) "
                    "automática(s) que a motivaram ainda falham na versão principal do produto."
                ),
                impact="Nada foi encerrado. As demais entregas seguem normalmente.",
                options=executable_options(["Seguir com o conserto dessas verificações"]),
            )
            msg.sender = MasterAgent.display
            msg.title = f"“{state.title}” ainda não está resolvida"
            return await block(
                ctx,
                state,
                BlockedReason.QUESTION,
                "asked to close as resolved, still failing on the base: " + "; ".join(still[:8]),
                resume=str(ask.get("resume") or "dev"),
                message=msg,
            )
    return close_resolved(
        ctx,
        state,
        str(ask.get("why") or "encerrada como já resolvida"),
        by=list(ask.get("by") or []),
    )


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
        "plan_review",
        node_plan_review,
        Stage.PLAN,
        owner="product_owner",
        description="coverage gate over plan and tasks: checks in code, review by evidence",
    )
)
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
        "resolve",
        node_resolve,
        Stage.TEST,
        owner="inspector",
        description="close a story whose goal already holds, after checking it on the base",
    )
)
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
    state.extra.pop(WAIT_KEY, None)
    state.blocked_reason = None
    state.blocked_message_id = None
    state.resume_stage = None
    state.resume_phase = None


def _drop_gone_dependencies(ctx: EngineContext, state: StoryState) -> list[tuple[str, str]]:
    """Forget the dependencies that were cancelled or went back to the backlog; returns them
    as `(id, title)`. The relation is the Product Owner's to write (ADR-0021)."""
    rows = rows_by_id(ctx.store, ctx.slug)
    gone = plan_gate(rows, state.story_id).gone
    if gone:
        keep = [d for d in deps_of(rows.get(state.story_id)) if d not in gone]
        ProductOwnerAgent(ctx).set_dependencies(state.story_id, keep)
    return [(d, (rows.get(d) or {}).get("title", d)) for d in gone]


def _merge_now(ctx: EngineContext, state: StoryState, wt: Worktree) -> str | None:
    """Merge an approved story on the spot when that cannot jump the queue or skip a check: no
    approved story is waiting ahead of it, its branch already holds the base, and nobody else is
    touching the base. None when merged; otherwise why it goes to the merge queue instead.
    Runs in whichever process took the answer (the dashboard, the CLI, the engine)."""
    if merge_queue(rows_by_id(ctx.store, ctx.slug)):
        return QUEUE_AHEAD
    lock = MergeLock.for_repo(ctx.worktrees)
    if not lock.try_acquire():
        return INTEGRATING
    try:
        if ctx.worktrees.behind_base(wt):
            # what was approved never ran next to what landed since: the queue merges the base
            # in and runs the checks before the merge (tamagotchi SP-008)
            return BASE_MOVED
        DeployerAgent(ctx).merge(state, wt)
    except GitError as exc:
        return str(exc)[-2000:]
    finally:
        lock.release()
    return None


def _back_to_spec(ctx: EngineContext, state: StoryState) -> None:
    """The founder asked for the requirements to be rewritten (ADR-0037 §1): the spec is written
    again with their guidance, then planned and built again, from a clean branch. The amendment
    path could only add tasks to a plan written for the old criteria (tamagotchi S-094)."""
    _fresh_branch(ctx, state, f"{state.story_id}-respec-{len(state.founder_notes)}")
    state.spec_ready = False
    state.plan_ready = False
    state.spec_review_rounds = 0
    state.plan_review_rounds = 0
    state.tasks_done = []
    state.extra.pop(AMEND_KEY, None)
    ctx.emit("story.respec", story_id=state.story_id)
    goto(state, "spec")


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
        if reason == BlockedReason.DEPENDENCY:
            _drop_gone_dependencies(ctx, state)
        back_to_backlog(ctx, state, priority=SKIPPED_PRIORITY)
    elif reason == BlockedReason.INTEGRATION:
        # "wait": the founder accepts one of the deliveries, and the story goes back to the gate,
        # which parks it until then; with one of them merged, it starts from the other's branch
        state.extra.pop(INTEGRATION_KEY, None)
        goto(state, resume or "plan")
    elif reason == BlockedReason.DEPENDENCY:  # "detach": go on without what left the sprint
        for dep, title in _drop_gone_dependencies(ctx, state):
            state.note(
                f"A história {dep} (“{title}”), da qual esta dependia, saiu do sprint: a "
                "especificação deve seguir sem ela."
            )
        state.spec_ready = False
        state.spec_review_rounds = 0
        goto(state, "spec")
    elif reason == BlockedReason.DELIVERY:
        if key in ("approve", "approved", "ok", "yes", "sim"):
            if answer.text:
                # what the founder wrote on approving (an agreement, a part moved to another
                # story) reaches the stories built on this one: tamagotchi S-017 was approved with
                # "reabrir_app goes to S-018", and S-018 never heard of it
                state.note(f"Ao aprovar: {answer.text.strip()}")
            if waits := merge_gate(rows_by_id(ctx.store, ctx.slug), state.story_id):
                # belt and braces: `review` parks a story whose dependency is not in the base, so
                # this delivery should not exist. If one ever does, the approval is kept and the
                # merge waits for the dependency instead of carrying it along (11.5).
                state.extra[WAIT_KEY] = merge_parked_note(
                    waits, f"aprovada · aguardando a entrega de {', '.join(waits)}"
                )
                state.blocked_reason = None
                state.stage = Stage.REVIEW
                state.phase = "review"
                ctx.emit("story.waiting", story_id=state.story_id, pending=waits, stage="review")
                return state
            wt = ctx.worktrees.get(state.story_id)
            if wt is not None and (why := _merge_now(ctx, state, wt)):
                # approved: it waits for its turn in the merge queue, and `review` integrates and
                # merges it without asking again (ADR-0016 §5, ADR-0029)
                seq = enqueue(state.extra, why)
                goto(state, "review")
                state.blocked_reason = None
                ctx.emit(
                    "merge.queued",
                    story_id=state.story_id,
                    seq=seq,
                    why=why[:200],
                    ahead=len(merge_queue(rows_by_id(ctx.store, ctx.slug))),
                )
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
            if guidance and state.acceptance:
                state.extra[ALIGN_KEY] = guidance
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
        last = str((state.extra.get(LAST_BLOCK_KEY) or {}).get("fp") or "")
        if key == "retry" and resume == "review" and MERGE_FAILED_TECHNICAL.lower() in last:
            # the delivery was approved before the merge failed: back in the merge queue, not
            # delivered for a second approval (tamagotchi S-053, S-054)
            enqueue(state.extra, "the founder asked to try the merge again")
        if note and state.phase == "dev" and "plan" in state.route:
            # what the founder wrote may withdraw part of the plan: the Architect amends the
            # tasks, or the Worker's self-check keeps asking for it (`contas` S-030)
            state.extra[AMEND_KEY] = note
    elif reason in (BlockedReason.COST_CAP, BlockedReason.STACK):
        # the decision itself is the answer; only what the founder wrote is guidance
        if answer.text and answer.text.strip():
            state.note(answer.text.strip())
        if reason == BlockedReason.STACK:
            state.extra[STACK_KEY] = "accepted"
        goto(state, resume or "dev")
    elif reason == BlockedReason.CONFLICT:
        # back through `dev`: the base is merged in there and conflicts go to the Worker;
        # retrying the delivery's rebase alone only ever met the same conflict again
        goto(state, "dev" if "dev" in state.route else "review")
    elif reason in DESIGN_REASONS:  # the design gate (ADR-0039)
        apply_design_answer(ctx, state, msg, answer, key, guidance)
    elif action_of(key) == RESOLVED:  # QUESTION: "it is already resolved" (ADR-0037 §1)
        # checked and closed by `resolve`, which runs the failures the story was opened for
        state.extra[RESOLVE_KEY] = {"why": guidance, "resume": resume or "dev"}
        goto(state, "resolve")
    elif action_of(key) == RESPEC:  # QUESTION: "rewrite the requirements first"
        if guidance:
            state.note(guidance)
        _back_to_spec(ctx, state)
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


# the design gate's phases register themselves; imported last, it builds on `block` above
from loompa.engine.design_gate import (  # noqa: E402
    DESIGN_KEY,
    DESIGN_REASONS,
    apply_design_answer,
    gated,
    opens_area,
)
