# ADR-0041: Product management — phases, epics and the product manager

Date: 2026-10-11 · Status: accepted

## Context

On 05/10 the plan (Phase 16) set two things:

- a Lean life cycle instead of a classic business plan;
- a product manager role that owns the phases, the epics and the roadmap.

It also set constraints:

- the founder decides every phase change;
- an epic is born only through the PM with the founder's OK, and becomes cards only through the
  Product Owner;
- the Spec Loompa and the Product Owner keep their jobs;
- everything is cheap: per phase and per sprint, never per story.

## Decision

### 1. The kind decides the cycle (16.1, `factory_kind`)

| Kind | Phases | Discovery | Growth |
|---|---|---|---|
| personal | `in_use` | no | no |
| commercial | `discovery → mvp → validation → growth → maturity` | yes | yes |

Switching the kind adds the phases the new kind lacks and keeps the ones already lived.

### 2. Phases and epics as entities (16.3, 16.5)

- **Tables.** `phases` (goal, exit criterion, measure, facts, status) and `epics` (name, goal,
  metric, phase, status) in `state.db`, and `stories.epic_id`. The old `epic` text stays the
  card's group label. It was not renamed to `parent`: it is part of the contracts of several
  prompts and conversation ops, and renaming it would change them all for no behaviour.
- **Linking a card.** A card filed with an epic's name is linked by code
  (`Lifecycle.link`, called from `Backlog.add_item`).
- **Who writes a phase.** The founder writes a phase's goal, exit criterion and facts (panel
  "🧭 Produto"). When the criterion changes, the Metrics Loompa proposes how to measure it
  (`MetricsMeasure`).
- **Moving.** Moving is a founder decision: the inbox (`lifecycle:phase:<key>`) or the panel's
  confirmed button. Nothing moves on its own.

### 3. The Product Manager Loompa (16.4, `agents/product_manager.py`, cluster strategy)

It has three prompts:

- **Epics of the current phase.** Outcomes, not layers, with countable metrics and no invented
  numbers. They go to the founder as one batch of questions (`lifecycle:epics`); "Criar" creates
  them.
- **Phase review.** It answers `exit_met` yes/no/unknown from the founder's facts and the
  deliveries. Deliveries never stand in for usage. It runs once per closed sprint, when the
  criterion is written and a next phase exists (`Scheduler._review_phase`). A "yes" becomes a
  decision for the founder.
- **Discovery.** Described in §4.

Its tool profile reads memory only, contracts included (13.5). It is consulted in the brainstorm.

### 4. Discovery inside the brainstorm (16.2, `discovery.py`)

The founder asks for it in a commercial product's brainstorm, with `/descoberta` or the
"🔎 Descoberta" button. Two steps:

1. The Analyst is consulted on competitors, with the brainstorm's citation filter.
2. The PM writes:
   - a one-page Lean Canvas (the sections of `business.md`);
   - the competitors with their sources;
   - hypotheses in the form "Acreditamos que X; saberemos pela métrica Y".

The result is a decision. Only "gravar" writes it into `.loompa/company/`, and a document the
founder already filled is never overwritten. Two company documents were added:

- `competitors.md`, read by the PM and the Analyst;
- `hypotheses.md`, read by the PM, the Master, the Product Owner and Metrics.

### 5. The Sprint Meeting starts from the phase (16.6)

The Product Owner's sprint proposal receives `Lifecycle.brief()` as material: the phase, its
criterion and its active epics. The proposal's contract did not change.

### 6. Panel (16.7)

- the phase's chip on the board, for example "MVP: 60%": the share of the cards of the phase's
  epics that are done;
- each card's epic;
- a filter by epic;
- the "Produto" modal.

### 7. The brainstorm's consultants (16.8)

The roster of 10.10 is complete: Analyst, Architect, UX, Product Owner, PM, Compliance, Metrics,
Storyteller and Inspector.

## Consequences

- The PM costs one call per closed sprint, and only on a product whose phase has an exit criterion
  written. Epics and discovery cost something only when the founder asks.
- Without facts from the founder, the phase review answers "unknown". This is by design: after the
  MVP the phases depend on data from outside the factory.

## Not done, on purpose

- Renaming `epic` to `parent` (see §2).
- An automatic phase move.
- A business plan with market sizes.
- The new prompts are not measured on the bench yet (plan reminders).
