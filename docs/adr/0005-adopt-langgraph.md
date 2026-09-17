# ADR-0005: Adopt LangGraph for orchestration (supersedes ADR-0001)

**Status:** accepted · **Date:** 2026-09-17

## Context

ADR-0001 chose a small custom asyncio state machine. The Founder decided to adopt LangGraph now,
even if part of its power stays idle, because the project is open source and should be aligned
with the industry standard: a community of users and tests, a known mental model for
contributors, tracing/hosting ecosystem (LangSmith, LangGraph Studio/Platform) and room to grow
into dynamic graphs and sub-graphs without another migration.

## Decision

- `loompa.engine.langgraph_engine` builds a `StateGraph(StoryState)` with the nodes from
  `engine/graph.py` (intake, spec, plan, dev, test, review, await_founder). Node functions keep
  the `(ctx, state) -> state` signature; the graph wraps them.
- Routing is a single conditional edge evaluated after every node: `state.stage` → next node,
  `AWAITING_FOUNDER` → `await_founder`, terminal stages → `END`. The escalation ladder is therefore
  just `test` routing back to `dev`.
- Persistence uses LangGraph's `AsyncSqliteSaver` (`.loompa/langgraph.db`), one thread per story
  (`thread_id = story id`). Crash/Ctrl-C recovery is `ainvoke(None, config)`.
- Human-in-the-loop: the inbox reply applies the answer to the state and injects it with
  `aupdate_state(..., as_node="await_founder")`. The graph then continues on the next scheduler
  cycle, never inside the reply process (so `loompa inbox reply` stays instant).
- The `stories`/`checkpoints`/`events` tables remain as the **read model** for CLI and dashboard;
  LangGraph's checkpointer is the execution source of truth.

## Consequences

- +LangGraph, langchain-core, aiosqlite dependencies (~15 packages). Pin `langgraph>=1.2,<2`.
- LangSmith tracing can be enabled with the standard `LANGCHAIN_TRACING_V2` env vars; prompts
  then leave the machine — off by default.
- Tests keep running with scripted providers; the graph is exercised end to end.
- ADR-0001 is superseded; its analysis stays as history.
