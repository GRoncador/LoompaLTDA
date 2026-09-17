"""Engine package. `graph`/`scheduler` import the agents (which import `engine.context`),
so they are exposed lazily to avoid an import cycle."""

from __future__ import annotations

from typing import Any

from loompa.engine.context import EngineContext
from loompa.engine.state import KANBAN_COLUMNS, BlockedReason, Stage, StoryState, kanban_column

_LAZY = {
    "NODES": "loompa.engine.graph",
    "apply_founder_answer": "loompa.engine.graph",
    "Scheduler": "loompa.engine.scheduler",
    "GraphRuntime": "loompa.engine.langgraph_engine",
    "build_graph": "loompa.engine.langgraph_engine",
    "StoryRunner": "loompa.engine.scheduler",
    "load_state": "loompa.engine.scheduler",
    "save_state": "loompa.engine.scheduler",
}

__all__ = [
    "KANBAN_COLUMNS",
    "NODES",
    "BlockedReason",
    "EngineContext",
    "GraphRuntime",
    "build_graph",
    "Scheduler",
    "Stage",
    "StoryRunner",
    "StoryState",
    "apply_founder_answer",
    "kanban_column",
    "load_state",
    "save_state",
]


def __getattr__(name: str) -> Any:
    module = _LAZY.get(name)
    if module is None:
        raise AttributeError(name)
    import importlib

    return getattr(importlib.import_module(module), name)
