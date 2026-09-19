"""Phase registry: the pipeline as data (ADR-0006).

A phase is a node function plus who owns it, who reviews it and which kanban stage it projects
to. A story's `route` is an ordered list of phase names decided at intake from its kind and
complexity; the graph edge simply reads `state.phase`. Adding a phase means registering it here
and putting it in a route: no graph surgery.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from loompa.engine.state import Complexity, Stage, StoryKind, StoryState

Node = Callable[..., Awaitable[StoryState]]


@dataclass(frozen=True)
class Phase:
    name: str
    node: Node
    stage: Stage  # kanban column while the phase runs
    owner: str  # agent role that does the work
    reviewer: str | None = None  # agent role that reviews the output (the consumer)
    description: str = ""


PHASES: dict[str, Phase] = {}


def register(phase: Phase) -> Phase:
    PHASES[phase.name] = phase
    return phase


def phase_stage(name: str) -> Stage:
    return PHASES[name].stage if name in PHASES else Stage.BACKLOG


# ---------------------------------------------------------------------------- routes

BASE_ROUTE = ["intake", "spec", "spec_review", "plan", "dev", "test", "review"]
# Research produces knowledge, not code: no worktree, no merge. The Product Owner reviews the
# report, then the founder reads it (`research_review` ends by pausing for the founder).
RESEARCH_ROUTE = ["intake", "research", "research_review"]


def build_route(kind: StoryKind | str, complexity: Complexity | str) -> list[str]:
    """Ordered phases for a story, from its kind and complexity."""
    kind = StoryKind(kind)
    complexity = Complexity(complexity)
    if kind == StoryKind.RESEARCH:
        return list(RESEARCH_ROUTE)
    route = list(BASE_ROUTE)
    if (
        complexity == Complexity.SIMPLE
        or kind == StoryKind.BUGFIX
        and complexity != Complexity.COMPLEX
    ):
        route.remove("spec_review")  # cheap stories: the Inspector gate is enough
    return route


# Legacy stories (persisted before routes existed) resume from their kanban stage.
STAGE_TO_PHASE: dict[Stage, str] = {
    Stage.BACKLOG: "intake",
    Stage.SPEC: "spec",
    Stage.PLAN: "plan",
    Stage.DEV: "dev",
    Stage.TEST: "test",
    Stage.REVIEW: "review",
}


def ensure_route(state: StoryState) -> StoryState:
    if not state.route:
        state.route = build_route(state.kind, state.complexity)
    if not state.phase or state.phase not in state.route and state.phase not in PHASES:
        state.phase = STAGE_TO_PHASE.get(state.stage, state.route[0])
    return state


def goto(state: StoryState, phase: str) -> StoryState:
    """Make `phase` the next thing to run (it may be earlier in the route: retries)."""
    state.phase = phase
    state.stage = phase_stage(phase)
    return state


def advance(state: StoryState) -> StoryState:
    nxt = state.next_phase()
    if nxt is None:
        state.stage = Stage.DONE
        state.phase = ""
        return state
    return goto(state, nxt)
