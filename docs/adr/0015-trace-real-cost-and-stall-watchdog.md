# ADR-0015: Per-story trace, real cost, a stall watchdog and the first speed fixes

Date: 2026-09-30 · Status: accepted (Plano set/2026 · Fase 8a; builds on ADR-0011 and ADR-0014)

## Context

Sprint 1 of the throw-away `contas` factory finished, and what it cost most was speed: S-031, a
4-line fix plus tests, took ~2.5 h. Every defect of the factory itself found in that run was found by
someone reading events, logs and `pmset` by hand:

- a Worker task looped for 53 calls and 35 minutes until the tool-call limit, was then **marked
  done**, the rest of its checklist was skipped, and the Inspector judged a half-built story;
- 52 answers were cut at the output limit in one hour, visible only in the log;
- the engine went silent for 50 minutes and then 1h30; both times the Mac was asleep;
- the founder's inbox said four times that DeepSeek V4 Flash "got more expensive": OpenRouter's
  catalogue price is one of the 29 providers serving it, and it had switched which one;
- a conflicted file was skipped by the resolver without a word; a test left `gastos.json` at the
  repository root and the task commit took it; merged story branches piled up;
- S-030's spec asked for "no border in any help", which Typer always draws: two tiers and a re-plan
  went into it, and the founder read "users can't see the help" when only the factory's new test
  failed.

The plan split Fase 8 in two (see "Ordem de execução das Fases 8 a 11"): **8a** makes the factory
record what happened and fixes the correctness bugs above without waiting for a detector; **8b**
(sprint report, self-diagnosis, OTLP export) comes after a whole sprint with a trace, so its signals
are calibrated on real data.

## Decisions

### 1. One span tree per story, next to the light events (`loompa/trace.py`)

Events stay light: the dashboard reads them. The content goes to `.loompa/traces/<story>.jsonl`:

- **One instrumentation point per layer**, nested by a `ContextVar`, so no caller passes a parent:
  the LangGraph node wrapper opens a `node` span per run of a node; the Worker a `task` span per
  checklist task (with its **origin**: plan, replan, preflight, founder, inspector); the tool loop
  a `round` span per round (labelled by pass: main, self_check, reproducer_retry, fix) and a `tool`
  span per call; the router an `llm` span per logical call.
- An `llm` span records the model asked for and the one that answered, the provider that served
  it, the parameters, the messages and the answer, usage, cost, `finish_reason`, latency and **every
  attempt**: cuts retried with more room and candidates that failed before the one that answered
  (outcome, status, error, budget, effort). A `tool` span records the arguments, the size of the
  result, the error and the LoopGuard's note.
- **Messages are written once per story and referenced by hash.** The tool loop re-sends the same
  prefix every round; storing each prompt whole would have cost ~140 MB for `contas`' 3.4k calls.
- Span ids are 8 bytes and trace ids 16 (OpenTelemetry sizes), one trace per node: the OTLP export
  planned for 8b maps onto it without a translation table.
- Every line goes through `redact_secrets`: this machine's keys, and any token shaped like a
  provider key a file or a tool result carried in. The folder writes its own `.gitignore` (`*`), so
  factories onboarded before it existed never commit it; `trace.retention_days` (30) prunes files
  untouched that long when an engine context is built. Calls outside a story go to
  `_factory-<date>.jsonl`.
- Always on, dry-run included; a tracer without a folder still hands out spans, and a write that
  fails is logged once and dropped: tracing never breaks the work it observes.
- `llm.call` and `tool.call` events carry the span id; `llm.call` also carries the story, the
  `finish_reason`, the cuts and the provider that served it; `llm.cut` and `llm.fallthrough` make
  retries visible (they were log lines); `tool.call` carries the search query;
  `worker.task_started`/`worker.task_finished` give each task its wall-clock time and origin;
  `resolver.skipped` says why a conflicted file was left unresolved.
- **`loompa trace S-031 [--task T5 | --span <id>] [--json]`** reads it in the terminal, and
  `loompa trace --stats` puts calls, cost, mean/p90 latency and cuts side by side per role and model
  (from the usage table): how a tier-2 model is compared with another on the same role before a
  preset changes. It is a tool for developing Loompa; the founder never sees the trace.

### 2. The cost is what the provider billed

OpenRouter's response carries `usage.cost` and the provider that served the call; the router now
reads both (plus `cost_details.upstream_inference_cost` on BYOK). `CostTracker.record` uses the
reported cost when there is one and the `pricing` table only for providers that do not report it.
The usage table gained `served_by`, `finish_reason`, `cost_source` and `span_id` (added to existing
databases when they are opened). Checked live: DeepSeek V4 Flash answered from "Sail Research" with
`usage.cost` = 4.71e-06.

### 3. "X got more expensive" means the providers' median rose

`ModelWatch.check_prices` fetches `/models/<id>/endpoints` for the models in use (public, one request
each; an alias is priced through the model it points to) and compares the **median** blended price
of the providers that accept tools with the last one seen; a provider entering or leaving at an end
barely moves it. The note gives the median before and after and how many providers serve the model,
and says what is true now: the cost control records what OpenRouter billed. A move of the catalogue's
reference price is only the `models.price.reference_moved` event.

### 4. The stall watchdog lives in the scheduler, on the clock that stops in sleep

A running story that emits nothing for `schedule.stall_minutes` (20, above the longest silent step,
a 15-minute test run) is cancelled; `story.stalled` says for how long and what it last did, and the
Ops Loompa handles it like a runner crash: restart from the last checkpoint after a backoff, then a
plain inbox note once `ops_max_recoveries` is spent. Silence is measured with `time.monotonic()`,
which stops while the machine sleeps, so a sleeping Mac is never mistaken for a hang. The sleep
itself is the wall clock running ahead of the monotonic one between two ticks: `engine.slept`, and
one inbox note when stories were running. `run_command` starts each command in its own process group
and kills the group on timeout or cancel (killing only `uv` left pytest running with the pipes
open).

### 5. A task that did not reach `done` is not done (8.5)

- `LoopGuard(repeat_limit)`: after `schedule.worker_repeat_limit` (6) lookups answered from memory
  since the tree last changed, the tool loop ends as `loop` and the guard names the calls it kept
  repeating.
- A pass that ends by `limit` or `loop` returns a diagnosis (English: it becomes the next attempt's
  "last failure"). The task is not marked done, nothing is committed or self-checked for it, and the
  run stops there: later tasks usually build on it. **We chose not to run the rest of the checklist**
  on a base the model is stuck on; the tasks stay pending for the next attempt, so none is skipped.
- `node_dev` sends that failure up the same escalation ladder as the Inspector's FAIL (`climb`):
  tier 2 again, tier 1 re-planned once, then the founder. The Inspector never judges a story with
  an unfinished task.

### 6. A criterion only the story's own tests fail goes to the Product Owner (Fase 7, item 1)

The Inspector reports which tests failed (pytest's node id is now kept) and whether they were the
only failing check; `test_origin` tells the story's own tests from those the base already had by
reading the test on the base. When the same own tests fail a second time while the product's pass,
`ProductOwnerAgent.review_criteria` keeps, rewrites or withdraws each criterion before a stronger
model is paid — never what the founder asked for, never all of them. A revision updates the
acceptance and `spec.md`, goes back to the Worker without spending a tier, and the delivery tells
the founder what was withdrawn. A persistent failure carries a `[facts]` line measured in code
("only the story's tests fail, nothing was merged" or "tests the product had now fail"); the
founder's fallback text says which, and the Master's rewrite gets the established facts with a rule
never to contradict them.

### 7. Hygiene sees what a test run leaves behind (Fase 7, items 3 and 5)

A new data file at the repository root that the plan does not list is a blocking `stray_data`
issue. The files a test run creates are found by comparing the files outside the last commit before
and after it — in the Worker's `run_tests` (told at once), and in the Inspector's run (blocking
`test_residue`, removed after). Found on the way: `diff_working` runs `git add -N`, after which a new
file is no longer `??`, so the Deployer's leftover check never saw one once the Inspector had looked
at the diff; it now reads the files outside the last commit. A merged story's branch is deleted, and
the story branches already merged that no worktree uses are pruned (Deployer only, `git branch -d`).

### 8. The card shows what a story is doing

The overview carries, per story at work, its current task and origin, the task's tool calls, the
last one and when the story last said anything; live events advance it between refreshes. The card
reads "há 12 s · T5 · 23 passos · lendo cli.py", turns amber after five quiet minutes and red when
the watchdog declared the story stalled.

### 9. Addendum — the live smoke run (same day)

A throw-away factory (two small stories, US$0.12) ran this build against OpenRouter before
Sprint 2. Three things changed from what it showed:

- **An answer cut while the model is still thinking gets less thinking, not more room.** The
  Architect's plan for a SIMPLE story was cut at 4k, 8k and 16k tokens on glm-5.3-flash and again on
  deepseek-v4-flash (12 minutes, US$0.05, story blocked); every token was reasoning. A cut whose
  output is at least 90% reasoning now retries in the same room one effort step lower; the effort
  that answered is remembered per model and role, like the budget that fitted after a cut. A cut
  with visible output still doubles the room first. This is not a
  model change (ADR-0011): the model stays, it is asked to think less.
- **A cut attempt keeps the tail of what it was writing** (text or half-written tool call, and the
  reasoning when the provider returns it) and its reasoning tokens; a call that never answered
  carries the attempts' cost, and `loompa trace` shows the models it tried.
- **The checkpoint connection is opened once.** Two stories dispatched in the same tick each opened
  one; the leaked one kept `loompa run` alive after the cycle ended.

## Consequences

- The next `contas` sprint (Sprint 2) is the validation: every call and tool is in the trace, the
  cut tasks and stalls are events, and `loompa trace --stats` can compare tier-2 models on the Worker
  and the judge. 8b's detector and sprint report read these files and events.
- Trace volume is bounded by distinct messages, not calls; the retention keeps a month.
- The watchdog restarts a stuck node from its checkpoint: a node's side effects must stay safe to
  replay (already the rule since the resumable `dev` of Fase 7).

## Not decided here

- The OTLP export and Phoenix (Fase 8b): the span shape is ready for it.
- Which tier-2 models to put in the presets: `loompa trace --stats` measures, the founder decides
  after Sprint 2.
