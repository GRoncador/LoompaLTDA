# ADR-0019: The sprint report and the Sprints tab

Date: 2026-10-01 · Status: accepted (Plano set/2026 · item 4: Fase 8.2 and 10.8; reads what
ADR-0015 records, follows ADR-0018's one sprint at a time)

## Context

When `contas` Sprint 1 ended, the founder got a one-line inbox note. They asked what was done, what
came in without being planned, how long and how many interactions each story took, what it cost and
how it compares with the sprint before. Everything needed is already recorded: the events (stage
changes, retries, escalations, blocks, the founder's answers, deliveries, task starts and ends with
their origin, cuts) and the usage table (calls, tokens, latency, the cost the provider billed). The
plan put the report (8.2) and the screen that shows it (10.8) before Sprint 2, so Sprint 2's close
already produces the comparison with Sprint 1.

## Decisions

### 1. The report is measured in code (`loompa/sprint_report.py`)

`measure(store, slug, sprint)` reads the events and usage rows inside the sprint's window (start →
close, or now while it runs) and returns plain JSON:

- **Members:** `story_ids`, plus the stories `sprint.started` began with and the ones
  `sprint.adjusted` brought in or took out. A story absent from `sprint.started` joined mid-sprint;
  how it came (an epic split, a Kaizen finding, a founder request in a meeting) is read from
  `story.split` and the card's origin.
- **Per story:** result (delivered, cancelled, back to the backlog, waiting for the founder, in
  progress), wall-clock time and time per stage (from `story.stage`), model time, calls, tokens and
  cost (usage rows of the window, not `stories.cost_usd`, which spans every sprint the story went
  through), attempts (`story.retry` with a tier) apart from Ops recoveries (`story.retry` with a
  cause), escalations, re-plans, restarts, stalls, blocks by reason, the founder's answers,
  deliveries, changes asked (the `changes` answer or `story.reopened`), review rounds, spec
  rejections, Worker tasks by outcome and origin, cuts.
- **Not planned:** stories that joined, tasks added after the plan by origin, review rounds beyond
  the first, spec rejections, re-plans, restarts. **Left for later:** cards filed during the window
  that are not in the sprint, Kaizen findings by kind, repeated findings, stories sent back.
- **Totals** and the **previous sprint**'s totals (the last finished one that started before; before
  ADR-0018 sprints could overlap). *Rework* = retries + re-plans + restarts + changes asked; extra
  review rounds are shown but not added, since each retry is reviewed again.
- **Charts' data:** a burn-up timeline (stories per kanban column at 48 points), time per stage.
- **"Não medido", never zero.** A sprint whose usage rows carry no span id ran before the trace
  (ADR-0015): task origins and times, cuts and reported cost are `None` and rendered as "não
  medido". `contas` SP-001 and SP-002 show what the events allow; old `worker.task` events still
  give task counts.

### 2. The executive summary is worded by the Master, from the numbers

`MasterAgent.write_sprint_report` sends the measured facts (English headings) to one `low` call
(ADR-0016: the numbers are given; it only words them). The answer must pass
`audit_executive_text`; otherwise, and in dry-run, `fallback_summary` writes it in code. The prompt
forbids contradicting or guessing numbers.

### 3. Written when a sprint closes, readable on demand

`close_finished_sprints` became async: it closes the sprint, writes the report and sends the inbox
note with the summary as its context and a new `FounderMessage.sprint_id`, which the panel turns
into "📊 Ver o relatório do SP-00X". A cancelled sprint is reported too (no extra note: the founder
just cancelled it). A failing report is logged and never keeps a sprint open.

The report is saved as `.loompa/reports/SP-00X.md` and `.json`. The folder writes its own
`.gitignore` (`*`), like the trace: it is the factory's record of its own work (cost, time), not
part of the product, and it must not dirty the tree the Deployer merges into.

`loompa sprint report [SP-00X] [--rewrite] [--json] [--dry-run]` prints the saved report of a
finished sprint (measures and saves it when missing; `--rewrite` measures again with a new summary)
and the live measurement of a running one, which is not saved.

### 4. The Sprints tab (10.8)

- `GET /sprints` adds each started sprint's totals (the list and the comparison chart);
  `GET /sprints/{id}/report` returns the saved report of a finished sprint or the live measurement,
  with the same markdown as the file and the CLI.
- "🏁 Sprints" in the header opens the tab: the list (goal, delivered/total, duration, cost, status)
  and, per sprint, *Visão geral* (summary, tiles compared with the previous sprint, burn-up, cost
  and time per story, time per stage, what was not planned and what was left, a comparison across
  sprints as small multiples: cost per delivered story, duration, rework), *Histórias* (the
  per-story table; a row opens the story drawer) and *Relatório* (the markdown). "Ver como tabela"
  replaces every chart with its table.
- The burn-up takes the validated dark categorical slots 1-3 (blue, orange, aqua); the other charts
  are one series in the brand amber, like *Custos*.
- **The card's sprint is read, never stored.** The overview maps story → sprint from
  `Sprint.story_ids` (the open or running sprint, else the last one) and the card shows it next to
  its id; the drawer lists every sprint the story went through. No `sprint_id` column: a story can
  go through more than one sprint.
- The kanban's sprint chip and the inbox note open the tab on that sprint.

## Consequences

- Sprint 2's close produces its report against Sprint 1 with no extra step; its traced numbers
  (task origin and time, cuts, reported cost) appear for the first time there.
- The report's prompt has not been seen with a real model; the fallback covers a bad answer.
- Measuring reads the window's events each time (a few thousand rows for `contas`); the list
  measures every started sprint. Fine at this scale; cache the totals in the JSON if it grows.

## Not decided here

- The link from a report to the factory's own findings (8.3/8.4, the "Fábrica" tab): the report's
  JSON is where it will go once those findings exist.
