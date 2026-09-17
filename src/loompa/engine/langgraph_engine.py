"""LangGraph orchestration for one factory (ADR-0005).

The story pipeline is a `StateGraph` over `StoryState`:

    START → intake → spec → plan → dev → test → review → await_founder → END
                        ↑_________________________________|   (founder answer re-routes by stage)

* Every node is one of the pure `(ctx, state) -> state` functions in `engine/graph.py`.
* After each node a conditional edge routes by `state.stage`, which is how the escalation ladder
  (test → dev again) and founder answers (await_founder → spec/dev/review/END) are expressed.
* Persistence is LangGraph's SQLite checkpointer (`.loompa/langgraph.db`, one thread per story).
  Ctrl-C or a crash resumes from the last completed node with `ainvoke(None, config)`.
* The Founder inbox is the human-in-the-loop channel: a reply calls `aupdate_state(...,
  as_node="await_founder")` so the graph continues on the next `loompa run` without executing
  inside the reply process.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

import aiosqlite
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from loompa.engine.context import EngineContext
from loompa.engine.graph import (
    BlockedReason,
    block,
    node_dev,
    node_intake,
    node_plan,
    node_review,
    node_spec,
    node_test,
)
from loompa.engine.state import PAUSED, TERMINAL, Stage, StoryState

log = logging.getLogger("loompa.langgraph")

STAGE_TO_NODE: dict[Stage, str] = {
    Stage.BACKLOG: "intake",
    Stage.SPEC: "spec",
    Stage.PLAN: "plan",
    Stage.DEV: "dev",
    Stage.TEST: "test",
    Stage.REVIEW: "review",
    Stage.AWAITING_FOUNDER: "await_founder",
}
NODE_FUNCS = {
    "intake": node_intake,
    "spec": node_spec,
    "plan": node_plan,
    "dev": node_dev,
    "test": node_test,
    "review": node_review,
}


def route(state: StoryState) -> str:
    """Conditional edge after a work node: where the story goes next, decided by its stage."""
    if state.stage in TERMINAL:
        return END
    return STAGE_TO_NODE[state.stage]


def route_after_founder(state: StoryState) -> str:
    """Edge after the pause node: still waiting → END (the run stops, checkpoint kept);
    otherwise (answer injected via `aupdate_state`) → the stage the answer chose."""
    if state.stage in TERMINAL or state.stage in PAUSED:
        return END
    return STAGE_TO_NODE[state.stage]


def thread_config(story_id: str) -> dict[str, Any]:
    return {"configurable": {"thread_id": story_id}}


def build_graph(
    ctx: EngineContext, checkpointer: AsyncSqliteSaver | None = None
) -> CompiledStateGraph:
    builder = StateGraph(StoryState)

    def make(name: str):
        fn = NODE_FUNCS[name]

        async def node(state: StoryState) -> dict[str, Any]:
            try:
                new_state = await fn(ctx, state)
            except Exception as exc:  # noqa: BLE001 - a crash isolates this story only
                log.exception("story %s failed in %s", state.story_id, name)
                technical = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()[-3000:]}"
                ctx.emit("story.error", story_id=state.story_id, node=name, error=str(exc)[:300])
                new_state = await block(
                    ctx, state, BlockedReason.PERSISTENT_FAILURE, technical, resume=state.stage
                )
            _project(ctx, new_state, f"node_{name}")
            return new_state.model_dump()

        node.__name__ = f"node_{name}"
        return node

    for name in NODE_FUNCS:
        builder.add_node(name, make(name))

    async def await_founder(state: StoryState) -> dict[str, Any]:
        # Pure pause point. The founder's answer is injected with `aupdate_state(as_node=...)`.
        return {}

    builder.add_node("await_founder", await_founder)
    builder.add_edge(START, "intake")
    targets = {**{n: n for n in STAGE_TO_NODE.values()}, END: END}
    for name in NODE_FUNCS:
        builder.add_conditional_edges(name, route, targets)
    builder.add_conditional_edges("await_founder", route_after_founder, targets)
    return builder.compile(checkpointer=checkpointer)


def _project(ctx: EngineContext, state: StoryState, node: str) -> None:
    """Keep the read model (stories table, node log, events) in sync for the CLI/dashboard."""
    ctx.store.update_story(
        state.story_id,
        stage=state.stage.value,
        state=state.model_dump(mode="json"),
        attempts_tier2=state.attempts_tier2,
        attempts_tier1=state.attempts_tier1,
        branch=state.branch,
        worktree=state.worktree,
        blocked_message_id=state.blocked_message_id,
    )
    ctx.store.checkpoint(state.story_id, node, state.stage.value, state.model_dump(mode="json"))
    ctx.emit("story.stage", story_id=state.story_id, stage=state.stage.value, node=node)


class GraphRuntime:
    """Owns the checkpointer connection and the compiled graph for one factory."""

    def __init__(self, ctx: EngineContext):
        self.ctx = ctx
        self._conn: aiosqlite.Connection | None = None
        self._graph: CompiledStateGraph | None = None

    async def graph(self) -> CompiledStateGraph:
        if self._graph is None:
            path = self.ctx.factory.paths.loompa / "langgraph.db"
            path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = await aiosqlite.connect(str(path))
            serde = JsonPlusSerializer(
                allowed_msgpack_modules=[
                    ("loompa.engine.state", "Stage"),
                    ("loompa.engine.state", "BlockedReason"),
                ]
            )
            saver = AsyncSqliteSaver(self._conn, serde=serde)
            await saver.setup()
            self._graph = build_graph(self.ctx, saver)
        return self._graph

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            self._graph = None

    async def run_story(self, state: StoryState) -> StoryState:
        """Start a new story or continue one from its last checkpoint until it pauses or ends."""
        graph = await self.graph()
        config = thread_config(state.story_id)
        snapshot = await graph.aget_state(config)
        if snapshot.values and snapshot.next:
            result = await graph.ainvoke(None, config)  # resume after crash / founder answer
        elif snapshot.values:
            return StoryState.model_validate(snapshot.values)  # already paused or finished
        else:
            result = await graph.ainvoke(state, config)
        return StoryState.model_validate(result)

    async def inject_founder_answer(self, state: StoryState) -> None:
        """Record the post-answer state at the pause node; `route` then picks the next node."""
        graph = await self.graph()
        await graph.aupdate_state(
            thread_config(state.story_id), state.model_dump(), as_node="await_founder"
        )

    async def run_until(self, state: StoryState, stop_after: list[str]) -> StoryState:
        """Run and pause right after any of `stop_after` nodes (LangGraph `interrupt_after`).
        Used by tests to simulate a crash/Ctrl-C between nodes; `run_story` resumes it."""
        graph = await self.graph()
        config = thread_config(state.story_id)
        snapshot = await graph.aget_state(config)
        payload = None if snapshot.values else state
        result = await graph.ainvoke(payload, config, interrupt_after=stop_after)
        return StoryState.model_validate(result)

    async def history(self, story_id: str) -> list[dict[str, Any]]:
        graph = await self.graph()
        out = []
        async for snap in graph.aget_state_history(thread_config(story_id)):
            out.append(
                {
                    "step": snap.metadata.get("step"),
                    "next": list(snap.next),
                    "stage": (snap.values or {}).get("stage"),
                    "created_at": snap.created_at,
                }
            )
        return out

    @staticmethod
    def is_runnable(stage: Stage) -> bool:
        return stage not in TERMINAL and stage not in PAUSED
