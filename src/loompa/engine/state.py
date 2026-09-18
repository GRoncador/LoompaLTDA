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
    WAIVER = "waiver"  # Inspector found a serious concern the tests do not catch; founder decides


class StoryKind(StrEnum):
    FEATURE = "feature"
    BUGFIX = "bugfix"
    RESEARCH = "research"


class Complexity(StrEnum):
    SIMPLE = "SIMPLE"  # everything on tier2, no spec review
    STANDARD = "STANDARD"
    COMPLEX = "COMPLEX"  # product/review roles lifted to tier1


class QAVerdict(StrEnum):
    PASS = "PASS"
    CONCERNS = "CONCERNS"  # medium/low findings: delivered, findings feed the Kaizen loop
    FAIL = "FAIL"
    WAIVED = "WAIVED"  # high-severity finding with green tests: founder waives or sends back


class StoryState(BaseModel):
    story_id: str
    title: str
    description: str = ""
    epic: str = ""
    stage: Stage = Stage.BACKLOG
    # Pipeline as data (ADR-0006): the route is an ordered list of phase names decided at
    # intake; `phase` is the phase to run next. `stage` stays the kanban projection.
    kind: StoryKind = StoryKind.FEATURE
    complexity: Complexity = Complexity.STANDARD
    route: list[str] = Field(default_factory=list)
    phase: str = ""
    handoff: dict[str, str] = Field(default_factory=dict)  # phase -> notes for the next phase
    resume_stage: Stage | None = None  # where to continue after the founder answers
    resume_phase: str | None = None
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
    spec_review_rounds: int = 0
    qa_verdict: QAVerdict | None = None
    qa_findings: list[dict[str, str]] = Field(default_factory=list)  # {id, severity, text}
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

    # ------------------------------------------------------------------ route
    def next_phase(self, after: str | None = None) -> str | None:
        """Phase that follows `after` (default: the current one) in the route."""
        current = after if after is not None else self.phase
        if current in self.route:
            i = self.route.index(current)
            return self.route[i + 1] if i + 1 < len(self.route) else None
        return self.route[0] if self.route else None

    def hand_off(self, text: str, *, phase: str | None = None) -> None:
        if text:
            self.handoff[phase or self.phase] = text.strip()

    def handoff_from(self, *phases: str) -> str:
        return "\n".join(f"[{p}] {self.handoff[p]}" for p in phases if self.handoff.get(p))
