"""Running a chat turn, and the facade the CLI and the dashboard talk to (ADR-0010).

`run_turn` is the one place a founder message becomes a model call: it records the message
first (a crash never loses it), builds the prompt from the draft plus the last turns, asks the
agent for `{reply, ops, questions}`, applies the ops in code and records the answer. The agents
(Master for a Sprint Meeting, Analyst for a brainstorm) only bring their prompt and their tools.
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
    OpsReport,
    Turn,
    apply_ops,
    render_backlog,
    render_draft,
    render_transcript,
)
from loompa.sprints import SprintBoard

if TYPE_CHECKING:
    from loompa.agents.toolbox import Toolbox
    from loompa.conversations import CommitResult
    from loompa.engine.context import EngineContext

log = logging.getLogger(__name__)

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
- {{"op": "drop", "ref": "D1"}}
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
) -> TurnResult:
    text = text.strip()
    if not text:
        raise ConversationError("escreva uma mensagem")
    history = render_transcript(conv)  # everything said before this message
    conv.turns.append(Turn(who="founder", text=text))
    if not conv.title:
        conv.title = (sanitize_for_founder(text, max_chars=80).splitlines() or [""])[0]
    board.save(conv)  # the message is safe even if the model call fails

    user = (
        f"{context}\n\n## Backlog\n{render_backlog(board.cards())}\n\n"
        f"## Current draft\n{render_draft(conv.draft)}\n\n"
        f"## Conversation so far\n{history}\n\n## Founder's message\n{text}"
    )
    if rounds is None:
        rounds = min(agent.ctx.config.schedule.agent_tool_iterations, CHAT_TOOL_ROUNDS)
    ops: list[Any] = []
    try:
        data = await agent.ask_json_with_tools(
            system.format(language=agent.language) + TURN_CONTRACT.format(language=agent.language),
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
        reply, questions, failed, consult = FAILURE_REPLY, [], True, False
        if fallback_ops is not None and not conv.draft.items:
            ops = fallback_ops(text)  # keep the day moving: a deterministic split of the goals
    else:
        reply, ops, questions = parse_turn(data)
        failed, consult = False, data.get("consult_po") is True

    report = apply_ops(conv.draft, ops, board.cards(), origin=origin)
    reply = founder_text(polish(reply) if polish else reply) or (
        "Atualizei o rascunho." if report.changes else "Certo. O que mais você quer ajustar?"
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
    return TurnResult(reply, report.changes, report.ignored, questions, failed, consult)


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
        from loompa.agents.analyst import AnalystAgent
        from loompa.agents.master import MasterAgent
        from loompa.agents.product_owner import ProductOwnerAgent

        if kind == ConversationKind.MEETING:
            return MasterAgent(self.ctx)
        if kind == ConversationKind.REVIEW:
            return ProductOwnerAgent(self.ctx)
        return AnalystAgent(self.ctx)

    def open(self, kind: ConversationKind, title: str = "") -> Conversation:
        """A new session. A review conversation is never opened by hand: it is born from a
        quick story the Product Owner did not file (`quick_story`). A meeting starts with the
        cards already planned for the next sprint (an inbox decision puts them there), so the
        meeting sees, and can drop, everything the sprint would start with."""
        if kind == ConversationKind.REVIEW:
            raise ConversationError("a revisão do Product Owner nasce de uma história rápida")
        conv = self.board.create(kind, title)
        if kind == ConversationKind.MEETING:
            planned = SprintBoard(self.ctx.store, self.ctx.slug).open_sprint()
            ops = [
                {"op": "update", "ref": sid, "in_sprint": True}
                for sid in (planned.story_ids if planned else [])
            ]
            if ops:
                apply_ops(conv.draft, ops, self.board.cards())
                self.board.save(conv)
        self.ctx.emit("conversation.opened", conversation_id=conv.id, kind=conv.kind.value)
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
        turn = await self.agent_for(conv.kind).converse(conv, text)
        if conv.kind == ConversationKind.REVIEW and not turn.failed:
            conv = self.board.require(conversation_id)
            if conv.draft.review is not None and conv.draft.review.admit:
                await self.commit(conversation_id)  # approved: the Product Owner files it now
        return turn

    async def propose(self, conversation_id: str) -> TurnResult:
        """The Product Owner's sprint proposal (plan 10.2); the sprint starts only after one."""
        from loompa.agents.product_owner import ProductOwnerAgent

        conv = self.board.require(conversation_id)
        if conv.kind != ConversationKind.MEETING:
            raise ConversationError("só a reunião de sprint tem proposta de sprint")
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

    def edit(self, conversation_id: str, ops: list[Any]) -> OpsReport:
        """The founder's own edits (checkboxes, priorities, removing a card): the same
        operations the model uses, so the chat and the panel agree."""
        conv = self.board.require(conversation_id)
        if conv.kind == ConversationKind.REVIEW:
            raise ConversationError("o pedido em revisão muda pela conversa com o Product Owner")
        report = apply_ops(conv.draft, ops, self.board.cards())
        self.board.save(conv)
        self.ctx.emit("conversation.edited", conversation_id=conv.id, changes=len(report.changes))
        return report

    async def commit(
        self,
        conversation_id: str,
        *,
        start_sprint: bool = False,
        goal: str = "",
        force: bool = False,
    ) -> CommitResult:
        """End the session. A meeting sends its cards to the backlog through the Product Owner
        and, with `start_sprint`, starts the sprint. A brainstorm asks the Product Owner to admit
        the ideas; whatever it holds back stays in the draft for another round. A review files
        the request once the Product Owner approves it, or with `force` when the founder has the
        last word."""
        from loompa.agents.product_owner import ProductOwnerAgent

        conv = self.board.require(conversation_id)
        if start_sprint and conv.kind != ConversationKind.MEETING:
            raise ConversationError("só uma reunião de sprint pode começar um sprint")
        try:
            if conv.kind == ConversationKind.MEETING:
                master = self.agent_for(conv.kind)
                result = await master.commit_meeting(conv, start_sprint=start_sprint, goal=goal)
                closing = master.name, _meeting_summary(result, start_sprint)
            elif conv.kind == ConversationKind.REVIEW:
                po = ProductOwnerAgent(self.ctx)
                result = po.commit_review(conv, force=force)
                closing = po.name, _review_summary(result, conv)
            else:
                po = ProductOwnerAgent(self.ctx)
                result = await po.admit_ideas(conv)
                closing = po.name, _ideas_summary(result)
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


def _meeting_summary(result: CommitResult, started: bool) -> str:
    cards = len(result.created) + len(result.existing)
    text = f"Pronto: {_plural(cards, 'história', 'histórias')} no backlog"
    if result.created and result.existing:
        text += f" ({_plural(len(result.created), 'nova', 'novas')})"
    text += f" e o sprint {result.sprint_id} começou." if started else "."
    if result.skipped:
        text += " Ficaram fora do sprint por já estarem em andamento: " + ", ".join(result.skipped)
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


def _ideas_summary(result: CommitResult) -> str:
    parts = []
    if result.created:
        parts.append(
            f"Admiti {_plural(len(result.created), 'ideia', 'ideias')} no backlog "
            f"({', '.join(result.created)})."
        )
    if result.existing:
        parts.append(
            f"{_plural(len(result.existing), 'já estava', 'já estavam')} no backlog: "
            f"{', '.join(result.existing)}."
        )
    for held in result.held:
        parts.append(f"Deixei “{held['title']}” de fora: {held['reason']}")
    if result.held:
        parts.append(
            "Ela continua no rascunho para você refinar com o Analyst."
            if len(result.held) == 1
            else "Elas continuam no rascunho para você refinar com o Analyst."
        )
    return " ".join(parts) or "Nada novo para admitir."
