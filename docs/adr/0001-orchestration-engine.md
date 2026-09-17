# ADR-0001: Orchestration engine — explicit asyncio state machine instead of LangGraph

**Status:** accepted · **Date:** 2026-09-17

## Context

The brief specifies LangGraph for the Master Loompa orchestrator, with three hard requirements:

1. Each user story runs in an isolated sub-graph; a blocked node never freezes other branches.
2. Full state persistence in local SQLite with `interrupt` / `resume` semantics.
3. Dynamic escalation (Tier 2 → Tier 1 → BLOCKED_AWAITING_INPUT) after repeated failures.

## Decision

Implement the orchestrator as a small, explicit **asyncio state machine** (`loompa.engine`) with
SQLite checkpoints, rather than depending on LangGraph.

- Every story is a `StoryRun` with a typed `StoryState` and a `stage` enum
  (`BACKLOG → SPEC → PLAN → DEV → TEST → REVIEW → AWAITING_FOUNDER → DONE`).
- The `Scheduler` runs up to `max_parallel` stories as independent `asyncio.Task`s.
  A story that needs the Founder transitions to `AWAITING_FOUNDER`, writes an inbox message,
  and its task simply ends; the scheduler picks the next runnable story. Nothing blocks.
- Every transition is checkpointed to SQLite *before* the next node runs, so a crash or
  Ctrl-C resumes from the last completed node. Resuming after an inbox reply is just
  "load state, apply reply, re-enqueue".
- Nodes are plain async functions `(ctx, state) -> state`, kept in a registry so the graph
  shape is readable in one file (`engine/graph.py`).

## Consequences

- **Pros:** zero framework lock-in, ~600 lines of fully unit-testable code, no hidden
  threading, checkpoints are plain rows a human can query, trivially portable to LangGraph
  later if a real need appears (the node signature is deliberately compatible).
- **Cons:** we forgo LangGraph's tracing ecosystem (LangSmith). We compensate with a
  structured event log table (`events`) that the dashboard streams over WebSocket.
- The brief's vocabulary (graph, node, interrupt, resume, checkpoint) is preserved in the API.
