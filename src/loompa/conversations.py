"""Conversations: chat sessions with a draft of the backlog and of the sprint (ADR-0010).

A session is where the founder thinks out loud with a Loompa: the Master in a Sprint Meeting,
the Analyst in a brainstorm. Everything the session produces is a *draft* kept inside the session
(`conversations` table); the stories table is not touched until the founder commits, and then
only through the Product Owner (`ProductOwnerAgent.add_item`, ADR-0008).

The model never writes the draft. It proposes a small list of edits (`add`, `update`, `drop`,
`goal`) and `apply_ops` validates each one in code: unknown references are ignored, titles that
already exist in the backlog become references to the existing card instead of duplicates, and
the text of an existing card can't be rewritten from here. The founder edits the same draft with
the same operations, so the chat and the checkboxes can never disagree.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from loompa.backlog import DEFAULT_PRIORITY, normalize_title
from loompa.engine.state import TERMINAL, Stage
from loompa.store import Store, now_iso

MAX_ITEMS = 40  # cards a draft may hold
MAX_TITLE = 120
PROMPT_TURNS = 16  # transcript turns replayed to the model; the draft carries the rest
PROMPT_TURN_CHARS = 1500


class ConversationError(ValueError):
    pass


class ConversationNotFound(ConversationError):
    pass


class ConversationKind(StrEnum):
    MEETING = "meeting"  # Sprint Meeting, led by the Master
    BRAINSTORM = "brainstorm"  # led by the Analyst; the Product Owner admits the ideas


class ConversationStatus(StrEnum):
    OPEN = "open"
    COMMITTED = "committed"
    DISCARDED = "discarded"


class Turn(BaseModel):
    who: Literal["founder", "agent"]
    name: str = ""  # who spoke, for agent turns
    text: str
    changes: list[str] = Field(default_factory=list)  # what the turn did to the draft
    ignored: list[str] = Field(default_factory=list)  # edits that were refused, and why
    at: str = Field(default_factory=now_iso)


class DraftItem(BaseModel):
    """A card in the draft. `story_id` is set when it already exists in the backlog; then the
    key is that id and only its priority and sprint membership can be changed from here."""

    key: str  # D1, D2… for new cards (never reused); the story id for existing ones
    title: str
    description: str = ""
    epic: str = ""
    priority: int = 3  # 1 urgent … 5 nice to have
    in_sprint: bool = False
    story_id: str | None = None
    origin: str = "founder"
    note: str = ""  # e.g. why the Product Owner held the idea back


class Draft(BaseModel):
    goal: str = ""
    items: list[DraftItem] = Field(default_factory=list)
    next_key: int = 1

    def find(self, ref: str) -> DraftItem | None:
        ref = ref.strip().lower()
        return next((i for i in self.items if i.key.lower() == ref), None)

    def in_sprint(self) -> list[DraftItem]:
        return [i for i in self.items if i.in_sprint]


class Conversation(BaseModel):
    id: str
    factory: str
    kind: ConversationKind
    status: ConversationStatus = ConversationStatus.OPEN
    title: str = ""
    turns: list[Turn] = Field(default_factory=list)
    draft: Draft = Field(default_factory=Draft)
    limits: list[str] = Field(default_factory=list)  # declared by code, e.g. "no web search"
    result: dict[str, Any] = Field(default_factory=dict)  # what the commit did
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = ""

    @property
    def open(self) -> bool:
        return self.status == ConversationStatus.OPEN


@dataclass
class CommitResult:
    """What committing a session did to the backlog and the sprint."""

    created: list[str] = field(default_factory=list)  # cards that did not exist before
    existing: list[str] = field(default_factory=list)  # cards that were already there
    held: list[dict[str, str]] = field(default_factory=list)  # ideas the Product Owner kept out
    skipped: list[str] = field(default_factory=list)  # sprint picks that could not join
    sprint_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "existing": self.existing,
            "held": self.held,
            "skipped": self.skipped,
            "sprint_id": self.sprint_id,
        }


# ---------------------------------------------------------------------------- open cards


@dataclass(frozen=True)
class OpenCard:
    id: str
    title: str
    stage: str
    priority: int  # 1-5
    epic: str = ""
    origin: str = "founder"

    @property
    def waiting(self) -> bool:
        return self.stage == Stage.BACKLOG


def to_scale(stored: int) -> int:
    """Stored priorities are 100-500 (`priority * 100`); drafts speak 1-5."""
    return max(1, min(5, round((stored or DEFAULT_PRIORITY) / 100)))


def from_scale(priority: int) -> int:
    return max(1, min(5, priority)) * 100


# ------------------------------------------------------------------------------- the ops


@dataclass
class OpsReport:
    changes: list[str] = field(default_factory=list)
    ignored: list[str] = field(default_factory=list)


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in ("true", "false", "sim", "não", "nao"):
        return value.strip().lower() in ("true", "sim")
    return None


def _prio(value: Any) -> int | None:
    try:
        return max(1, min(5, int(value)))
    except (TypeError, ValueError):
        return None


def _text(raw: Mapping[str, Any], key: str, limit: int) -> str | None:
    value = raw.get(key)
    return None if value is None else str(value).strip()[:limit]


def apply_ops(
    draft: Draft,
    ops: list[Any],
    cards: Mapping[str, OpenCard],
    *,
    origin: str = "founder",
) -> OpsReport:
    """Validate and apply edits to `draft`. Nothing here raises for bad input from a model: a
    malformed or unknown operation is reported in `ignored` and skipped."""
    report = OpsReport()
    by_title = {normalize_title(c.title): c for c in cards.values()}
    for raw in ops:
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op") or raw.get("action") or "").strip().lower()
        if op == "add":
            _add(draft, raw, by_title, origin, report)
        elif op in ("update", "set"):
            _update(draft, raw, cards, report)
        elif op in ("drop", "remove"):
            _drop(draft, raw, report)
        elif op == "goal":
            goal = (_text(raw, "text", 200) or _text(raw, "goal", 200) or "").strip()
            draft.goal = goal
            report.changes.append(f"meta do sprint: {goal}" if goal else "meta do sprint removida")
        else:
            report.ignored.append(f"operação desconhecida: {op or '(vazia)'}")
    return report


def _pull(draft: Draft, card: OpenCard, *, in_sprint: bool = False) -> DraftItem:
    item = DraftItem(
        key=card.id,
        title=card.title,
        epic=card.epic,
        priority=card.priority,
        in_sprint=in_sprint,
        story_id=card.id,
        origin=card.origin,
    )
    draft.items.append(item)
    return item


def _add(
    draft: Draft,
    raw: dict[str, Any],
    by_title: Mapping[str, OpenCard],
    origin: str,
    report: OpsReport,
) -> None:
    title = (_text(raw, "title", MAX_TITLE) or "").strip()
    if not title:
        report.ignored.append("uma história precisa de título")
        return
    in_sprint = bool(_bool(raw.get("in_sprint")))
    key = normalize_title(title)
    same = next((i for i in draft.items if normalize_title(i.title) == key), None)
    if same is not None:  # the model repeated itself: refine the card instead of doubling it
        _apply_fields(same, raw)
        report.changes.append(f"atualizei “{same.title}”")
        return
    card = by_title.get(key)
    if card is not None:
        if not card.waiting:
            report.ignored.append(f"“{card.title}” já existe e está em andamento ({card.id})")
            return
        if draft.find(card.id) is None and len(draft.items) < MAX_ITEMS:
            _pull(draft, card, in_sprint=in_sprint)
            report.changes.append(f"“{card.title}” já estava no backlog ({card.id})")
        return
    if len(draft.items) >= MAX_ITEMS:
        report.ignored.append(f"o rascunho comporta no máximo {MAX_ITEMS} histórias")
        return
    item = DraftItem(
        key=f"D{draft.next_key}",
        title=title,
        description=(_text(raw, "description", 4000) or ""),
        epic=(_text(raw, "epic", 60) or ""),
        priority=_prio(raw.get("priority")) or 3,
        in_sprint=in_sprint,
        origin=origin,
    )
    draft.next_key += 1
    draft.items.append(item)
    report.changes.append(f"nova história “{title}”" + (" no sprint" if in_sprint else ""))


def _apply_fields(item: DraftItem, raw: Mapping[str, Any]) -> list[str]:
    """Copy the editable fields present in `raw`; returns the names of the fields it refused."""
    refused: list[str] = []
    for name, limit in (("title", MAX_TITLE), ("description", 4000), ("epic", 60)):
        value = _text(raw, name, limit)
        if value is None or value == getattr(item, name):
            continue
        if item.story_id:
            refused.append(name)  # the card's text belongs to the backlog
        elif name != "title" or value:
            setattr(item, name, value)
    if (prio := _prio(raw.get("priority"))) is not None:
        item.priority = prio
    if (flag := _bool(raw.get("in_sprint"))) is not None:
        item.in_sprint = flag
    return refused


def _ref(raw: Mapping[str, Any]) -> str:
    return str(raw.get("ref") or raw.get("id") or raw.get("key") or "").strip()


def _update(
    draft: Draft, raw: dict[str, Any], cards: Mapping[str, OpenCard], report: OpsReport
) -> None:
    ref = _ref(raw)
    item = draft.find(ref)
    if item is None:
        card = cards.get(ref.upper()) or cards.get(ref)
        if card is None:
            report.ignored.append(f"não encontrei {ref or 'a história citada'} no rascunho")
            return
        if not card.waiting:
            report.ignored.append(f"{card.id} já está em andamento e não entra em um sprint")
            return
        if len(draft.items) >= MAX_ITEMS:
            report.ignored.append(f"o rascunho comporta no máximo {MAX_ITEMS} histórias")
            return
        item = _pull(draft, card)
    refused = _apply_fields(item, raw)
    if refused:
        report.ignored.append(
            f"o texto de {item.key} pertence ao backlog e não muda por aqui ({', '.join(refused)})"
        )
    report.changes.append(f"ajustei “{item.title}”")


def _drop(draft: Draft, raw: dict[str, Any], report: OpsReport) -> None:
    item = draft.find(_ref(raw))
    if item is None:
        report.ignored.append(f"não encontrei {_ref(raw) or 'a história citada'} no rascunho")
        return
    draft.items.remove(item)
    report.changes.append(
        f"tirei “{item.title}” do rascunho" + (" (continua no backlog)" if item.story_id else "")
    )


# ------------------------------------------------------------------------ prompt renderers


def render_draft(draft: Draft) -> str:
    if not draft.items and not draft.goal:
        return "(empty)"
    lines = [f"Sprint goal: {draft.goal or '(not set)'}"]
    for i in draft.items:
        where = "IN SPRINT" if i.in_sprint else "backlog only"
        existing = " · existing backlog card" if i.story_id else ""
        epic = f" · epic: {i.epic}" if i.epic else ""
        lines.append(f'- {i.key} · P{i.priority} · {where}{existing}{epic} · "{i.title}"')
        if i.description and not i.story_id:
            lines.append(f"    {i.description[:400]}")
    return "\n".join(lines)


def render_backlog(cards: Mapping[str, OpenCard], *, limit: int = 60) -> str:
    waiting = [c for c in cards.values() if c.waiting]
    busy = [c for c in cards.values() if not c.waiting]
    lines = ["Cards waiting in the backlog (reference them by id):"]
    lines += [
        f'- {c.id} · P{c.priority} · {c.origin}{f" · epic: {c.epic}" if c.epic else ""} · "{c.title}"'
        for c in waiting[:limit]
    ] or ["(none)"]
    if busy:
        lines.append("Work already in progress (never duplicate it):")
        lines += [f'- {c.id} · {c.stage} · "{c.title}"' for c in busy[:limit]]
    return "\n".join(lines)


def render_transcript(conv: Conversation, *, limit: int = PROMPT_TURNS) -> str:
    turns = conv.turns[-limit:]
    if not turns:
        return "(this is the first message)"
    return "\n".join(
        f"{'Founder' if t.who == 'founder' else t.name or 'You'}: {t.text[:PROMPT_TURN_CHARS]}"
        for t in turns
    )


# --------------------------------------------------------------------------------- board


class ConversationBoard:
    """Persistence for chat sessions (the same shape as `SprintBoard`)."""

    def __init__(self, store: Store, slug: str):
        self.store = store
        self.slug = slug

    def create(self, kind: ConversationKind, title: str = "") -> Conversation:
        conv = Conversation(
            id=self.store.next_conversation_id(self.slug),
            factory=self.slug,
            kind=ConversationKind(kind),
            title=title.strip()[:80],
        )
        self.save(conv)
        return conv

    def get(self, conversation_id: str) -> Conversation | None:
        row = self.store.get_conversation(conversation_id)
        return Conversation.model_validate(row) if row else None

    def require(self, conversation_id: str, *, open_only: bool = True) -> Conversation:
        conv = self.get(conversation_id)
        if conv is None:
            raise ConversationNotFound(f"conversa {conversation_id} não existe")
        if open_only and not conv.open:
            raise ConversationError(f"{conversation_id} já foi encerrada")
        return conv

    def list(self, status: ConversationStatus | None = None) -> list[Conversation]:
        rows = self.store.list_conversations(self.slug, status.value if status else None)
        return [Conversation.model_validate(r) for r in rows]

    def save(self, conv: Conversation) -> Conversation:
        self.store.put_conversation(conv.model_dump(mode="json"))
        return conv

    def discard(self, conversation_id: str) -> Conversation:
        conv = self.require(conversation_id)
        conv.status = ConversationStatus.DISCARDED
        return self.save(conv)

    def cards(self) -> dict[str, OpenCard]:
        """Every card that is not finished, by id. What a session may reference or must not repeat."""
        return {
            row["id"]: OpenCard(
                id=row["id"],
                title=row["title"],
                stage=row["stage"],
                priority=to_scale(row["priority"]),
                epic=row.get("epic", ""),
                origin=row.get("origin", "founder"),
            )
            for row in self.store.list_stories(self.slug)
            if row["stage"] not in TERMINAL
        }
