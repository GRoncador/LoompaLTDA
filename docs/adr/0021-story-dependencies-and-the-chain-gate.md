# ADR-0021: Dependencies between stories, and the gate between spec and plan

Date: 2026-10-01 · Status: accepted (Plano set/2026 · item 6: Fase 10.7 and 10.9; extends ADR-0008's
backlog authority to one new field, and ADR-0017's sprint proposal to declare it)

## Context

Until now nothing in the factory knew that one story builds on another. The design avoided it by
splitting big requests into independent stories (an epic's children), and the Scheduler dispatched any
admitted story as soon as a slot opened. `contas` Sprint 2 has the natural case: the budget per
category builds on the refactor to integer cents. Run in parallel, the budget story is planned on the
code before the refactor, then executed on code that no longer matches its plan — the rework that cost
the most in Sprint 1 (S-030).

The founder decided the rules on 2026-09-30 (plan 10.7 and 10.9): the relation is a *product* one,
declared by the Product Owner from the cards' text when it proposes the sprint; a dependent story
waits without bothering the founder; the specs of a whole chain are written and approved before any of
it is planned; and each story is planned right before it is built, after what it depends on is
delivered. Technical coupling found while specifying stays the spec pipeline's business.

## Decisions

### 1. `depends_on` is a column of `stories`, written only by the Product Owner

`stories.depends_on` (JSON list of story ids, `[]` by default, added on open like `priority_pinned`).
It is not part of `StoryState`: the engine projects the graph's state over the row after every node,
and a field inside it would be overwritten by a stale checkpoint. Only `Backlog.set_dependencies`
writes it (so only the Product Owner, ADR-0008), and it refuses in code: an unknown card, a card that
depends on itself, a finished card gaining dependencies, and any **cycle** (A → B → A, or longer).

### 2. Declared in the Sprint Meeting, carried by the draft

`DraftItem.depends_on` holds refs (draft keys or story ids). The Product Owner's proposal says, per
picked card, which cards it needs delivered first (`picks[].depends_on`); the founder sees each relation
on the card ("depende de D1") and can remove it (`update` with `depends_on`). `apply_ops` resolves the
refs and refuses a cycle with the existing ones; when the proposal itself has a cycle, the Product Owner
is asked once more with the refusal in English, and a relation still refused is dropped and reported.
A brainstorm's split can declare relations between its cards the same way. On commit, ids replace keys
and `set_dependencies` records them.

### 3. Assembly rule: a dependency travels with its dependent

Every dependency of a card in a sprint is in the same sprint or already `DONE`. In the proposal the
Product Owner is told so, and code brings a missing dependency that waits in the backlog into the
sprint with a note ("entrou junto: S-041 depende dela"). `commit_meeting`, `MasterAgent.start_sprint`
and a card joining the running sprint check it again and refuse a sprint that breaks it. Taking a card
out of the running sprint while a card that stays depends on it is refused too ("tire as duas").

### 4. The gate before `plan` (10.9)

A *chain* is a connected group of admitted, unfinished stories linked by `depends_on`. Before a story
of a chain runs `plan`, `dependencies.plan_gate` decides:

- **open** — every dependency is `DONE`, and every code story of the chain has its spec approved
  (it is past `spec_review`, or past `spec` when its route has no review). Planning goes ahead.
- **wait** — something above is missing. The node wrapper records the wait in the state
  (`extra.waiting`), the edge ends the run with the checkpoint kept, and the slot is free. No `block()`,
  no inbox: waiting for a sibling is time, not a decision.
- **gone** — a dependency was cancelled, or left the sprint for the backlog. The only case that needs
  the founder: `block()` with the new `BlockedReason.DEPENDENCY` and three options: cancel this one too
  (`drop`), go on without it (`detach`: the relation is removed, a note tells the spec writer the
  dependency is gone and the story goes back to `spec`), or back to the backlog (`skip`, the relation
  to the gone card removed).

Stories without relations never meet the gate. A research story has no `plan`: it only counts as a
dependency (done when `DONE`).

### 5. The Scheduler

`runnable()` leaves out a story parked at the gate while the gate still says *wait*; when it opens (or
says *gone*) the story is dispatched, the wait is cleared and the graph resumes at `plan` from its
checkpoint (the same injection a founder answer uses), never redoing the spec. Among the runnable
stories, chain stories whose spec is not approved yet go first, so the chain leaves the spec phase
quickly; the rest keep the backlog order. The stall watchdog never sees a parked story: it is not
running.

### 6. What the founder sees, computed on every read

The card carries `waiting_on`, computed from the gate (never stored): "aguardando S-040" (a dependency
not delivered), "spec aprovada · aguardando a cadeia" (specs of the chain pending) or "aguardando
S-040, que espera você" when the dependency waits on the founder. The inbox message of a story that
others depend on gains one line ("A S-041 depende desta"), so the founder knows one answer unblocks
both.

## Alternatives considered

- **`block()` the dependent.** Every block is "the founder must decide"; waiting for a sibling is not,
  and the inbox would fill with notes about time passing.
- **A filter in the Scheduler only (10.7 as first written).** The dependent would not even write its
  spec, and the specs of the chain would not be checked against each other before the code (10.9).
- **Plan early and revalidate after the dependency merges.** Two Architect passes, and a half-stale
  plan if the revalidation misses something (rejected on 2026-09-30).
- **A `Stage` for waiting.** The kanban already says where the story is (Especificação); the wait is a
  property of the moment, computed from the other stories.

## Consequences

- One new column and one new `BlockedReason`; old factories migrate on open; a card with `[]` behaves
  as before.
- A chain whose first story waits on the founder holds the rest of the chain, by design; the inbox says
  which answer unblocks it.
- Not decided here: technical dependencies found while writing a spec (they stay findings of the spec
  pipeline), and grouping by area without order (notes only).
- Not verified against a real model: the relations in the Product Owner's proposal. `contas` Sprint 2
  has the cents → budget chain to exercise it.
