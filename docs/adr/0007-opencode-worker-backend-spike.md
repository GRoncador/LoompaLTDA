# ADR-0007: OpenCode as an optional Worker backend (spike)

Date: 2026-09-19 · Status: accepted, spike (`docs/PLANO-2026-09.md`, Fase 2). ADR-0006 already
flagged this decision as deferred ("OpenCode as a Worker backend gets its own ADR when it
arrives").

## Context

`WorkerAgent` (ADR-0005/0006) implements every task itself: a zero-context LLM conversation
driven through `loompa.aci` (Loompa's own read/patch/test tool loop), ending in one commit.
The September 2026 plan asks for a spike comparing that against delegating the same job to
[OpenCode](https://opencode.ai), an existing open-source coding-agent CLI, so the Founder can
decide which one is the standard Worker backend — not to replace ACI outright.

## Decision

* `worker.backend` in `config.yaml`: `aci` (default, unchanged) or `opencode`.
  `node_dev` (`engine/graph.py`) picks the class at dispatch time — `WorkerAgent` or the new
  `OpenCodeWorker` (`agents/opencode_worker.py`) — both implement the same
  `run(state, wt) -> AgentResult` contract, so nothing else in the pipeline (Inspector,
  Deployer, Kaizen, escalation ladder) changes.
* `OpenCodeWorker` keeps Loompa's own per-task loop (one task from `tasks.md` at a time, one
  commit per task, mark done, stop on the first `blocked`) but the coding step itself is
  `opencode run --format json --agent loompa-worker --model <provider>/<model> "<prompt>"`,
  run inside the story's worktree via the model the router already picked for the `worker`
  role/tier/complexity. Before the first task, it writes
  `.opencode/agents/loompa-worker.md` in the worktree: a subagent definition scoped to
  `read: allow`, `bash: allow` and `edit` allowed only on `state.allowed_paths` (deny by
  default), mirroring the ACI tool's own path guard
  (`aci/tools.py::ACI._resolve`).
* The model ends its answer with `DONE: <summary>` or `BLOCKED: <reason>`; `OpenCodeWorker`
  parses that (from the JSON output when shaped as expected, falling back to plain text)
  instead of a `done`/`blocked` tool call, since the tool loop itself now lives inside
  `opencode`, not in Loompa.
* Ops keeps owning retries and cooldowns (ADR-0006 unchanged): `OpenCodeWorker` never retries
  by itself. A missing binary or a non-zero exit raises `RuntimeError`; a subprocess timeout
  raises `TimeoutError`. Both bubble up through `node_dev` to the runtime's generic exception
  handler, exactly like an `LLMError` from the ACI path, and `OpsAgent.on_failure` triages them
  (non-transient → straight to the Founder's inbox; a timeout reads as network-shaped
  instability and gets retried like any other transient crash).
* Cost is approximate: OpenCode does not hand back token counts, so `OpenCodeWorker` estimates
  input/output tokens from character counts (`len(text) // 4`) and records them through the
  same `CostTracker`/pricing table as every other call, tagged `tier="opencode"` so the
  Finance view can tell spike spend apart from the regular tiers. This is deliberately rough —
  good enough to put both backends' cost in the same ballpark for the Founder's comparison,
  not for budget enforcement.
* `loompa worker backend [aci|opencode]` (CLI) and `PUT /api/factories/{slug}/settings` with
  `worker_backend` (dashboard) switch it per factory without restarting the engine, the same
  way Fase 0b's provider/tier settings apply on the next call.

## How the Founder runs the comparison

Fase 2's ask is a report, not new reporting infrastructure: run the same story once with each
backend (`loompa worker backend aci` / `opencode`, then `loompa run` — or the dashboard toggle
— per run) and compare them exactly where Fase 0b already put the numbers: the kanban card's
QA verdict, the finance view's per-story cost (`opencode` tier stands out), and the delivered
diff. No bespoke A/B harness was added for a decision this is meant to close, not maintain.

## Consequences

* Requires the `opencode` binary on the machine that runs `loompa run`; `shutil.which` gates
  it, same pattern as the Deployer's optional `gh pr create`. Dry-run mode and CI are
  unaffected — the default backend stays `aci`, which needs no external binary.
* No DoD self-check (ADR-0006 §item 5) on the OpenCode path yet: the spike measures the
  backend's own output as-is. If OpenCode becomes the standard, the DoD checklist and the
  Worker's `note_learning`/Kaizen feed are the known gap to close next.
* `.opencode/agents/loompa-worker.md`'s permission schema is written against OpenCode's
  documented subagent format as of this writing; it has not been exercised against a real
  `opencode` install in this repo (tests script a fake `opencode` binary, per
  `tests/test_opencode_worker.py`, consistent with "tests use scripted providers, never the
  network"). Confirming the real CLI accepts it is part of running the spike.

## Out of scope

Making OpenCode the default, intra-task parallelism, or a shared tool-loop abstraction between
the two backends — premature before the Founder has a comparison to decide from.
