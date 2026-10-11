"""Conversations: chat sessions with a draft of the backlog and of the sprint (ADR-0010).

A session is where the founder thinks out loud with a Loompa: the Master in a Sprint Meeting
and in a brainstorm (where it calls in the roles the idea needs, ADR-0020), the Product Owner in
the review of a quick story it would not file (ADR-0017). Everything the session produces is a *draft* kept inside the session
(`conversations` table); the stories table is not touched until the founder commits, and then
only through the Product Owner (`ProductOwnerAgent.add_item`, ADR-0008).

The model never writes the draft. It proposes a small list of edits (`add`, `update`, `drop`,
`goal`) and `apply_ops` validates each one in code: unknown references are ignored, titles that
already exist in the backlog become references to the existing card instead of duplicates, and
the text of an existing card can't be rewritten from here. The founder edits the same draft with
the same operations, so the chat and the checkboxes can never disagree.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

from loompa.backlog import DEFAULT_PRIORITY, Triage, normalize_title
from loompa.engine.state import TERMINAL, Stage
from loompa.store import Store, now_iso

MAX_ITEMS = 40  # cards a draft may hold
MAX_TITLE = 120
PROMPT_TURNS = 16  # transcript turns replayed to the model; the draft carries the rest
PROMPT_TURN_CHARS = 1500
DELIVERED_LIMIT = 30  # delivered stories shown to a meeting, newest first
DELIVERED_SUMMARY_CHARS = 220


class ConversationError(ValueError):
    pass


class ConversationNotFound(ConversationError):
    pass


class ConversationKind(StrEnum):
    MEETING = "meeting"  # Sprint Meeting, led by the Master
    BRAINSTORM = "brainstorm"  # led by the Master, who consults roles; the PO splits it (ADR-0020)
    REVIEW = "review"  # the Product Owner refused a quick story; the founder can clarify (ADR-0017)


class MeetingMode(StrEnum):
    """What a Sprint Meeting is about (ADR-0018). With a sprint running the founder chooses."""

    CURRENT = "current"  # adjust the running sprint: cards in or out, restarts, cancel it
    NEXT = "next"  # plan the next sprint: started now, or assembled to wait for the running one


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
    unpin: bool = False  # hand the founder's pinned place back to the Product Owner on commit
    stage: str = ""  # a card of the running sprint: where it is now
    restart: bool = False  # a card of the running sprint starts over from scratch on commit
    restart_reason: str = ""
    # a brainstorm's split (ADR-0020)
    kind: str = ""  # StoryKind value the Product Owner gave the card, "" when it did not say
    ideas: list[str] = Field(default_factory=list)  # the ideas (D1…) the card came from
    amend: str = ""  # with `story_id`: what this brainstorm adds to that waiting card
    depends_on: list[str] = Field(
        default_factory=list
    )  # keys of the cards it needs first (ADR-0021)


class Proposal(BaseModel):
    """The Product Owner's sprint proposal in a meeting (ADR-0017): the sprint starts only after
    one, and only with cards it saw."""

    keys: list[str] = Field(default_factory=list)  # the draft's cards when it was made
    reviewed: bool = True  # False: the model could not be asked, the draft went as it was
    at: str = Field(default_factory=now_iso)


class Opinion(BaseModel):
    """A role's preliminary opinion in a brainstorm (ADR-0020): points of attention, cost,
    benefit and counterpoints, never an acceptance of the idea. The founder can set one aside;
    the Master then stops weighing it."""

    key: str  # O1, O2…
    role: str
    name: str
    question: str
    summary: str = ""
    attention: list[str] = Field(default_factory=list)
    cost: str = ""
    benefit: str = ""
    counterpoints: list[str] = Field(default_factory=list)  # e.g. "goes against decision X"
    sources: list[str] = Field(default_factory=list)  # URLs a web tool returned, or repo paths
    asked_by: Literal["master", "founder"] = "master"
    failed: bool = False  # the role could not be asked; nothing in it is an opinion
    dismissed: bool = False
    at: str = Field(default_factory=now_iso)


class Approval(BaseModel):
    """The founder's first OK on a brainstorm's direction (ADR-0020)."""

    direction: str = ""
    ideas: list[str] = Field(default_factory=list)  # the idea keys it covered
    reviewed: bool = True  # False: the Product Owner could not split it, one card per idea
    at: str = Field(default_factory=now_iso)


class StackOption(BaseModel):
    """One stack the Architect proposes at a greenfield kickoff (plan 10.10)."""

    languages: list[str] = Field(default_factory=list)
    frameworks: list[str] = Field(default_factory=list)
    package_managers: list[str] = Field(default_factory=list)
    test_frameworks: list[str] = Field(default_factory=list)
    test_command: str = ""
    lint_command: str = ""
    summary: str = ""  # pt-BR, one line the founder reads
    reason: str = ""  # pt-BR: why this one for this product


class Kickoff(BaseModel):
    """The opening of a greenfield factory with no code (plan 10.10): what the brainstorm must
    settle before the first OK, on top of its direction and ideas (the v1 features)."""

    vision: str = ""  # what it is, for whom, the problem it solves
    out_of_v1: list[str] = Field(default_factory=list)
    stacks: list[StackOption] = Field(default_factory=list)  # 1 = recommended, 2 = alternative
    chosen: int | None = None  # 1-based index into `stacks`, the founder's pick
    applied: bool = False  # the first OK wrote the stack into the factory

    def stack(self) -> StackOption | None:
        if self.chosen is None or not 1 <= self.chosen <= len(self.stacks):
            return None
        return self.stacks[self.chosen - 1]


GROOM_ACTIONS = ("retire", "merge", "split")
FACTORY_ORIGINS = ("kaizen",)  # cards the factory filed itself; every other origin is the founder's


class GroomProposal(BaseModel):
    """One change the Product Owner proposes to a waiting card when it grooms the backlog at a
    Sprint Meeting (ADR-0038). A card the factory filed is confirmed by the Master and lands in the
    draft at once; a card that came from the founder, or one the Master held, waits for the
    founder's answer. Either way nothing leaves the backlog until the meeting is saved."""

    ref: str  # the card: S-074
    action: str  # retire | merge | split
    reason: str = ""  # for the founder, plain words
    into: str = ""  # merge: the waiting card that takes this one's content
    parts: list[dict[str, str]] = Field(default_factory=list)  # split: [{"title", "description"}]
    founder: bool = False  # only the founder answers it (the founder's card, or held by the Master)
    held: str = ""  # the Master's reason when it did not confirm a change to a factory card
    accepted: bool | None = None  # None: waiting for the founder; True: in the draft; False: no
    keys: list[str] = Field(default_factory=list)  # draft keys this proposal added (split parts)
    pulled: bool = False  # merge: the target was pulled into the draft by this proposal


class Draft(BaseModel):
    goal: str = ""
    items: list[DraftItem] = Field(default_factory=list)
    next_key: int = 1
    proposal: Proposal | None = None  # meetings
    review: Triage | None = None  # review conversations: the Product Owner's latest reading
    sprint_id: str = ""  # the sprint the meeting is about (running, or the one being assembled)
    members: list[str] = Field(default_factory=list)  # the running sprint's cards when it opened
    # the members came in with their relations, so the draft's are theirs to apply (a meeting
    # opened before they did would otherwise wipe what the cards depend on)
    members_deps: bool = False
    cancel_sprint: bool = False  # a meeting about the running sprint: call it off on commit
    cancel_reason: str = ""
    # backlog cards the founder wants gone (obsolete, unwanted): story id -> reason. The Product
    # Owner cancels them on commit (tamagotchi SP-002: "S-010 is obsolete" had no op at all)
    retire: dict[str, str] = Field(default_factory=dict)
    # the Product Owner's grooming of the waiting backlog, once per meeting (ADR-0038)
    grooming: list[GroomProposal] = Field(default_factory=list)
    groomed: bool = False
    # a brainstorm (ADR-0020): the ideas are `items` until the founder approves the direction,
    # then the Product Owner's split into cards waits in `split` for the second OK
    direction: str = ""
    ready: bool = False  # the Master thinks the direction is clear enough to approve
    consults: list[Opinion] = Field(default_factory=list)
    next_opinion: int = 1
    approved: Approval | None = None
    split: list[DraftItem] | None = None
    kickoff: Kickoff | None = None  # a greenfield factory's opening brainstorm (plan 10.10)

    def opinions(self) -> list[Opinion]:
        """The opinions still in play (not set aside by the founder, not failed)."""
        return [o for o in self.consults if not o.dismissed and not o.failed]

    def reopen(self) -> bool:
        """Back from the split to the conversation: the first OK no longer stands."""
        was = self.approved is not None or self.split is not None
        self.approved, self.split = None, None
        return was

    def joining(self) -> list[DraftItem]:
        """Cards that would join the running sprint (it had them not when the meeting opened)."""
        return [i for i in self.in_sprint() if i.story_id not in self.members]

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
    mode: MeetingMode | None = None  # meetings only; None until the founder chooses
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
    # a meeting about the running sprint (ADR-0018)
    joined: list[str] = field(default_factory=list)
    withdrawn: list[str] = field(default_factory=list)
    restarted: list[str] = field(default_factory=list)
    amended: list[str] = field(default_factory=list)  # waiting cards a brainstorm added to
    retired: list[str] = field(default_factory=list)  # backlog cards cancelled by the meeting

    def as_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "existing": self.existing,
            "held": self.held,
            "skipped": self.skipped,
            "sprint_id": self.sprint_id,
            "joined": self.joined,
            "withdrawn": self.withdrawn,
            "restarted": self.restarted,
            "amended": self.amended,
            "retired": self.retired,
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
    pinned: bool = False  # the founder dragged it into place
    kind: str = ""
    depends_on: tuple[str, ...] = ()

    @property
    def waiting(self) -> bool:
        return self.stage == Stage.BACKLOG


@dataclass(frozen=True)
class DeliveredCard:
    """A story the factory delivered and the founder approved: what the product does today."""

    id: str
    title: str
    summary: str = ""
    kind: str = ""


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
    cycles: list[str] = field(default_factory=list)  # relations refused for closing a cycle


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
    current: bool = False,
    brainstorm: bool = False,
) -> OpsReport:
    """Validate and apply edits to `draft`. Nothing here raises for bad input from a model: a
    malformed or unknown operation is reported in `ignored` and skipped. `current` is a meeting
    about the running sprint, the only draft where a card restarts or the sprint is cancelled.
    In a `brainstorm` nothing goes into a sprint, the goal is the direction of the idea, and an
    opinion can be set aside (ADR-0020)."""
    report = OpsReport()
    by_title = {normalize_title(c.title): c for c in cards.values()}
    for raw in ops:
        if not isinstance(raw, dict):
            continue
        op = str(raw.get("op") or raw.get("action") or "").strip().lower()
        if brainstorm and "in_sprint" in raw:
            raw = {
                k: v for k, v in raw.items() if k != "in_sprint"
            }  # a brainstorm ends in the backlog
        if brainstorm and op in ("direction", "goal"):
            text = (_text(raw, "text", 1200) or _text(raw, op, 1200) or "").strip()
            if text != draft.direction:
                draft.direction = text
                report.changes.append("rumo atualizado" if text else "rumo removido")
            continue
        if brainstorm and op in ("dismiss", "restore"):
            _dismiss(draft, raw, op == "dismiss", report)
            continue
        if op == "choose_stack":
            _choose_stack(draft, raw, report)
            continue
        if op == "add":
            _add(
                draft,
                raw,
                by_title,
                origin,
                report,
                noun="ideia" if brainstorm else "história",
                cards=cards,
            )
        elif op in ("update", "set"):
            _update(draft, raw, cards, report)
        elif op in ("drop", "remove"):
            _drop(draft, raw, report)
        elif op in ("restart", "cancel_sprint", "keep_sprint") and not current:
            report.ignored.append("isso só vale numa reunião sobre o sprint em andamento")
        elif op == "restart":
            _restart(draft, raw, report)
        elif op == "cancel_sprint":
            draft.cancel_sprint = True
            draft.cancel_reason = (_text(raw, "reason", 300) or "").strip()
            report.changes.append("o sprint em andamento será cancelado ao aplicar")
        elif op == "keep_sprint":
            if draft.cancel_sprint:
                report.changes.append("o sprint em andamento continua")
            draft.cancel_sprint, draft.cancel_reason = False, ""
        elif op in ("retire", "unretire") and brainstorm:
            report.ignored.append("um brainstorm não aposenta cards; isso é na reunião de sprint")
        elif op in ("retire", "unretire"):
            _retire(draft, raw, cards, op == "retire", report)
        elif op == "groom" and brainstorm:
            report.ignored.append("a limpeza do backlog é na reunião de sprint")
        elif op == "groom":
            _groom(draft, raw, cards, report)
        elif op == "goal":
            goal = (_text(raw, "text", 200) or _text(raw, "goal", 200) or "").strip()
            draft.goal = goal
            report.changes.append(f"meta do sprint: {goal}" if goal else "meta do sprint removida")
        else:
            report.ignored.append(f"operação desconhecida: {op or '(vazia)'}")
    return report


def _choose_stack(draft: Draft, raw: dict[str, Any], report: OpsReport) -> None:
    """The founder's pick of a kickoff stack (plan 10.10): 1 = recommended, 2 = alternative."""
    k = draft.kickoff
    if k is None or not k.stacks:
        report.ignored.append("ainda não há proposta de stack para escolher")
        return
    try:
        n = int(raw.get("option") or raw.get("ref") or 0)
    except (TypeError, ValueError):
        n = 0
    if not 1 <= n <= len(k.stacks):
        report.ignored.append(f"a proposta de stack tem {len(k.stacks)} opção(ões)")
        return
    if k.applied and k.chosen != n:
        report.ignored.append("a stack já foi gravada na fábrica no primeiro OK")
        return
    k.chosen = n
    opt = k.stacks[n - 1]
    report.changes.append(f"stack escolhida: {opt.summary or ', '.join(opt.languages)}")


def _retire(
    draft: Draft,
    raw: dict[str, Any],
    cards: Mapping[str, OpenCard],
    retire: bool,
    report: OpsReport,
) -> None:
    """A backlog card leaves for good when the meeting is applied (cancelled by the Product
    Owner). Only a card waiting in the backlog: work in progress leaves a sprint another way."""
    ref = _ref(raw).upper()
    if not retire:
        groomed = next((g for g in draft.grooming if g.ref == ref and g.accepted), None)
        if groomed is not None:  # the whole change goes: its merged text or its new parts too
            dematerialize(draft, groomed)
            groomed.accepted = False
            report.changes.append(f"{ref} continua no backlog")
            return
        if draft.retire.pop(ref, None) is not None:
            report.changes.append(f"{ref} continua no backlog")
        return
    card = cards.get(ref)
    if card is None:
        report.ignored.append(f"não encontrei o card {ref or 'citado'} no backlog")
        return
    if not card.waiting:
        report.ignored.append(
            f"{ref} já está em andamento; só um card que espera no backlog sai assim"
        )
        return
    needed = sorted(
        c.id for c in cards.values() if ref in c.depends_on and c.id not in draft.retire
    )
    if needed:
        report.ignored.append(f"{', '.join(needed)} depende de {ref}: aposente as duas ou nenhuma")
        return
    draft.retire[ref] = (_text(raw, "reason", 300) or "").strip()
    item = next((i for i in draft.items if i.story_id == ref), None)
    if item is not None:
        draft.items.remove(item)
    report.changes.append(f"“{card.title}” ({ref}) sai do backlog ao aplicar (cancelado)")


def _groom(
    draft: Draft, raw: dict[str, Any], cards: Mapping[str, OpenCard], report: OpsReport
) -> None:
    """The founder's answer to one of the Product Owner's grooming proposals (ADR-0038): yes puts
    it in the draft, no takes it out again. The same op undoes a change the Master confirmed."""
    ref = _ref(raw).upper()
    prop = next((g for g in draft.grooming if g.ref == ref), None)
    if prop is None:
        report.ignored.append(f"não há proposta do Product Owner para {ref or 'esse card'}")
        return
    accept = _bool(raw.get("accept"))
    if accept is None:
        report.ignored.append(f"diga se aceita ou não a proposta para {ref}")
        return
    if accept and prop.accepted is not True:
        if materialize(draft, prop, cards, report):
            prop.accepted = True
    elif not accept and prop.accepted is not False:
        if prop.accepted:
            dematerialize(draft, prop)
        prop.accepted = False
        report.changes.append(f"{ref} fica no backlog como está")


def materialize(
    draft: Draft, prop: GroomProposal, cards: Mapping[str, OpenCard], report: OpsReport
) -> bool:
    """Write a grooming proposal into the draft as the edits a meeting already knows: the card is
    retired, and a merge adds its content to the card it goes into (`amend`), a split adds its
    parts as new cards. Checked like any edit: a card that started, or one another card still
    depends on, is not touched."""
    card = cards.get(prop.ref)
    if card is None or not card.waiting:
        report.ignored.append(f"{prop.ref} não está mais esperando no backlog")
        return False
    if any(prop.ref in c.depends_on for c in cards.values() if c.id != prop.ref):
        report.ignored.append(f"outro card depende de {prop.ref}: decida na conversa")
        return False
    if prop.action == "merge":
        target = cards.get(prop.into)
        if target is None or not target.waiting or target.id == prop.ref:
            report.ignored.append(
                f"{prop.into or 'o card de destino'} não está esperando no backlog"
            )
            return False
    draft.retire[prop.ref] = prop.reason or "removido na limpeza do backlog"
    if prop.action == "merge":
        item = draft.find(prop.into)
        if item is None:
            item = _pull(draft, cards[prop.into])
            prop.pulled = True
        item.amend = f"Juntado de {prop.ref} (“{card.title}”). {prop.reason}".strip()
        report.changes.append(f"{prop.ref} junta-se a {prop.into}")
    elif prop.action == "split":
        prop.keys = []
        for part in prop.parts[:4]:
            title = str(part.get("title") or "").strip()[:MAX_TITLE]
            if not title or len(draft.items) >= MAX_ITEMS:
                continue
            item = DraftItem(
                key=f"D{draft.next_key}",
                title=title,
                description=str(part.get("description") or "").strip()[:4000],
                epic=card.epic,
                priority=card.priority,
                origin=card.origin,
                note=f"parte de {prop.ref}",
            )
            draft.next_key += 1
            draft.items.append(item)
            prop.keys.append(item.key)
        report.changes.append(f"{prop.ref} dividida em {len(prop.keys)} cards")
    else:
        report.changes.append(f"{prop.ref} sai do backlog ao aplicar")
    return True


def dematerialize(draft: Draft, prop: GroomProposal) -> None:
    draft.retire.pop(prop.ref, None)
    if prop.action == "merge":
        item = draft.find(prop.into)
        if item is not None:
            item.amend = ""
            if prop.pulled and not item.in_sprint:
                draft.items.remove(item)
        prop.pulled = False
    elif prop.action == "split":
        draft.items = [i for i in draft.items if i.key not in prop.keys]
        prop.keys = []


def _dismiss(draft: Draft, raw: dict[str, Any], dismissed: bool, report: OpsReport) -> None:
    ref = _ref(raw).upper()
    opinion = next((o for o in draft.consults if o.key == ref), None)
    if opinion is None:
        report.ignored.append(f"não encontrei o parecer {ref or 'citado'}")
        return
    opinion.dismissed = dismissed
    report.changes.append(
        f"parecer {ref} ({opinion.name}) deixado de lado"
        if dismissed
        else f"parecer {ref} ({opinion.name}) de volta à conversa"
    )


def _pull(draft: Draft, card: OpenCard, *, in_sprint: bool = False) -> DraftItem:
    item = DraftItem(
        key=card.id,
        title=card.title,
        epic=card.epic,
        priority=card.priority,
        in_sprint=in_sprint,
        story_id=card.id,
        origin=card.origin,
        depends_on=list(card.depends_on),
    )
    draft.items.append(item)
    return item


def _add(
    draft: Draft,
    raw: dict[str, Any],
    by_title: Mapping[str, OpenCard],
    origin: str,
    report: OpsReport,
    *,
    noun: str = "história",
    cards: Mapping[str, OpenCard] | None = None,
) -> None:
    cards = cards or {}
    title = (_text(raw, "title", MAX_TITLE) or "").strip()
    if not title:
        report.ignored.append("uma história precisa de título")
        return
    in_sprint = bool(_bool(raw.get("in_sprint")))
    key = normalize_title(title)
    same = next((i for i in draft.items if normalize_title(i.title) == key), None)
    if same is not None:  # the model repeated itself: refine the card instead of doubling it
        _apply_fields(same, raw)
        _set_deps(draft, same, raw.get("depends_on"), cards, report)
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
    report.changes.append(f"nova {noun} “{title}”" + (" no sprint" if in_sprint else ""))
    _set_deps(draft, item, raw.get("depends_on"), cards, report)


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
    if item.story_id and _bool(raw.get("pinned")) is False:
        item.unpin = True
    return refused


def _set_deps(
    draft: Draft,
    item: DraftItem,
    raw: Any,
    cards: Mapping[str, OpenCard],
    report: OpsReport,
) -> None:
    """Which cards `item` needs delivered first (ADR-0021): draft keys or open cards' ids. An
    unknown reference is skipped, and a set that would close a cycle is refused whole."""
    from loompa.dependencies import would_cycle

    if raw is None:
        return
    refs = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else []
    wanted: list[str] = []
    for ref in (str(r).strip() for r in refs if str(r).strip()):
        target = draft.find(ref)
        key = target.key if target else (ref.upper() if ref.upper() in cards else None)
        if key is None:
            report.ignored.append(f"{item.key} depende de {ref}, que não está aberto")
        elif key == item.key:
            report.ignored.append(f"{item.key} não pode depender de si mesmo")
        elif key not in wanted:
            wanted.append(key)
    edges = {c.id: list(c.depends_on) for c in cards.values()}
    edges.update({i.key: list(i.depends_on) for i in draft.items})
    cycle = would_cycle(edges, item.key, wanted)
    if cycle:
        path = " → ".join(cycle)
        report.cycles.append(path)
        report.ignored.append(f"essa dependência fecharia um ciclo ({path}) e não entrou")
        return
    if wanted != item.depends_on:
        item.depends_on = wanted
        report.changes.append(
            f"“{item.title}” depende de {', '.join(wanted)}"
            if wanted
            else f"“{item.title}” não depende de outro card"
        )


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
    _set_deps(draft, item, raw.get("depends_on"), cards, report)
    report.changes.append(f"ajustei “{item.title}”")


def _restart(draft: Draft, raw: dict[str, Any], report: OpsReport) -> None:
    item = draft.find(_ref(raw))
    if item is None or item.story_id not in draft.members:
        report.ignored.append(f"{_ref(raw) or 'a história citada'} não está no sprint em andamento")
        return
    flag = _bool(raw.get("restart"))
    item.restart = flag is not False
    item.restart_reason = (_text(raw, "reason", 400) or "").strip() if item.restart else ""
    report.changes.append(
        f"“{item.title}” recomeça do zero ao aplicar"
        if item.restart
        else f"“{item.title}” segue de onde está"
    )


def _drop(draft: Draft, raw: dict[str, Any], report: OpsReport) -> None:
    item = draft.find(_ref(raw))
    if item is None:
        report.ignored.append(f"não encontrei {_ref(raw) or 'a história citada'} no rascunho")
        return
    if item.story_id in draft.members:
        item.in_sprint = False  # a running card leaves the sprint, never the meeting's view
        report.changes.append(f"“{item.title}” sai do sprint ao aplicar (volta ao backlog)")
        return
    draft.items.remove(item)
    for other in draft.items:  # nobody depends on a card that left the draft
        if item.key in other.depends_on:
            other.depends_on.remove(item.key)
    report.changes.append(
        f"tirei “{item.title}” do rascunho" + (" (continua no backlog)" if item.story_id else "")
    )


# ------------------------------------------------------------------------ prompt renderers


def render_draft(draft: Draft) -> str:
    if not draft.items and not draft.goal and not draft.retire and not draft.grooming:
        return "(empty)"
    lines = [f"Sprint goal: {draft.goal or '(not set)'}"]
    if draft.members:
        lines[0] += f" · this is sprint {draft.sprint_id}, running now"
        if draft.cancel_sprint:
            lines.append(f"THE SPRINT WILL BE CANCELLED: {draft.cancel_reason or '(no reason)'}")
    for i in draft.items:
        if i.story_id in draft.members:
            where = f"running ({i.stage})" if i.in_sprint else f"LEAVES THE SPRINT ({i.stage})"
            if i.restart:
                where += " · RESTARTS FROM SCRATCH"
            lines.append(f'- {i.key} · P{i.priority} · {where} · "{i.title}"')
            if i.note:
                lines.append(f"    Product Owner: {i.note[:300]}")
            continue
        where = "IN SPRINT" if i.in_sprint else "backlog only"
        if i.in_sprint and draft.members:
            where = "JOINS THE RUNNING SPRINT"
        existing = " · existing backlog card" if i.story_id else ""
        epic = f" · epic: {i.epic}" if i.epic else ""
        needs = f" · depends on {', '.join(i.depends_on)}" if i.depends_on else ""
        lines.append(f'- {i.key} · P{i.priority} · {where}{existing}{epic}{needs} · "{i.title}"')
        if i.description and not i.story_id:
            lines.append(f"    {i.description[:400]}")
        if i.note:
            lines.append(f"    Product Owner: {i.note[:300]}")
    for sid, reason in draft.retire.items():
        lines.append(f"- {sid} · LEAVES THE BACKLOG when the meeting is saved · {reason[:200]}")
    waiting = [g for g in draft.grooming if g.accepted is None]
    if waiting:
        lines.append(
            "The Product Owner's backlog proposals waiting for the founder's answer "
            '(answer with {"op": "groom", "ref": ..., "accept": true|false}):'
        )
        for g in waiting:
            what = {"merge": f"merge into {g.into}", "split": f"split in {len(g.parts)}"}.get(
                g.action, "retire"
            )
            lines.append(f"- {g.ref} · {what} · {g.reason[:200]}")
    return "\n".join(lines)


def render_opinion(o: Opinion, *, limit: int = 900) -> str:
    """One opinion as the models read it."""
    parts = [f"{o.key} · {o.name} · asked: {o.question[:200]}"]
    if o.failed:
        return parts[0] + "\n    (could not be asked)"
    parts.append(f"    Summary: {o.summary[:limit]}")
    if o.attention:
        parts.append("    Attention: " + "; ".join(a[:200] for a in o.attention[:5]))
    if o.cost:
        parts.append(f"    Cost: {o.cost[:300]}")
    if o.benefit:
        parts.append(f"    Benefit: {o.benefit[:300]}")
    if o.counterpoints:
        parts.append("    Counterpoints: " + "; ".join(c[:200] for c in o.counterpoints[:4]))
    if o.dismissed:
        parts.append("    (the founder set this opinion aside: do not weigh it)")
    return "\n".join(parts)


def render_brainstorm(draft: Draft) -> str:
    """A brainstorm's draft: the direction, the ideas and the opinions given so far."""
    lines = [f"Direction: {draft.direction or '(not set yet)'}"]
    if draft.approved is not None:
        lines.append("The founder APPROVED this direction; the Product Owner is splitting it.")
    lines.append("Ideas:")
    for i in draft.items:
        existing = f" · builds on backlog card {i.story_id}" if i.story_id else ""
        lines.append(f'- {i.key} · P{i.priority}{existing} · "{i.title}"')
        if i.description and not i.story_id:
            lines.append(f"    {i.description[:400]}")
        if i.note:
            lines.append(f"    Product Owner: {i.note[:300]}")
    if not draft.items:
        lines.append("(none yet)")
    if draft.consults:
        lines.append("Opinions given (preliminary):")
        lines += [render_opinion(o, limit=500) for o in draft.consults[-8:]]
    if draft.kickoff is not None:
        lines.append(render_kickoff(draft.kickoff))
    return "\n".join(lines)


def render_kickoff(k: Kickoff) -> str:
    """The kickoff's own fields, as the models read them (plan 10.10)."""
    lines = [
        "KICKOFF of a new product (no code yet). Before the founder's first OK the direction needs "
        "a vision, the v1 features (the ideas) and a chosen stack.",
        f"Vision: {k.vision or '(not set yet)'}",
        "Out of v1: " + ("; ".join(k.out_of_v1) if k.out_of_v1 else "(nothing said yet)"),
    ]
    for n, opt in enumerate(k.stacks, 1):
        tag = " · CHOSEN by the founder" if k.chosen == n else ""
        lines.append(
            f"Stack option {n}{tag}: {', '.join(opt.languages + opt.frameworks)} · tests: "
            f"{opt.test_command or '?'} · {opt.summary}"
        )
    if not k.stacks:
        lines.append("Stack: not proposed yet (the Architect proposes it).")
    return "\n".join(lines)


def render_backlog(
    cards: Mapping[str, OpenCard],
    *,
    limit: int = 60,
    delivered: Sequence[DeliveredCard] = (),
) -> str:
    waiting = [c for c in cards.values() if c.waiting]
    busy = [c for c in cards.values() if not c.waiting]
    lines = ["Cards waiting in the backlog (reference them by id):"]
    for c in waiting[:limit]:
        tags = [f"P{c.priority}"]
        if c.pinned:
            tags.append("pinned by the founder")
        tags.append(c.origin)
        if c.kind not in ("", "feature"):
            tags.append(c.kind)
        if c.epic:
            tags.append(f"epic: {c.epic}")
        if c.depends_on:
            tags.append(f"depends on {', '.join(c.depends_on)}")
        lines.append(f'- {c.id} · {" · ".join(tags)} · "{c.title}"')
    if not waiting:
        lines.append("(none)")
    if busy:
        lines.append("Work already in progress (never duplicate it):")
        lines += [f'- {c.id} · {c.stage} · "{c.title}"' for c in busy[:limit]]
    if delivered:
        lines.append(render_delivered(delivered))
    return "\n".join(lines)


def render_delivered(cards: Sequence[DeliveredCard]) -> str:
    """The delivered stories, newest first. A meeting that saw only open cards drafted five
    stories the product already had (tamagotchi SP-005: decay, care actions, consequences and
    restart, delivered an hour before), with needs the code does not use."""
    lines = [
        "Already delivered and approved by the founder (the product does this today; never draft "
        "it again, and use the names it uses):"
    ]
    for c in cards:
        tag = f" · {c.kind}" if c.kind not in ("", "feature") else ""
        lines.append(f'- {c.id}{tag} · "{c.title}"' + (f" — {c.summary}" if c.summary else ""))
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
                pinned=bool(row.get("priority_pinned")),
                kind=str((row.get("state") or {}).get("kind") or ""),
                depends_on=tuple(row.get("depends_on") or ()),
            )
            for row in self.store.list_stories(self.slug)
            if row["stage"] not in TERMINAL
        }

    def delivered(self, limit: int = DELIVERED_LIMIT) -> list[DeliveredCard]:
        """The delivered stories, newest first, each with the opening of its description."""
        rows = [r for r in self.store.list_stories(self.slug, stage=Stage.DONE) if r.get("title")]
        rows.sort(key=lambda r: r.get("updated_at") or "", reverse=True)
        out = []
        for row in rows[:limit]:
            text = " ".join(str(row.get("description") or "").split())
            if len(text) > DELIVERED_SUMMARY_CHARS:
                text = text[:DELIVERED_SUMMARY_CHARS].rsplit(" ", 1)[0] + "…"
            out.append(
                DeliveredCard(
                    id=row["id"],
                    title=row["title"],
                    summary=text,
                    kind=str((row.get("state") or {}).get("kind") or ""),
                )
            )
        return out
