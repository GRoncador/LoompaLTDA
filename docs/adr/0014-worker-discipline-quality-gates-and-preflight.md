# ADR-0014: Worker discipline, deterministic quality gates and risk pre-flight

Date: 2026-09-30 · Status: accepted (Plano set/2026 · Fase 7; builds on ADR-0006 and ADR-0009)

## Context

Sprint 1 of the throw-away `contas` factory showed what still held stories back once the engine
bugs of Fase 6 were fixed, and all of it was about the quality of the Worker's work and of the judge:

- the Worker spent most of its rounds reading: tasks with dozens of `read_file`/`search`/`list_dir`
  calls in a row and no edit, one fix pass that ran the same suite thirty times with nothing changed
  (4.27M input tokens for the sprint, only ~89k of them from tool results: the cost is the history
  re-sent each round, so the lever is fewer rounds);
- a `debug.txt` with paths of the founder's machine reached the S-005 delivery;
- S-003 and S-006 failed a whole retry on one import-order lint finding, with green tests;
- the judge wrote "the suite does not pass" next to 44 green tests (it never saw them), and every
  delivery produced 3-4 Kaizen cards, mostly nits.

The plan's Fase 7 list (7.1-7.11, from AIOX-Core, the official Spec Kit templates and the reference
CLIs) was reordered by that evidence: 7.11, 7.10, 7.2 first; prompt-heavy items after measuring
their cost; specialised Workers only with evidence.

## Decisions

### 1. Discipline enforced in code, not asked for in prompts (7.11)

- **Read before write.** With `ACI.require_read` (the Worker, per task), editing or overwriting an
  existing file that the task has not read is refused. New files need no read. `begin_task` resets
  it: a new task is a new conversation.
- **`LoopGuard`** (`agents/loopguard.py`), used by every tool loop: a lookup repeated while the
  tree is unchanged (`ACI.version`) and whose result is still verbatim in the history points back
  to it; a repeated `run_tests`/`run_lint` returns the previous result without running; a streak
  of `schedule.worker_explore_nudge` (10) reads without a change gets a note on the tool result
  asking for the diagnosis and the edit; a write tool that fails three times suggests another way.
  Notes ride on the tool result, never as an extra user turn (every provider accepts that shape).
- **Pruning keeps the latest read of each file** (`schedule.worker_keep_file_chars`, 16k chars)
  while nothing wrote to it since: pruning it was what sent the Worker back to read it again.
  *Amended 2026-10-02:* the history is no longer pruned every round. It grows untouched up to
  `schedule.context_compact_chars` (200k chars, every tool loop) and is compacted once past it,
  keeping a quarter of that in current file reads; `worker_keep_file_chars` is gone. Rewriting an
  old message each round cut the provider's prompt cache there, and the 16k budget still sent the
  Worker back to re-read (contas Sprint 2). Each tool loop sends OpenRouter its own `x-session-id`.
- **Diagnosis with the first edit.** On a fix pass and on bugfix stories the write tools take a
  `reason`; the first write without one is refused. The root cause is stated in the same call that
  changes the code, so it costs no extra round when given.
- The repository outline sits in the Worker's cached prefix; each task sees what earlier commits
  changed. `worker.task` (rounds, repeats, nudges, diagnosis) and `tool.call` (path, repeat) make
  the effect measurable on the next run.

### 2. Diff hygiene is read line by line (7.10)

`loompa/hygiene.py` blocks debris files, absolute machine/worktree paths, debugger statements and
conflict markers, and notes new TODOs, debug prints and whitespace-only files. The Worker gets them
after each task, before its model self-check; the Inspector fails a story that still carries a
blocking one; the Deployer never commits an untracked debris file.

### 3. The judge is bound by a rubric and by facts (7.2)

The judge sees the checks that already ran as facts and follows an explicit rubric per
SEC/PERF/TEST/ARCH and per severity. A finding must be anchored in a file of the diff (or name one),
low severity is never reported, at most three are kept, and a failed criterion needs a reason.
Tautological tests, tests that assert nothing and code changed with no test are found without a
model. Only medium/high findings become Kaizen cards.
**Self-healing** before a failure counts: a lint-only failure gets the linter's own fixes at no model
cost; a high-severity finding gets one Worker round before the founder is asked.

### 4. Reproducer first on bugfixes (7.7) and alternatives in the plan (7.9)

A bugfix plan starts with a test that reproduces the bug (added by code when the Architect forgot).
During that task only test files are writable, and the suite must then show a new failure that is not
an import error. A reproducer that never fails is recorded and the fix goes on; it never blocks.
The Architect lists the approaches it rejected (`alternatives_considered`).

### 5. Every write is checked at once (7.8)

`write_file`/`edit_file`/`apply_patch` return syntax errors (`compile`), undefined names (one narrow
`ruff --select E9,F63,F7,F82` when the project uses ruff) and invalid JSON/TOML in their own result.
Unused imports are not reported: mid-task they are normal.
Found on the way: the runner set `FORCE_COLOR=0`, and Rich, ruff and others turn colour **on** when
the variable exists at all; `contas` S-030 failed on the ANSI codes that put in Typer's help. The
runner now drops the variables that force colour.

### 6. Richer artifacts, measured (7.1, 7.3)

spec.md: NFRs, edge cases, key entities, assumptions (the last two from the official Spec Kit
templates), numbered criteria. plan.md: impact, rollback, criterion→test traceability, constitution
check (Spec Kit's Constitution Check + Complexity Tracking), tasks with files and a check. The PO's
review adds feasibility, declared dependencies (read from the manifests), regression and assumptions;
the Architect can bounce an unbuildable spec once. Left out of the Spec Kit templates on purpose:
P1-P3 user stories and `[P]` parallel markers (v1 runs one story sequentially), business Success
Criteria (not verifiable by the factory; the measurable part is in the NFRs), constitution
versioning (git and the lessons section already do it).
Cost, measured against the prompts before Fase 7 (chars/4): Product +208, Architect +429, PO review
+199, judge +313, Worker +143 plus a ~65-token outline, all in cached system prefixes: roughly 2-4%
more input per story, before the 7.11 savings.

### 7. Autonomy modes and risk pre-flight (7.5, 7.6)

`schedule.autonomy: auto|yolo|standard|preflight`. Auto: SIMPLE is yolo (no model self-check per
task), COMPLEX is preflight, and a plan touching a schema, a migration or a public contract is lifted
to preflight. The `preflight` phase (after `plan`, owner Architect) starts from facts measured in code
(`loompa/risk.py`: exists, kind, git churn, dependents, whether a test mentions the file), writes
`risk.md`, puts up to three mitigation tasks in front of the change and asks the founder only when
the plan could destroy data irreversibly.

### 8. Separation of duties in the registry (7.4)

`register()` refuses a phase whose reviewer is its owner. Specialised Workers (backend/frontend/DB/
DevOps) are **not** built: nothing shows they help yet. What would count: stories of one kind failing
where a different stack context was the cause, visible in `worker.task` and the failure history.

## Consequences

- Test fixtures that wrote `assert True`, overwrote files unread or wrote a fix without a reason had
  to change: the rules apply to the scripted Workers too.
- Everything is unverified against a real model until the `contas` Sprint 1 resumes on this build;
  that run is the measure of 7.11 (rounds per task, repeats, nudges) and of the judge's noise.
