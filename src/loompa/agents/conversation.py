"""Running a chat turn, and the facade the CLI and the dashboard talk to (ADR-0010).

`run_turn` is the one place a founder message becomes a model call: it records the message
first (a crash never loses it), builds the prompt from the draft plus the last turns, asks the
agent for `{reply, ops, questions}`, applies the ops in code and records the answer. The agent
(the Master, in a Sprint Meeting and in a brainstorm) only brings its prompt and its tools.
`Conversations` opens, continues, edits and commits sessions; committing always goes through the
Product Owner, so a conversation never writes a card by itself. It is also the dashboard's quick
story door (ADR-0017): the Product Owner files the request or opens a review conversation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loompa.agents.base import LoompaAgent
from loompa.backlog import Triage
from loompa.comms import audit_executive_text, sanitize_for_founder
from loompa.conversations import (
    Conversation,
    ConversationBoard,
    ConversationError,
    ConversationKind,
    ConversationStatus,
    DraftItem,
    MeetingMode,
    OpsReport,
    Turn,
    apply_ops,
    render_backlog,
    render_brainstorm,
    render_draft,
    render_transcript,
    to_scale,
)
from loompa.engine.state import Stage
from loompa.sprints import SprintBoard

if TYPE_CHECKING:
    from loompa.agents.toolbox import Toolbox
    from loompa.conversations import CommitResult
    from loompa.engine.context import EngineContext

log = logging.getLogger(__name__)

AT_WORK = (Stage.SPEC, Stage.PLAN, Stage.DEV, Stage.TEST, Stage.REVIEW)  # a runner may hold it

MAX_REPLY_CHARS = 2400
CHAT_TOOL_ROUNDS = 4  # a chat turn should answer quickly; research is what takes many rounds
# On a reasoning model the output budget pays for the thinking and the answer both, and a chat
# turn keeps a small one. Left alone, a model that reasons at full effort (GLM 5.3, Qwen Max and
# most of the frontier make it mandatory) spends the whole budget thinking and returns nothing.
# Drafting a card from what the founder just said is not the work that needs deep thought.
CHAT_REASONING_EFFORT = "low"
FAILURE_REPLY = (
    "Não consegui responder agora. Sua mensagem ficou registrada; tente de novo em instantes."
)

TURN_CONTRACT = """
Respond with JSON only:
{{"reply": str, "ops": [op, ...], "questions": [str]}}
`reply` is what the founder reads: plain {language}, at most five short sentences, no file names,
code, stack traces or error names. `ops` edit the DRAFT of the backlog and of the sprint; they are
proposals the founder can still change, and nothing reaches the backlog until the founder commits.
Never say a card was created or a sprint started. Operations:
- {{"op": "add", "title": str, "description": str, "epic": str, "priority": 1-5, "in_sprint": bool}}
- {{"op": "update", "ref": "D1" or "S-004", "title"?: str, "description"?: str, "epic"?: str,
   "priority"?: 1-5, "in_sprint"?: bool, "pinned"?: false}}  (a ref to a backlog card pulls it into
   the draft; for a card that already exists only `priority` and `in_sprint` can change, and
   `"pinned": false` hands a card the founder pinned by dragging back to the Product Owner's
   ranking, only when the founder asks for it)
- {{"op": "drop", "ref": "D1"}}  (takes a card out of the DRAFT only; the backlog keeps it)
- {{"op": "retire", "ref": "S-010", "reason": str}}  (a backlog card the founder calls obsolete or
   unwanted leaves the backlog for good when the meeting is applied; only when the founder asks;
   {{"op": "unretire", "ref": "S-010"}} undoes it)
- {{"op": "goal", "text": str}}  (the sprint goal, one sentence)
`priority` is 1 (urgent) to 5 (nice to have). `questions` repeats any question you asked in the
reply (at most two), or is empty. Return "ops": [] when the message needs no change to the draft.
The reply and the ops must say the same thing: every story you describe in `reply` needs its own
`add` op in the same answer. A `goal` op sets the sprint goal and never creates a story, so a reply
that says you prepared the work, with no `add` next to it, is wrong.
"""


@dataclass
class TurnResult:
    reply: str
    changes: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)
    failed: bool = False
    consult: bool = False  # the Master asked the Product Owner for the sprint proposal
    extra: dict[str, Any] = field(default_factory=dict)  # the rest of the model's answer


def founder_text(text: str, limit: int = MAX_REPLY_CHARS) -> str:
    """A chat reply is founder-facing: hold it to the executive-language rule."""
    text = (text or "").strip()[:limit]
    return sanitize_for_founder(text, max_chars=limit) if audit_executive_text(text) else text


def parse_turn(data: dict[str, Any]) -> tuple[str, list[Any], list[str]]:
    """`(reply, ops, questions)` from a model answer. Tolerates the older meeting shape
    (`stories`, `clarifications`) that smaller models still produce."""
    reply = str(data.get("reply") or data.get("message") or "").strip()
    ops = data.get("ops")
    ops = list(ops) if isinstance(ops, list) else []
    stories = data.get("stories")
    if isinstance(stories, list):
        ops += [{"op": "add", **s} for s in stories if isinstance(s, dict)]
    raw_questions = data.get("questions") or data.get("clarifications") or []
    if isinstance(raw_questions, str):
        raw_questions = [raw_questions]
    questions = [str(q).strip() for q in raw_questions if str(q).strip()][:2]
    return reply, ops, questions


async def run_turn(
    agent: LoompaAgent,
    board: ConversationBoard,
    conv: Conversation,
    text: str,
    *,
    system: str,
    context: str,
    toolbox: Toolbox,
    origin: str = "founder",
    fallback_ops: Callable[[str], list[dict[str, Any]]] | None = None,
    polish: Callable[[str], str] | None = None,
    rounds: int | None = None,
    max_tokens: int = 1800,
    contract: str = TURN_CONTRACT,
) -> TurnResult:
    text = text.strip()
    if not text:
        raise ConversationError("escreva uma mensagem")
    history = render_transcript(conv)  # everything said before this message
    conv.turns.append(Turn(who="founder", text=text))
    if not conv.title:
        conv.title = (sanitize_for_founder(text, max_chars=80).splitlines() or [""])[0]
    board.save(conv)  # the message is safe even if the model call fails

    brainstorm = conv.kind == ConversationKind.BRAINSTORM
    draft = render_brainstorm(conv.draft) if brainstorm else render_draft(conv.draft)
    user = (
        f"{context}\n\n## Backlog\n{render_backlog(board.cards(), delivered=board.delivered())}\n\n"
        f"## Current draft\n{draft}\n\n"
        f"## Conversation so far\n{history}\n\n## Founder's message\n{text}"
    )
    if rounds is None:
        rounds = min(agent.ctx.config.schedule.agent_tool_iterations, CHAT_TOOL_ROUNDS)
    ops: list[Any] = []
    try:
        data = await agent.ask_json_with_tools(
            system.format(language=agent.language) + contract.format(language=agent.language),
            user,
            toolbox,
            max_iterations=rounds,
            max_tokens=max_tokens,
            reasoning_effort=CHAT_REASONING_EFFORT,
        )
    except Exception as exc:  # noqa: BLE001 - the founder gets a plain note, Ops gets the detail
        # The traceback goes to the log, never to the event: `/api/factories/{slug}/events` is
        # read by the dashboard, and a bare message ("'list' object has no attribute 'get'")
        # is unreadable without it.
        log.exception("conversation %s failed in a turn", conv.id)
        agent.ctx.emit(
            "conversation.error",
            agent=agent.name,
            conversation_id=conv.id,
            error=f"{type(exc).__name__}: {exc}"[:300],
        )
        reply, questions, failed, consult, extra = FAILURE_REPLY, [], True, False, {}
        if fallback_ops is not None and not conv.draft.items:
            ops = fallback_ops(text)  # keep the day moving: a deterministic split of the goals
    else:
        reply, ops, questions = parse_turn(data)
        failed, consult = False, data.get("consult_po") is True
        extra = {k: v for k, v in data.items() if k not in ("reply", "ops", "questions")}

    report = apply_ops(
        conv.draft,
        ops,
        board.cards(),
        origin=origin,
        current=conv.mode == MeetingMode.CURRENT,
        brainstorm=brainstorm,
    )
    reply = founder_text(polish(reply) if polish else reply) or (
        "Atualizei o rascunho." if report.changes else "Certo. O que mais você quer ajustar?"
    )
    if report.ignored and not failed:
        # the reply was written with the edits it asked for, before they were checked: the
        # Master said "removi o card S-010" while the edit was refused (tamagotchi SP-002 meeting)
        reply += (
            " Ressalva: nem tudo o que descrevi entrou no rascunho — "
            + "; ".join(report.ignored)
            + "."
        )
    conv.turns.append(
        Turn(
            who="agent",
            name=agent.name,
            text=reply,
            changes=report.changes,
            ignored=report.ignored,
        )
    )
    board.save(conv)
    agent.ctx.emit(
        "conversation.turn",
        agent=agent.name,
        conversation_id=conv.id,
        kind=conv.kind.value,
        cards=len(conv.draft.items),
        in_sprint=len(conv.draft.in_sprint()),
        ignored=len(report.ignored),
    )
    return TurnResult(reply, report.changes, report.ignored, questions, failed, consult, extra)


@dataclass
class QuickStory:
    """What the dashboard's "nova história rápida" became (ADR-0017): a card the Product Owner
    filed, or a review conversation where it explains why it did not."""

    story_id: str | None = None
    triage: Triage | None = None
    conversation: Conversation | None = None


# ---------------------------------------------------------------------------- facade


class Conversations:
    """Sessions of one factory: the API the CLI and the dashboard use."""

    def __init__(self, ctx: EngineContext):
        self.ctx = ctx
        self.board = ConversationBoard(ctx.store, ctx.slug)

    def agent_for(self, kind: ConversationKind) -> Any:
        """Who answers the founder: the Product Owner in a review, the Master otherwise (in a
        brainstorm it calls in the other roles, ADR-0020)."""
        from loompa.agents.master import MasterAgent
        from loompa.agents.product_owner import ProductOwnerAgent

        if kind == ConversationKind.REVIEW:
            return ProductOwnerAgent(self.ctx)
        return MasterAgent(self.ctx)

    def open(self, kind: ConversationKind, title: str = "") -> Conversation:
        """A new session. A review conversation is never opened by hand: it is born from a
        quick story the Product Owner did not file (`quick_story`). With a sprint running, a
        meeting waits for the founder to choose what it is about (`choose`, ADR-0018);
        otherwise it plans the next sprint, starting from the cards already planned for it (an
        inbox decision puts them there), so it sees, and can drop, everything that sprint would
        start with."""
        if kind == ConversationKind.REVIEW:
            raise ConversationError("a revisão do Product Owner nasce de uma história rápida")
        conv = self.board.create(kind, title)
        if (
            kind == ConversationKind.MEETING
            and SprintBoard(self.ctx.store, self.ctx.slug).running() is None
        ):
            self._plan_next(conv)
            self.board.save(conv)
        self.ctx.emit("conversation.opened", conversation_id=conv.id, kind=conv.kind.value)
        return conv

    def _plan_next(self, conv: Conversation) -> None:
        conv.mode = MeetingMode.NEXT
        planned = SprintBoard(self.ctx.store, self.ctx.slug).open_sprint()
        if planned is None:
            return
        conv.draft.sprint_id = planned.id
        conv.draft.goal = conv.draft.goal or planned.goal
        ops = [{"op": "update", "ref": sid, "in_sprint": True} for sid in planned.story_ids]
        if ops:
            apply_ops(conv.draft, ops, self.board.cards())

    def choose(self, conversation_id: str, mode: MeetingMode) -> Conversation:
        """With a sprint running, the founder says what the meeting is about: the running
        sprint (its cards come into the draft as they are now) or the next one, assembled to
        wait for the running one (not recommended: what this sprint teaches is not in yet)."""
        conv = self.board.require(conversation_id)
        if conv.kind != ConversationKind.MEETING or conv.mode is not None:
            raise ConversationError("esta reunião já sabe do que trata")
        mode = MeetingMode(mode)
        running = SprintBoard(self.ctx.store, self.ctx.slug).running()
        if mode == MeetingMode.CURRENT:
            if running is None:
                raise ConversationError("não há sprint em andamento")
            conv.mode = mode
            conv.draft.sprint_id, conv.draft.goal = running.id, running.goal
            conv.draft.members = list(running.story_ids)
            for sid in running.story_ids:
                row = self.ctx.store.get_story(sid)
                if row is None:
                    continue
                conv.draft.items.append(
                    DraftItem(
                        key=sid,
                        title=row["title"],
                        epic=row.get("epic", ""),
                        priority=to_scale(row["priority"]),
                        in_sprint=True,
                        story_id=sid,
                        origin=row.get("origin", "founder"),
                        stage=row["stage"],
                    )
                )
            text = _current_intro(running.id)
        else:
            self._plan_next(conv)
            text = (
                f"Certo: vamos pré-montar o próximo sprint. Ele fica esperando o {running.id} "
                "terminar; para disparar, abra uma nova reunião depois e eu mostro como revisar "
                "e iniciar."
                if running
                else "Certo: vamos montar o próximo sprint."
            )
        conv.turns.append(Turn(who="agent", name=self.agent_for(conv.kind).name, text=text))
        self.board.save(conv)
        self.ctx.emit("meeting.mode", conversation_id=conv.id, mode=mode.value)
        return conv

    async def brief(self, conversation_id: str) -> str:
        """The Master's opening of a Sprint Meeting: where the project stands (plan 10.2)."""
        conv = self.board.require(conversation_id)
        if conv.kind != ConversationKind.MEETING:
            raise ConversationError("só a reunião de sprint abre com um parecer")
        text = await self.agent_for(conv.kind).brief(conv)
        self.board.save(conv)
        return text

    async def say(self, conversation_id: str, text: str) -> TurnResult:
        conv = self.board.require(conversation_id)
        if conv.kind == ConversationKind.MEETING and conv.mode is None:
            raise ConversationError(
                "escolha primeiro: ajustar o sprint em andamento ou pré-montar o próximo"
            )
        if conv.kind == ConversationKind.BRAINSTORM and text.strip():
            self._reopen(conv)  # talking again reopens the direction the founder approved
        turn = await self.agent_for(conv.kind).converse(conv, text)
        if conv.kind == ConversationKind.REVIEW and not turn.failed:
            conv = self.board.require(conversation_id)
            if conv.draft.review is not None and conv.draft.review.admit:
                await self.commit(conversation_id)  # approved: the Product Owner files it now
        return turn

    # ----------------------------------------------------------- brainstorm (ADR-0020)
    def _brainstorm(self, conversation_id: str) -> Conversation:
        conv = self.board.require(conversation_id)
        if conv.kind != ConversationKind.BRAINSTORM:
            raise ConversationError("isso só vale num brainstorm")
        return conv

    def _reopen(self, conv: Conversation) -> bool:
        """The founder went back to the conversation after the first OK: the approval and the
        Product Owner's split no longer stand."""
        if not conv.draft.reopen():
            return False
        self.board.save(conv)
        self.ctx.emit("brainstorm.reopened", conversation_id=conv.id)
        return True

    async def consult(self, conversation_id: str, role: str, question: str) -> TurnResult:
        """The founder asks a role for an opinion directly (the Master asks on its own too)."""
        from loompa.agents.brainstorm import CONSULTANTS, consult, opinion_text

        conv = self._brainstorm(conversation_id)
        who = CONSULTANTS.get(role.strip().lower())
        question = question.strip()
        if who is None:
            raise ConversationError(f"não há um papel “{role}” para consultar")
        if not question:
            raise ConversationError("diga o que perguntar")
        self._reopen(conv)
        conv.turns.append(Turn(who="founder", text=f"Parecer do {who.role.title()}: {question}"))
        self.board.save(conv)  # the question is safe even if the role cannot answer
        opinion = await consult(self.ctx, self.board, conv, who.role, question, asked_by="founder")
        return TurnResult(opinion_text(opinion), failed=opinion.failed)

    async def approve(self, conversation_id: str) -> TurnResult:
        """The founder's first OK: the direction stands, and the Product Owner proposes how it
        becomes cards. Nothing is written until the second OK (`commit`)."""
        from loompa.agents.product_owner import ProductOwnerAgent
        from loompa.conversations import Approval

        conv = self._brainstorm(conversation_id)
        if not conv.draft.items and not conv.draft.direction:
            raise ConversationError("ainda não há um rumo para aprovar: conte a ideia primeiro")
        conv.draft.approved = Approval(
            direction=conv.draft.direction, ideas=[i.key for i in conv.draft.items]
        )
        self.board.save(conv)
        self.ctx.emit("brainstorm.approved", conversation_id=conv.id, ideas=len(conv.draft.items))
        turn = await ProductOwnerAgent(self.ctx).propose_split(conv)
        self.board.save(conv)
        return turn

    def reopen(self, conversation_id: str) -> Conversation:
        """Back from the Product Owner's split to the conversation."""
        conv = self._brainstorm(conversation_id)
        if self._reopen(conv):
            conv.turns.append(
                Turn(
                    who="agent",
                    name=self.agent_for(conv.kind).name,
                    text="Certo, voltamos à conversa. A divisão do Product Owner foi deixada de "
                    "lado; quando o rumo estiver bom, aprove de novo.",
                )
            )
            self.board.save(conv)
        return conv

    async def propose(self, conversation_id: str) -> TurnResult:
        """The Product Owner's sprint proposal (plan 10.2); the sprint starts only after one."""
        from loompa.agents.product_owner import ProductOwnerAgent

        conv = self.board.require(conversation_id)
        if conv.kind != ConversationKind.MEETING:
            raise ConversationError("só a reunião de sprint tem proposta de sprint")
        if conv.mode is None:
            raise ConversationError(
                "escolha primeiro: ajustar o sprint em andamento ou pré-montar o próximo"
            )
        turn = await ProductOwnerAgent(self.ctx).propose_sprint(conv)
        self.board.save(conv)
        return turn

    async def quick_story(self, title: str, description: str = "", priority: int = 3) -> QuickStory:
        """The founder's quick story (plan 10.6): the Product Owner reads it first. Approved, it
        is filed as the Product Owner wrote it (the founder's words stay on the card); refused,
        nothing is written and a review conversation opens with the reason."""
        from loompa.agents.product_owner import ProductOwnerAgent
        from loompa.conversations import from_scale

        title, description = title.strip(), description.strip()
        if not title:
            raise ConversationError("uma história precisa de título")
        po = ProductOwnerAgent(self.ctx)
        said = "\n".join(p for p in (title, description) if p)
        verdict = await po.triage_request(title, description)
        if verdict.admit:
            added = po.file_request(verdict, [said], priority=from_scale(priority))
            if added.created:
                return QuickStory(story_id=added.story_id, triage=verdict)
            verdict = verdict.model_copy(
                update={
                    "admit": False,
                    "reason_code": "duplicate",
                    "duplicate_of": added.story_id,
                    "reason": "Há um card aberto com o mesmo título.",
                }
            )
        conv = self.board.create(ConversationKind.REVIEW, title)
        conv.turns.append(Turn(who="founder", text=said))
        conv.draft.items = [
            DraftItem(
                key="D1",
                title=verdict.title or title,
                description=verdict.description or description,
                epic=verdict.epic,
                note=verdict.reason,
            )
        ]
        conv.draft.next_key = 2
        conv.draft.review = verdict
        conv.turns.append(Turn(who="agent", name=po.name, text=po.refusal_text(verdict)))
        self.board.save(conv)
        self.ctx.emit(
            "conversation.opened",
            conversation_id=conv.id,
            kind=conv.kind.value,
            reason_code=verdict.reason_code,
        )
        return QuickStory(triage=verdict, conversation=conv)

    def edit(self, conversation_id: str, ops: list[Any], *, split: bool = False) -> OpsReport:
        """The founder's own edits (checkboxes, priorities, removing a card): the same
        operations the model uses, so the chat and the panel agree. In a brainstorm, `split`
        edits the Product Owner's proposed cards (priority, or leaving one out); editing the
        ideas after the first OK takes the approval back."""
        conv = self.board.require(conversation_id)
        if conv.kind == ConversationKind.REVIEW:
            raise ConversationError("o pedido em revisão muda pela conversa com o Product Owner")
        if conv.kind == ConversationKind.BRAINSTORM:
            return self._edit_brainstorm(conv, ops, split=split)
        if conv.kind == ConversationKind.MEETING and conv.mode is None:
            raise ConversationError(
                "escolha primeiro: ajustar o sprint em andamento ou pré-montar o próximo"
            )
        report = apply_ops(
            conv.draft, ops, self.board.cards(), current=conv.mode == MeetingMode.CURRENT
        )
        self.board.save(conv)
        self.ctx.emit("conversation.edited", conversation_id=conv.id, changes=len(report.changes))
        return report

    def _edit_brainstorm(self, conv: Conversation, ops: list[Any], *, split: bool) -> OpsReport:
        from loompa.conversations import Draft

        if split:
            if conv.draft.split is None:
                raise ConversationError("ainda não há uma divisão do Product Owner para ajustar")
            wanted = [
                o
                for o in ops
                if isinstance(o, dict) and str(o.get("op") or "").lower() in ("update", "drop")
            ]
            temp = Draft(items=conv.draft.split)
            report = apply_ops(temp, wanted, {}, brainstorm=True)
            if len(wanted) < len(ops):
                report.ignored.append(
                    "na divisão do Product Owner só a prioridade muda ou um card sai"
                )
            conv.draft.split = temp.items
        else:
            report = apply_ops(conv.draft, ops, self.board.cards(), brainstorm=True)
            if report.changes and any(
                str(o.get("op") or "").lower() not in ("dismiss", "restore")
                for o in ops
                if isinstance(o, dict)
            ):
                self._reopen(conv)
        self.board.save(conv)
        self.ctx.emit("conversation.edited", conversation_id=conv.id, changes=len(report.changes))
        return report

    def touches_work_in_flight(self, conversation_id: str) -> bool:
        """Committing this meeting would take out, restart or cancel a story that may be
        executing right now: the engine has to stop first (the dashboard pauses it)."""
        conv = self.board.require(conversation_id)
        if conv.mode != MeetingMode.CURRENT:
            return False
        moving = {
            i.story_id
            for i in conv.draft.items
            if i.story_id in conv.draft.members
            and (conv.draft.cancel_sprint or i.restart or not i.in_sprint)
        }
        return any(
            (row := self.ctx.store.get_story(sid)) is not None and row["stage"] in AT_WORK
            for sid in moving
        )

    async def commit(
        self,
        conversation_id: str,
        *,
        start_sprint: bool = False,
        plan_next: bool = False,
        goal: str = "",
        force: bool = False,
    ) -> CommitResult:
        """End the session. A meeting about the next sprint sends its cards to the backlog
        through the Product Owner and, with `start_sprint`, starts the sprint or, with
        `plan_next`, assembles it to wait for the running one. A meeting about the running
        sprint applies its changes to it. A brainstorm asks the Product Owner to admit the
        ideas; whatever it holds back stays in the draft for another round. A review files the
        request once the Product Owner approves it, or with `force` when the founder has the
        last word."""
        from loompa.agents.product_owner import ProductOwnerAgent

        conv = self.board.require(conversation_id)
        if (start_sprint or plan_next) and conv.kind != ConversationKind.MEETING:
            raise ConversationError("só uma reunião de sprint pode começar um sprint")
        if conv.kind == ConversationKind.BRAINSTORM and conv.draft.split is None:
            raise ConversationError(
                "aprove o rumo primeiro: o Product Owner propõe os cards e você confirma"
            )
        if conv.kind == ConversationKind.MEETING and conv.mode is None:
            raise ConversationError(
                "escolha primeiro: ajustar o sprint em andamento ou pré-montar o próximo"
            )
        try:
            if conv.kind == ConversationKind.MEETING and conv.mode == MeetingMode.CURRENT:
                master = self.agent_for(conv.kind)
                result = await master.commit_sprint_changes(conv)
                closing = master.name, _changes_summary(result, conv)
            elif conv.kind == ConversationKind.MEETING:
                master = self.agent_for(conv.kind)
                result = await master.commit_meeting(
                    conv, start_sprint=start_sprint, plan_next=plan_next, goal=goal
                )
                closing = master.name, _meeting_summary(result, start_sprint, plan_next)
            elif conv.kind == ConversationKind.REVIEW:
                po = ProductOwnerAgent(self.ctx)
                result = po.commit_review(conv, force=force)
                closing = po.name, _review_summary(result, conv)
            else:  # a brainstorm: the second OK files the Product Owner's split
                po = ProductOwnerAgent(self.ctx)
                result = await po.apply_split(conv)
                closing = po.name, _split_summary(result, conv)
        finally:
            self.board.save(conv)  # ids assigned before a failure survive for the retry
        conv.turns.append(Turn(who="agent", name=closing[0], text=closing[1]))
        conv.result = result.as_dict()
        if not (conv.kind == ConversationKind.BRAINSTORM and conv.draft.items):
            conv.status = ConversationStatus.COMMITTED  # a brainstorm with held ideas stays open
        self.board.save(conv)
        self.ctx.emit(
            "conversation.committed",
            conversation_id=conv.id,
            kind=conv.kind.value,
            created=len(result.created),
            sprint_id=result.sprint_id,
        )
        return result

    def discard(self, conversation_id: str) -> Conversation:
        conv = self.board.discard(conversation_id)
        self.ctx.emit("conversation.discarded", conversation_id=conv.id)
        return conv


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _current_intro(sprint_id: str) -> str:
    return (
        f"Certo: vamos olhar o {sprint_id}. Os cards dele estão no rascunho como estão agora. "
        "Posso tirar um card do sprint (ele volta ao backlog), incluir outro (o Product Owner "
        "avalia antes), recomeçar uma história do zero ou cancelar o sprint; também podemos "
        "discutir alternativas, dependências e bloqueios. Nada muda até você aplicar."
    )


def _changes_summary(result: CommitResult, conv: Conversation) -> str:
    if conv.draft.cancel_sprint:
        back = ", ".join(result.withdrawn) or "nenhuma"
        return (
            f"O {conv.draft.sprint_id} foi cancelado. Voltaram ao backlog: {back}. "
            "O Product Owner reordenou o backlog."
        )
    parts = []
    if result.joined:
        parts.append(f"entraram no sprint: {', '.join(result.joined)}")
    if result.withdrawn:
        parts.append(f"voltaram ao backlog: {', '.join(result.withdrawn)}")
    if result.restarted:
        parts.append(f"recomeçam do zero: {', '.join(result.restarted)}")
    if result.retired:
        parts.append(f"saíram do backlog (cancelados): {', '.join(result.retired)}")
    if not parts:
        return f"Nada mudou no {conv.draft.sprint_id}."
    return f"Pronto, {conv.draft.sprint_id} ajustado: " + "; ".join(parts) + "."


def _meeting_summary(result: CommitResult, started: bool, planned: bool = False) -> str:
    cards = len(result.created) + len(result.existing)
    text = f"Pronto: {_plural(cards, 'história', 'histórias')} no backlog"
    if result.created and result.existing:
        text += f" ({_plural(len(result.created), 'nova', 'novas')})"
    if started:
        text += f" e o sprint {result.sprint_id} começou."
    elif planned:
        text += (
            f" e o sprint {result.sprint_id} está montado. Ele espera o sprint em andamento "
            "terminar; para disparar, abra uma reunião de sprint e revise com o Product Owner."
        )
    else:
        text += "."
    if result.skipped:
        text += " Ficaram fora do sprint por já estarem em andamento: " + ", ".join(result.skipped)
    if result.retired:
        text += f" Saíram do backlog (cancelados): {', '.join(result.retired)}."
    return text


def _review_summary(result: CommitResult, conv: Conversation) -> str:
    review = conv.draft.review
    if result.existing and not result.created:
        return f"Não gravei de novo: {result.existing[0]} já tem esse título no backlog."
    sid = (result.created or ["?"])[0]
    if review is not None and not review.admit:
        return (
            f"Gravei {sid} como você pediu. Minha objeção ficou registrada no card: {review.reason}"
        )
    return f"Gravei {sid} no backlog."


def _split_summary(result: CommitResult, conv: Conversation) -> str:
    parts = []
    if result.created:
        parts.append(
            f"Gravei {_plural(len(result.created), 'card novo', 'cards novos')} no backlog "
            f"({', '.join(result.created)})."
        )
    if result.amended:
        parts.append(f"Acrescentei o que foi decidido a {', '.join(result.amended)}.")
    if result.existing:
        parts.append(
            f"{_plural(len(result.existing), 'já estava', 'já estavam')} no backlog: "
            f"{', '.join(result.existing)}."
        )
    if conv.draft.items:
        parts.append(
            f"{_plural(len(conv.draft.items), 'ideia ficou', 'ideias ficaram')} de fora e "
            f"{'continua' if len(conv.draft.items) == 1 else 'continuam'} aqui para outra rodada."
        )
    return " ".join(parts) or "Nada novo para gravar."
