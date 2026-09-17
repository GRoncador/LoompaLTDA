"""Typed story state persisted at every checkpoint."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class Stage(StrEnum):
    BACKLOG = "BACKLOG"
    SPEC = "SPEC"
    PLAN = "PLAN"
    DEV = "DEV"
    TEST = "TEST"
    REVIEW = "REVIEW"
    AWAITING_FOUNDER = "AWAITING_FOUNDER"
    DONE = "DONE"
    CANCELLED = "CANCELLED"


TERMINAL = {Stage.DONE, Stage.CANCELLED}
PAUSED = {Stage.AWAITING_FOUNDER}
KANBAN_COLUMNS = [
    ("BACKLOG", "Backlog"),
    ("SPEC", "Especificação"),
    ("DEV", "Em Dev"),
    ("TEST", "Em Testes"),
    ("AWAITING_FOUNDER", "Aguardando Você"),
    ("DONE", "Concluído"),
]


def kanban_column(stage: str) -> str:
    return {"PLAN": "SPEC", "REVIEW": "TEST", "CANCELLED": "DONE"}.get(stage, stage)


class BlockedReason(StrEnum):
    QUESTION = "question"  # agent asked for a decision
    PERSISTENT_FAILURE = "persistent_failure"  # escalation ladder exhausted
    DELIVERY = "delivery"  # waiting for founder to approve a delivery
    CONFLICT = "conflict"  # merge conflict with base


class StoryState(BaseModel):
    story_id: str
    title: str
    description: str = ""
    epic: str = ""
    stage: Stage = Stage.BACKLOG
    resume_stage: Stage | None = None  # where to continue after the founder answers
    blocked_reason: BlockedReason | None = None
    blocked_message_id: str | None = None
    founder_notes: list[str] = Field(default_factory=list)  # answers/guidance accumulated

    spec_ready: bool = False
    plan_ready: bool = False
    acceptance: list[str] = Field(default_factory=list)
    allowed_paths: list[str] = Field(default_factory=list)

    worktree: str = ""
    branch: str = ""
    tasks_total: int = 0
    tasks_done: list[int] = Field(default_factory=list)
    commits: list[str] = Field(default_factory=list)

    attempts_tier2: int = 0
    attempts_tier1: int = 0
    current_tier: str = "tier2"
    last_test_summary: str = ""
    failure_history: list[str] = Field(default_factory=list)
    worker_summary: str = ""
    review_notes: str = ""
    pr_url: str | None = None
    merged_sha: str | None = None
    delivery_summary: str = ""

    learnings: list[dict[str, str]] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_terminal(self) -> bool:
        return self.stage in TERMINAL

    @property
    def is_paused(self) -> bool:
        return self.stage in PAUSED

    def note(self, text: str) -> None:
        if text and text not in self.founder_notes:
            self.founder_notes.append(text)
