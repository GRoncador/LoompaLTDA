# ADR-0022: The factory diagnoses itself, apart from the product's Kaizen

Date: 2026-10-01 · Status: accepted (Plano set/2026 · item 7: Fase 8.3 and 8.4; keeps ADR-0008's
separation between the product's backlog and everything else, and ADR-0016's "facts in code, the model
only reads them")

## Context

Every factory defect found in `contas` Sprint 1 (a merge that failed silently, a task cut by the limit
counted as done, a stall nobody noticed, a slow judge, branches left behind, answers cut by the output
limit) was found because someone read the events and logs by hand. The Kaizen loop exists, but it is
about the *product*: its findings become cards in the factory's backlog. Problems of Loompa itself had no
place: they should not become product cards, the factory should not fix its own code, and they hold for
every factory, not one.

## Decisions

### 1. Signals are code over what the factory already records

`loompa/factory_health.py` has a catalogue of signals, each a function over a `Window` (one factory, a
time window, optionally a sprint) that reads events, model usage, the founder's inbox, git and the
per-story trace, and returns `Finding`s: a signature, a pt-BR title and detail with the numbers, the
area of Loompa most likely involved (`worker`, `inspector`, `deployer`, `worktrees`, `comms`…), a
severity, an impact (calls, minutes, US$), and evidence (story, event and span ids, and the
`loompa trace` command that opens each one). A signal that raises is skipped: diagnosis never stops the
factory. Signals: answers cut per model and role; tasks ended by the round limit; repeats inside a task;
slow judge and slow Worker tasks; stalls; repeated self-check failures, base-sync failures and a base
already red; model fall-through; process tasks and duplicate tasks in plans; criteria the founder removed
and repeated Kaizen findings; test leftovers; cost concentrated in one role or one tool; jargon in what
the founder read; merged branches not deleted and uncommitted changes under `.loompa/`; and, from the
trace, re-reads with the same arguments, a LoopGuard warning followed by the same call, and context
growth inside a task.

### 2. Thresholds come from Sprint 1; trace signals say they are provisional

Thresholds were calibrated on `contas`' `state.db` (2026-09-20 → 30): finished tasks never passed 4
repeats while cut ones reached 22; the judge's model time averaged 24 min per story; a self-check failing
twice was normal and 8–10 times was not; the base sync failed twice on S-007. The signals that read the
trace had no Sprint 1 data: their findings carry `provisional` and the panel marks them, until Sprint 2
calibrates them. Rerun over Sprint 1 the detector finds the problems seen by hand (11 findings in
SP-001, 3 more in SP-002, including the S-007 base sync and the S-030 repeats).

### 3. Findings live in the hub, with a signature that joins them across sprints and factories

`~/.loompa/factory_health.db` (`HealthBook`): `findings` (one row per signature, open or resolved with
the commit that fixed it), `sightings` (every time a scan saw it, per factory and sprint) and `scans`.
The signature drops story and sprint ids, so the same problem in another sprint or factory is the same
finding. A scan reports what is new, what came back after a fix (the finding reopens) and which fixes are
confirmed (a resolved signature the window no longer shows).

### 4. The model only reads the facts, and its reading is marked as a hypothesis

For new findings, one `low` call to the Ops role gives a likely cause and a fix inside Loompa, stored
apart from the finding and always shown as "hipótese". It never creates or removes a finding and is
skipped in dry-run. The prompt says the findings are data, to say "unclear" when the evidence points
nowhere, and to use only the numbers the finding gives. Seen with a real model on Sprint 1: without that
rule, and without the finding naming the limit, the model wrote "the 100-call limit" for a 40-round
limit; the finding now cites the configured limit (`worker_max_iterations`) and the rule is in the
prompt.

### 5. When it runs, and where the founder sees it

- On sprint close (`MasterAgent`), before the report: the report gets a "Achados da fábrica neste
  sprint" section and the inbox note already points to the report.
- `loompa factory-health` lists (with the trend per scan), `show` opens one with its evidence, `resolve
  <sig> --commit <sha>` marks it fixed, `scan [SP] [--since] [--dry-run]` reruns, and `--export
  factory-improvements.md` writes what to bring to a Loompa development session.
- The panel's **Fábrica** tab (`FactoryModal`) lists open and resolved findings, the trend dots, the
  evidence and the hypothesis, and resolves or reopens. API: `GET /api/factory-health`,
  `POST /api/factory-health/{sig}/resolve|reopen`, `POST /api/factories/{slug}/factory-health/scan`.
- The header's "💡 melhorias catalogadas hoje" becomes **"achados no produto"**: the product's Kaizen
  findings of the founder's local day, without repeats, and clickable (`/product-findings`).

## Alternatives considered

- **Factory findings as backlog cards.** They would mix with the product's work, and the factory would
  try to change Loompa's code (out of Fase 8 by the founder's decision).
- **A model reading the logs and finding problems.** Not reproducible and cannot be trusted with numbers;
  the hypothesis step shows how easily a model fills a gap.
- **Per-factory storage.** The same Loompa defect would be found and fixed separately in each factory.

## Consequences

- Findings about git state (merged branches, `.loompa/` changes) read the repository as it is now, not
  as it was in the window, so an old sprint scanned today shows them too.
- Sprint windows can overlap (SP-001 and SP-002 in `contas` did), so the same event can count in both.
- Not verified yet: the provisional trace thresholds (Sprint 2), and whether the fixes of Fase 8.5 move
  the signals as expected.
