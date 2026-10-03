"""Backlog: the single write path for story cards (ADR-0006 §5, ADR-0008, ADR-0017).

Only the Product Owner Loompa may hold a `Backlog`; every other agent (Master, Kaizen, the
dashboard, the founder's answers) asks the Product Owner. That keeps duplicate detection,
priorities and admission to the pipeline in one place. The engine still writes a story's
*progress* (stage, checkpoints, cost) through the store, but never creates, ranks, admits or
cancels a card.

Order (ADR-0017): a new card is slotted after one named card and no other card changes place;
the whole backlog is re-ranked only when a planning session closes, and a card the founder
dragged (`priority_pinned`) is a fence that ranking never moves nor lets another card pass.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from loompa.engine.state import TERMINAL, Stage, StoryKind, StoryState

if TYPE_CHECKING:
    from loompa.agents.base import LoompaAgent
    from loompa.engine.context import EngineContext

OWNER_ROLE = "product_owner"
DEFAULT_PRIORITY = 300
MIN_PRIORITY, MAX_PRIORITY = 1, 999
SETTABLE_STATUS = (Stage.BACKLOG, Stage.CANCELLED)
TOP = "top"  # `after` value: the new card goes before every waiting card
# state.extra: how the Product Owner read the request that became this card (ADR-0017)
TRIAGE_KEY = "po_triage"
REFUSAL_CODES = ("duplicate", "contradicts", "vague", "too_big")


class BacklogError(ValueError):
    pass


class BacklogAuthorityError(PermissionError):
    """Someone other than the Product Owner tried to write the backlog."""


class Triage(BaseModel):
    """The Product Owner's reading of a request before it becomes a card (ADR-0017). Even a
    refusal carries the card as the Product Owner would file it: the founder has the last word."""

    admit: bool = True
    reason_code: str = ""  # one of REFUSAL_CODES when refused
    reason: str = ""  # for the founder, plain language
    duplicate_of: str = ""
    process_only: bool = False  # a finding that changes nothing in the product: not filed
    title: str = ""
    description: str = ""
    kind: str = ""  # StoryKind value, "" when the Product Owner did not say
    epic: str = ""
    after: str | None = None  # slot right after this waiting card; TOP = first; None = unplaced
    reviewed: bool = True  # False: the model could not be asked, the request went in as written

    def record(self, **extra: Any) -> dict[str, Any]:
        """What the card keeps of its admission (`state.extra[TRIAGE_KEY]`)."""
        keep = {"reviewed": self.reviewed, "kind": self.kind, "reason": self.reason}
        if not self.admit:
            keep["reason_code"] = self.reason_code
        return {**keep, **extra}


@dataclass
class Admission:
    story_id: str
    created: bool  # False when an equivalent open card already existed
    duplicate_of: str | None = None


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


# A file named in a finding: `debug.txt`, `src/app/cli.py`. The spec artifacts every story has
# say nothing about which finding it is.
_PATH_TOKEN = re.compile(r"[\w\-/]+\.[a-z]{1,5}\b", re.I)
_GENERIC_PATHS = {
    "plan.md",
    "spec.md",
    "tasks.md",
    "research.md",
    "constitution.md",
    "learnings.md",
}


def file_tokens(text: str) -> set[str]:
    return {
        t.lower()
        for t in _PATH_TOKEN.findall(text or "")
        if t.lower().rsplit("/", 1)[-1] not in _GENERIC_PATHS and not t[0].isdigit()
    }


class Backlog:
    def __init__(self, ctx: EngineContext, *, owner: LoompaAgent):
        if getattr(owner, "role", None) != OWNER_ROLE:
            raise BacklogAuthorityError("só o Product Owner escreve no backlog")
        self.ctx = ctx
        self.agent = owner.name

    # ------------------------------------------------------------------ reads
    def find_duplicate(self, title: str, *, origin: str = "", text: str = "") -> str | None:
        """Id of an open card with the same (normalized) title. For Kaizen findings, also an
        open Kaizen card about the same file: one leftover file once came back as four cards
        worded by the Worker, the Kaizen loop and two Inspector findings (`contas` S-005)."""
        key = normalize_title(title)
        if not key:
            return None
        files = file_tokens(f"{title} {text}") if origin == "kaizen" else set()
        for row in self.ctx.store.list_stories(self.ctx.slug):
            if row["stage"] in TERMINAL:
                continue
            if normalize_title(row["title"]) == key:
                return row["id"]
            if (
                files
                and row["stage"] == Stage.BACKLOG
                and row.get("origin") == "kaizen"
                and files & file_tokens(f"{row['title']} {row.get('description', '')}")
            ):
                return row["id"]
        return None

    def _row(self, story_id: str) -> dict:
        row = self.ctx.store.get_story(story_id)
        if row is None:
            raise BacklogError(f"história {story_id} não existe")
        return row

    # ----------------------------------------------------------------- writes
    def add_item(
        self,
        title: str,
        description: str = "",
        *,
        epic: str = "",
        priority: int = DEFAULT_PRIORITY,
        origin: str = "founder",
        founder_notes: list[str] | None = None,
        kind: str = "",
        extra: dict[str, Any] | None = None,
        after: str | None = None,
    ) -> Admission:
        """Create a card in the backlog, or point at the open card that already says the same.
        `after` slots the new card right after that waiting card (`TOP`: first) without moving
        any other; `kind` and `extra` keep what the Product Owner decided when it read it."""
        title = title.strip()[:120]
        if not title:
            raise BacklogError("uma história precisa de título")
        dup = self.find_duplicate(title, origin=origin, text=description)
        if dup:
            self.ctx.emit("backlog.duplicate", story_id=dup, agent=self.agent, title=title)
            return Admission(dup, created=False, duplicate_of=dup)
        story_id = self.ctx.store.next_story_id(self.ctx.slug)
        state = StoryState(
            story_id=story_id,
            title=title,
            description=description.strip(),
            epic=epic.strip(),
            founder_notes=list(founder_notes or []),
            extra=dict(extra or {}),
        )
        if kind in {k.value for k in StoryKind}:
            state.kind = StoryKind(kind)
        self.ctx.store.upsert_story(
            {
                "id": story_id,
                "factory": self.ctx.slug,
                "title": title,
                "description": state.description,
                "epic": state.epic,
                "stage": Stage.BACKLOG,
                "priority": self._clamp(priority),
                "origin": origin,
                "state": state.model_dump(mode="json"),
            }
        )
        self.ctx.emit(
            "story.created", story_id=story_id, agent=self.agent, title=title, origin=origin
        )
        if after is not None:
            self.place(story_id, after)
        return Admission(story_id, created=True)

    def set_priority(self, story_id: str, priority: int) -> None:
        self._row(story_id)
        self.ctx.store.update_story(story_id, priority=self._clamp(priority))
        self.ctx.emit(
            "backlog.priority", story_id=story_id, agent=self.agent, priority=self._clamp(priority)
        )

    def reorder(self, story_ids: list[str], *, dragged: str | None = None) -> list[str]:
        """The founder dragged the backlog into this order: first is next. Only cards still in
        the backlog move; the rest (already running or done) are skipped and not returned. The
        card the founder dragged is pinned there: the Product Owner's ranking leaves it."""
        moved: list[str] = []
        for sid in story_ids:
            row = self.ctx.store.get_story(sid)
            if row is None or row["stage"] != Stage.BACKLOG or sid in moved:
                continue
            moved.append(sid)
        self._spread(moved)
        if dragged in moved:
            self.pin(dragged, True)
        return moved

    def pin(self, story_id: str, pinned: bool) -> None:
        """Pin a card where the founder put it, or hand its place back to the Product Owner."""
        row = self._row(story_id)
        if bool(row.get("priority_pinned")) == pinned:
            return
        self.ctx.store.update_story(story_id, priority_pinned=int(pinned))
        self.ctx.emit("backlog.pinned", story_id=story_id, agent=self.agent, pinned=pinned)

    def waiting(self) -> list[dict[str, Any]]:
        """Cards waiting in the backlog, first to be built first."""
        return self.ctx.store.list_stories(self.ctx.slug, stage=Stage.BACKLOG)

    def place(self, story_id: str, after: str) -> bool:
        """Slot a waiting card right after `after` (`TOP`: before all) without changing anyone
        else's place: other numbers only grow where they must to keep the order. False when the
        card or the reference is not waiting in the backlog (the card keeps its number)."""
        rows = self.waiting()
        me = next((r for r in rows if r["id"] == story_id), None)
        others = [r for r in rows if r["id"] != story_id]
        if me is None:
            return False
        if after == TOP or not others:
            order = [me, *others]
            start = max(MIN_PRIORITY, others[0]["priority"] - 1) if others else me["priority"]
        else:
            idx = next((i for i, r in enumerate(others) if r["id"] == after), None)
            if idx is None:
                return False
            order = [*others[: idx + 1], me, *others[idx + 1 :]]
            start = others[idx]["priority"]
        wanted: dict[str, int] = {}
        last: tuple[int, str] | None = None
        for row in order:
            number = start if row is me else row["priority"]
            if last is not None and (number, row["created_at"]) <= last:
                # equal numbers sort by creation: a newer card can share its predecessor's
                number = last[0] if row["created_at"] > last[1] else last[0] + 1
            last = (number, row["created_at"])
            wanted[row["id"]] = number
        if max(wanted.values()) > MAX_PRIORITY:  # no room left at the bottom: even spread
            self._spread([r["id"] for r in order])
        else:
            for row in order:
                if wanted[row["id"]] != row["priority"]:
                    self.set_priority(row["id"], wanted[row["id"]])
        self.ctx.emit("backlog.placed", story_id=story_id, agent=self.agent, after=after)
        return True

    def rerank(self, ranked: list[str]) -> list[str]:
        """Apply the Product Owner's ranking of the waiting cards. Pinned cards are fences: they
        keep their place and no card crosses them, so the ranking only reorders the cards between
        two pins. Cards the ranking left out keep their place. Returns the ids that moved."""
        rows = self.waiting()
        current = [r["id"] for r in rows]
        pinned = {r["id"] for r in rows if r.get("priority_pinned")}
        ranked = [sid for sid in dict.fromkeys(ranked) if sid in current and sid not in pinned]
        final: list[str] = []
        segment: list[str] = []
        for sid in [*current, None]:
            if sid is not None and sid not in pinned:
                segment.append(sid)
                continue
            mentioned = [r for r in ranked if r in segment]
            slots = iter(mentioned)
            final += [next(slots) if s in mentioned else s for s in segment]
            segment = []
            if sid is not None:
                final.append(sid)
        moved = [a for a, b in zip(final, current, strict=True) if a != b]
        if moved:
            self._spread(final)
        self.ctx.emit("backlog.reranked", agent=self.agent, moved=len(moved))
        return moved

    def _spread(self, ordered: list[str]) -> None:
        """Even numbers in this order, first is next (what a drag and a full ranking write)."""
        step = max(1, (MAX_PRIORITY - MIN_PRIORITY) // max(len(ordered) + 1, 10))
        for i, sid in enumerate(ordered):
            number = self._clamp(MIN_PRIORITY + step * (i + 1))
            row = self.ctx.store.get_story(sid)
            if row is not None and row["priority"] != number:
                self.set_priority(sid, number)

    def set_dependencies(self, story_id: str, depends_on: list[str]) -> list[str]:
        """Record which cards `story_id` needs delivered first (ADR-0021). Refused in code: an
        unknown card, the card itself, a finished card gaining dependencies, and any cycle.
        Returns the list as saved."""
        from loompa.dependencies import deps_of, edges_of, would_cycle

        row = self._row(story_id)
        wanted = list(dict.fromkeys(d.strip().upper() for d in depends_on if d and d.strip()))
        if wanted == deps_of(row):
            return wanted
        if story_id in wanted:
            raise BacklogError(f"{story_id} não pode depender de si mesma")
        if wanted and row["stage"] in TERMINAL:
            raise BacklogError(f"{story_id} já está encerrada")
        for dep in wanted:
            self._row(dep)
        cycle = would_cycle(edges_of(self.ctx.store.list_stories(self.ctx.slug)), story_id, wanted)
        if cycle:
            raise BacklogError(f"isso criaria um ciclo de dependências: {' → '.join(cycle)}")
        self.ctx.store.update_story(story_id, depends_on=wanted)
        self.ctx.emit("backlog.depends", story_id=story_id, agent=self.agent, depends_on=wanted)
        return wanted

    def amend(self, story_id: str, addition: str, *, source: str = "") -> bool:
        """Add to a waiting card what a session decided about it (a brainstorm's split,
        ADR-0020): the text goes at the end of its description, and the card keeps a record of
        each addition. Only a card still waiting in the backlog changes; work in progress keeps
        the spec it started from. False when nothing was added."""
        addition = addition.strip()
        row = self._row(story_id)
        if not addition or row["stage"] != Stage.BACKLOG:
            return False
        state = StoryState.from_row(row)
        lead = f"Acrescentado ({source}):" if source else "Acrescentado:"
        state.description = f"{state.description}\n\n{lead} {addition}".strip()
        state.extra.setdefault("amended", []).append({"source": source, "text": addition})
        self.ctx.store.update_story(
            story_id, description=state.description, state=state.model_dump(mode="json")
        )
        self.ctx.emit("backlog.amended", story_id=story_id, agent=self.agent, source=source)
        return True

    def set_status(self, story_id: str, status: Stage) -> None:
        """Back to the backlog (deferred) or cancelled. Every other stage belongs to the engine."""
        status = Stage(status)
        if status not in SETTABLE_STATUS:
            raise BacklogError(
                f"o backlog só define {', '.join(SETTABLE_STATUS)}; recebeu {status}"
            )
        row = self._row(story_id)
        if row["stage"] in TERMINAL:
            raise BacklogError(f"{story_id} já está encerrada")
        state = StoryState.from_row(row)
        state.stage = status
        if status == Stage.CANCELLED:
            state.phase = ""
        self.ctx.store.update_story(
            story_id, stage=status.value, state=state.model_dump(mode="json")
        )
        self.ctx.emit("backlog.status", story_id=story_id, agent=self.agent, status=status.value)

    def admit(self, story_id: str) -> bool:
        """Let a backlog card into the pipeline: it leaves BACKLOG and starts at intake.
        Returns False when the card was not waiting in the backlog."""
        row = self._row(story_id)
        if row["stage"] != Stage.BACKLOG:
            return False
        state = StoryState.from_row(row)
        state.stage = Stage.SPEC  # the kanban leaves the backlog; intake still classifies it
        state.phase = "intake"
        self.ctx.store.update_story(
            story_id, stage=state.stage.value, state=state.model_dump(mode="json")
        )
        self.ctx.store.checkpoint(
            story_id, "admit", state.stage.value, state.model_dump(mode="json")
        )
        self.ctx.emit("story.stage", story_id=story_id, stage=state.stage.value, node="admit")
        self.ctx.emit("story.promoted", story_id=story_id, agent=self.agent)
        return True

    @staticmethod
    def _clamp(priority: int) -> int:
        return max(MIN_PRIORITY, min(MAX_PRIORITY, int(priority)))
