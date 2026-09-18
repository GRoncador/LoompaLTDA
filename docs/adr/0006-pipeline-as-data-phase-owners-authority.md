# ADR-0006: Pipeline as data, phases with an owner and a reviewer, authority in code

Date: 2026-09-18 · Status: accepted (implementation in phases 1 and 3 of `docs/PLANO-2026-09.md`)

## Context

The engine today (ADR-0005) is a fixed LangGraph: `intake → spec → plan → dev → test → review →
await_founder`, with the node list and the stage→node map hard-coded in
`engine/langgraph_engine.py`. Every story walks the same path regardless of its size or kind;
review happens only at the end (Inspector on the code, Founder on the delivery); the Master
agent both writes the backlog and orchestrates; nothing stops any agent from calling git.

The September 2026 handoff review proposed a set of changes borrowed from agent-framework
practice (dynamic pipelines, graded reviews, definition-of-done checklists, a single backlog
writer, sprints, batch approvals). This ADR records the ones we adopt and the shape they take
in Loompa. Items that need their own decision (OpenCode as a Worker backend, MCP tools, chat
sessions) get their own ADRs when they arrive.

## Decisions

### 1. The pipeline is data on the story, not code in the graph

* `StoryState` gains `kind` (`feature | bugfix | research`), `complexity`
  (`SIMPLE | STANDARD | COMPLEX`), `route` (an ordered list of phase names) and `handoff`
  (free-form notes the previous phase leaves for the next one).
* `node_intake` asks the Master to classify the request and builds the route. Large requests
  become an epic with child stories; there is no fixed "2 to 8 tasks" ceiling.
* A **phase registry** (name, node function, owning agent, reviewing agent) replaces the
  `NODES` / `STAGE_TO_NODE` tables. The LangGraph conditional edge reads the next item of
  `state.route`. `Stage` stays as the kanban projection of whichever phase is running.
* v1 routes are sequential. Parallelism exists between stories (epic → children), not inside
  one story; fan-out/fan-in inside a story is deferred until a real case appears.

### 2. Every phase has an owner and a reviewer, and the reviewer is the consumer

| Phase | Owner | Reviewer | Gate |
| --- | --- | --- | --- |
| spec | Product | Product Owner | "No Invention": each acceptance criterion cites its origin (founder text, constitution, spec) |
| plan | Architect | Product Owner | scope and allowed paths match the spec |
| dev | Worker | — (self DoD checklist per task) | tests written, paths respected, summary |
| test/review | Inspector | Founder (only on WAIVED) | graded verdict below |
| research | Analyst | Product Owner | sources cited, limitation declared when no web search |

Handoff between phases is the `handoff` field on the state. There is no relay through the
Master: the Master orchestrates and runs the Sprint Meeting; it does not carry messages.

### 3. Graded quality gate

`qa_review` returns `PASS | CONCERNS | FAIL | WAIVED`, each finding carrying a severity
(`low | medium | high`) and a prefix (`SEC- | PERF- | TEST- | ARCH-`). Layers run from cheapest
to most expensive: deterministic tooling (tests, lint, types) → optional scanner (CodeRabbit) →
LLM judge → Founder. `CONCERNS` feeds the Kaizen loop as learnings; `WAIVED` goes to the inbox
with `BlockedReason.WAIVER` and the Founder decides.

### 4. Complexity steers the model tier

`ModelRouter.candidates(role, complexity=...)` lets a `SIMPLE` story run every role on tier2
and a `COMPLEX` one lift Product/Inspector to tier1. This is the main lever that keeps the
extra review calls (spec review, QA review, DoD checklist) within +5–10% per story.

### 5. Authority lives in code, not in prompts

* A `Backlog` service is the only writer of stories; only `ProductOwnerAgent` holds it
  (`add_item`, `set_priority`, `set_status`, `admit`). Master, Kaizen and
  `apply_founder_answer` go through the Product Owner.
* Git operations are guarded: only the Deployer may merge, push or create PRs. A test proves
  the guard blocks any other role.
* A `Sprint` entity (`story_ids`, `open | running | closed`) scopes dispatch: the Scheduler
  only picks stories of the open sprint and emits `sprint.done` when all reach a terminal
  state; blocked stories wait alone and never hold the batch.
* Batch approval: a delivery message carries `decisions[]` so the Founder answers the delivery
  and each suggested card independently. Findings never get lost: every finding becomes a
  card admitted by the Product Owner or a review cycle until resolved.

### 6. Roles are an open set, models are configuration

`Role` is a string. New roles (Product Owner, Analyst, Ops) map to a tier through
`models.roles`, defaulting to tier2. Providers, tiers, keys and tool keys are configured per
factory at onboarding and in the dashboard (phase 0b); keys live only in the secrets files.

## Consequences

* The graph builder becomes generic: nodes are registered from the phase registry and the
  route decides the edges. Tests keep driving stories through `MockProvider` scripts.
* Two more LLM calls per story on average (spec review, QA review) plus one small call per
  Worker task (DoD). Offset by tier2-only SIMPLE stories.
* The Product Owner becomes a real agent with authority, which changes who the Founder talks
  to about the backlog (Sprint Meeting via Master, admission via Product Owner).
* Superseded pieces: the fixed `NODES`/`STAGE_TO_NODE` tables in ADR-0005's implementation
  (ADR-0005 itself, LangGraph as the engine, stands).

## Out of scope

Agent-framework ceremony from AIOX-Core (identity files, squad creator, IDE sync, greeting
rituals, file-based handoffs with a "consumed" flag): built for humans chatting with agents,
not for an autonomous factory.
