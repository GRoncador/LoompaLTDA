"""Sprints: a named batch of stories the factory works through together (ADR-0008).

A sprint is `open` while the founder and the Master are still choosing what goes in, `running`
once it has started (its stories were admitted by the Product Owner and the Scheduler works on
them) and `closed` when every story reached a terminal state. A story blocked on the founder
waits alone; the rest of the batch keeps going. Sprints only group stories: admitting a card
into the pipeline is the Product Owner's call (`Backlog.admit`).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field

from loompa.engine.state import Stage
from loompa.store import Store, now_iso


class SprintError(ValueError):
    pass


class SprintStatus(StrEnum):
    OPEN = "open"
    RUNNING = "running"
    CLOSED = "closed"


class Sprint(BaseModel):
    id: str
    factory: str
    goal: str = ""
    status: SprintStatus = SprintStatus.OPEN
    story_ids: list[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=now_iso)
    started_at: str | None = None
    closed_at: str | None = None


class SprintBoard:
    def __init__(self, store: Store, slug: str):
        self.store = store
        self.slug = slug

    # ------------------------------------------------------------------ reads
    def get(self, sprint_id: str) -> Sprint | None:
        row = self.store.get_sprint(sprint_id)
        return Sprint.model_validate(row) if row else None

    def sprints(self, status: SprintStatus | None = None) -> list[Sprint]:
        rows = self.store.list_sprints(self.slug, status.value if status else None)
        return [Sprint.model_validate(r) for r in rows]

    def open_sprint(self) -> Sprint | None:
        """The sprint being planned, if any."""
        opened = self.sprints(SprintStatus.OPEN)
        return opened[-1] if opened else None

    def sprint_of(self, story_id: str) -> Sprint | None:
        """The open or running sprint a story belongs to."""
        for sprint in self.sprints():
            if sprint.status != SprintStatus.CLOSED and story_id in sprint.story_ids:
                return sprint
        return None

    def progress(self, sprint: Sprint) -> dict[str, int]:
        counts = {"total": len(sprint.story_ids), "done": 0, "cancelled": 0, "waiting": 0}
        for sid in sprint.story_ids:
            row = self.store.get_story(sid)
            stage = row["stage"] if row else Stage.CANCELLED
            if stage == Stage.DONE:
                counts["done"] += 1
            elif stage == Stage.CANCELLED:
                counts["cancelled"] += 1
            elif stage == Stage.AWAITING_FOUNDER:
                counts["waiting"] += 1
        return counts

    # ----------------------------------------------------------------- writes
    def draft(self) -> Sprint:
        """The open sprint, created when there is none."""
        sprint = self.open_sprint()
        if sprint is None:
            sprint = Sprint(id=self.store.next_sprint_id(self.slug), factory=self.slug)
            self.store.put_sprint(sprint.model_dump())
        return sprint

    def add(self, story_id: str, sprint_id: str | None = None) -> Sprint:
        """Put a story in a sprint (the open one by default). Open and running sprints accept
        stories; a story belongs to one sprint at a time."""
        sprint = self.get(sprint_id) if sprint_id else self.draft()
        if sprint is None:
            raise SprintError(f"sprint {sprint_id} não existe")
        if sprint.status == SprintStatus.CLOSED:
            raise SprintError(f"{sprint.id} já foi encerrado")
        row = self.store.get_story(story_id)
        if row is None:
            raise SprintError(f"história {story_id} não existe")
        if row["stage"] != Stage.BACKLOG:
            raise SprintError(f"{story_id} não está esperando no backlog")
        other = self.sprint_of(story_id)
        if other is not None and other.id != sprint.id:
            raise SprintError(f"{story_id} já está no {other.id}")
        if story_id not in sprint.story_ids:
            sprint.story_ids.append(story_id)
            self.store.put_sprint(sprint.model_dump())
        return sprint

    def remove(self, story_id: str) -> None:
        sprint = self.sprint_of(story_id)
        if sprint is None or sprint.status != SprintStatus.OPEN:
            raise SprintError(f"{story_id} não está em um sprint aberto")
        sprint.story_ids.remove(story_id)
        self.store.put_sprint(sprint.model_dump())

    def start(self, sprint_id: str, goal: str = "") -> Sprint:
        sprint = self.get(sprint_id)
        if sprint is None or sprint.status != SprintStatus.OPEN:
            raise SprintError(f"{sprint_id} não está aberto")
        if not sprint.story_ids:
            raise SprintError("um sprint precisa de pelo menos uma história")
        sprint.status = SprintStatus.RUNNING
        sprint.started_at = now_iso()
        sprint.goal = goal.strip() or sprint.goal
        self.store.put_sprint(sprint.model_dump())
        return sprint

    def close(self, sprint_id: str) -> Sprint:
        sprint = self.get(sprint_id)
        if sprint is None:
            raise SprintError(f"sprint {sprint_id} não existe")
        sprint.status = SprintStatus.CLOSED
        sprint.closed_at = now_iso()
        self.store.put_sprint(sprint.model_dump())
        return sprint
