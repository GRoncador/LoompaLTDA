"""Sprints: a named batch of stories the factory works through together (ADR-0008).

A sprint is `open` while the founder and the Master are still choosing what goes in (the next
sprint, possibly already assembled while another runs), `running` once it has started (its
stories were admitted by the Product Owner and the Scheduler works on them), `closed` when every
story reached a terminal state and `cancelled` when the founder called it off in a meeting. One
sprint runs at a time (ADR-0018). A story blocked on the founder waits alone; the rest of the
batch keeps going. Sprints only group stories: admitting a card into the pipeline is the Product
Owner's call (`Backlog.admit`).
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
    CANCELLED = "cancelled"


ACTIVE = (SprintStatus.OPEN, SprintStatus.RUNNING)


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
        """The sprint being planned, if any: the next one, waiting for a meeting to start it."""
        opened = self.sprints(SprintStatus.OPEN)
        return opened[-1] if opened else None

    def running(self) -> Sprint | None:
        """The sprint the factory is working through. There is at most one (ADR-0018)."""
        running = self.sprints(SprintStatus.RUNNING)
        return running[-1] if running else None

    def sprint_of(self, story_id: str) -> Sprint | None:
        """The open or running sprint a story belongs to."""
        for sprint in self.sprints():
            if sprint.status in ACTIVE and story_id in sprint.story_ids:
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
        if sprint.status not in ACTIVE:
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

    def set_goal(self, sprint_id: str, goal: str) -> Sprint:
        sprint = self.get(sprint_id)
        if sprint is None:
            raise SprintError(f"sprint {sprint_id} não existe")
        if goal.strip():
            sprint.goal = goal.strip()
            self.store.put_sprint(sprint.model_dump())
        return sprint

    def withdraw(self, story_id: str) -> Sprint:
        """Take a story out of the running sprint (the founder's call in a meeting)."""
        sprint = self.running()
        if sprint is None or story_id not in sprint.story_ids:
            raise SprintError(f"{story_id} não está no sprint em andamento")
        sprint.story_ids.remove(story_id)
        self.store.put_sprint(sprint.model_dump())
        return sprint

    def start(self, sprint_id: str, goal: str = "") -> Sprint:
        sprint = self.get(sprint_id)
        if sprint is None or sprint.status != SprintStatus.OPEN:
            raise SprintError(f"{sprint_id} não está aberto")
        if not sprint.story_ids:
            raise SprintError("um sprint precisa de pelo menos uma história")
        busy = self.running()
        if busy is not None:
            raise SprintError(running_message(busy))
        sprint.status = SprintStatus.RUNNING
        sprint.started_at = now_iso()
        sprint.goal = goal.strip() or sprint.goal
        self.store.put_sprint(sprint.model_dump())
        return sprint

    def close(self, sprint_id: str, *, cancelled: bool = False) -> Sprint:
        sprint = self.get(sprint_id)
        if sprint is None:
            raise SprintError(f"sprint {sprint_id} não existe")
        sprint.status = SprintStatus.CANCELLED if cancelled else SprintStatus.CLOSED
        sprint.closed_at = now_iso()
        self.store.put_sprint(sprint.model_dump())
        return sprint


def running_message(sprint: Sprint) -> str:
    return (
        f"o {sprint.id} ainda está rodando e só um sprint roda por vez: ele precisa terminar, "
        "ou ser cancelado numa reunião de sprint, antes de outro começar"
    )
