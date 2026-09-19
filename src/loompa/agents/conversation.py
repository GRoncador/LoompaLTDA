"""Running a chat turn, and the facade the CLI and the dashboard talk to (ADR-0010).

`run_turn` is the one place a founder message becomes a model call: it records the message
first (a crash never loses it), builds the prompt from the draft plus the last turns, asks the
agent for `{reply, ops, questions}`, applies the ops in code and records the answer. The agents
(Master for a Sprint Meeting, Analyst for a brainstorm) only bring their prompt and their tools.
`Conversations` opens, continues, edits and commits sessions; committing always goes through the
Product Owner, so a conversation never writes a card by itself.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from loompa.agents.base import LoompaAgent
from loompa.comms import audit_executive_text, sanitize_for_founder
from loompa.conversations import (
    Conversation,
    ConversationBoard,
    ConversationError,
    ConversationKind,
    ConversationStatus,
    OpsReport,
    Turn,
    apply_ops,
    render_backlog,
    render_draft,
    render_transcript,
)

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
   "priority"?: 1-5, "in_sprint"?: bool}}  (a ref to a backlog card pulls it into the draft; for a
   card that already exists only `priority` and `in_sprint` can change)
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
        reply, questions, failed = FAILURE_REPLY, [], True
        if fallback_ops is not None and not conv.draft.items:
            ops = fallback_ops(text)  # keep the day moving: a deterministic split of the goals
    else:
        reply, ops, questions = parse_turn(data)
        failed = False

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
    return TurnResult(reply, report.changes, report.ignored, questions, failed)


# ---------------------------------------------------------------------------- facade


class Conversations:
    """Sessions of one factory: the API the CLI and the dashboard use."""

    def __init__(self, ctx: EngineContext):
        self.ctx = ctx
        self.board = ConversationBoard(ctx.store, ctx.slug)

    def agent_for(self, kind: ConversationKind) -> Any:
        from loompa.agents.analyst import AnalystAgent
        from loompa.agents.master import MasterAgent

        return MasterAgent(self.ctx) if kind == ConversationKind.MEETING else AnalystAgent(self.ctx)

    def open(self, kind: ConversationKind, title: str = "") -> Conversation:
        conv = self.board.create(kind, title)
        self.ctx.emit("conversation.opened", conversation_id=conv.id, kind=conv.kind.value)
        return conv

    async def say(self, conversation_id: str, text: str) -> TurnResult:
        conv = self.board.require(conversation_id)
        return await self.agent_for(conv.kind).converse(conv, text)

    def edit(self, conversation_id: str, ops: list[Any]) -> OpsReport:
        """The founder's own edits (checkboxes, priorities, removing a card): the same
        operations the model uses, so the chat and the panel agree."""
        conv = self.board.require(conversation_id)
        report = apply_ops(conv.draft, ops, self.board.cards())
        self.board.save(conv)
        self.ctx.emit("conversation.edited", conversation_id=conv.id, changes=len(report.changes))
        return report

    async def commit(
        self, conversation_id: str, *, start_sprint: bool = False, goal: str = ""
    ) -> CommitResult:
        """End the session. A meeting sends its cards to the backlog through the Product Owner
        and, with `start_sprint`, starts the sprint. A brainstorm asks the Product Owner to admit
        the ideas; whatever it holds back stays in the draft for another round."""
        from loompa.agents.product_owner import ProductOwnerAgent

        conv = self.board.require(conversation_id)
        if start_sprint and conv.kind != ConversationKind.MEETING:
            raise ConversationError("só uma reunião de sprint pode começar um sprint")
        try:
            if conv.kind == ConversationKind.MEETING:
                master = self.agent_for(conv.kind)
                result = master.commit_meeting(conv, start_sprint=start_sprint, goal=goal)
                closing = master.name, _meeting_summary(result, start_sprint)
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
