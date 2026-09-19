"""Backlog: the single write path for story cards (ADR-0006 §5, ADR-0008).

Only the Product Owner Loompa may hold a `Backlog`; every other agent (Master, Kaizen, the
dashboard, the founder's answers) asks the Product Owner. That keeps duplicate detection,
priorities and admission to the pipeline in one place. The engine still writes a story's
*progress* (stage, checkpoints, cost) through the store, but never creates, ranks, admits or
cancels a card.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import TYPE_CHECKING

from loompa.engine.state import TERMINAL, Stage, StoryState

if TYPE_CHECKING:
    from loompa.agents.base import LoompaAgent
    from loompa.engine.context import EngineContext

OWNER_ROLE = "product_owner"
DEFAULT_PRIORITY = 300
MIN_PRIORITY, MAX_PRIORITY = 1, 999
SETTABLE_STATUS = (Stage.BACKLOG, Stage.CANCELLED)


class BacklogError(ValueError):
    pass


class BacklogAuthorityError(PermissionError):
    """Someone other than the Product Owner tried to write the backlog."""


@dataclass
class Admission:
    story_id: str
    created: bool  # False when an equivalent open card already existed
    duplicate_of: str | None = None


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


class Backlog:
    def __init__(self, ctx: EngineContext, *, owner: LoompaAgent):
        if getattr(owner, "role", None) != OWNER_ROLE:
            raise BacklogAuthorityError("só o Product Owner escreve no backlog")
        self.ctx = ctx
        self.agent = owner.name

    # ------------------------------------------------------------------ reads
    def find_duplicate(self, title: str) -> str | None:
        """Id of an open card with the same (normalized) title."""
        key = normalize_title(title)
        if not key:
            return None
        for row in self.ctx.store.list_stories(self.ctx.slug):
            if row["stage"] not in TERMINAL and normalize_title(row["title"]) == key:
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
    ) -> Admission:
        """Create a card in the backlog, or point at the open card that already says the same."""
        title = title.strip()[:120]
        if not title:
            raise BacklogError("uma história precisa de título")
        dup = self.find_duplicate(title)
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
        )
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
        return Admission(story_id, created=True)

    def set_priority(self, story_id: str, priority: int) -> None:
        self._row(story_id)
        self.ctx.store.update_story(story_id, priority=self._clamp(priority))
        self.ctx.emit(
            "backlog.priority", story_id=story_id, agent=self.agent, priority=self._clamp(priority)
        )

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
