# ADR-0028: The short plan for a simple story

Date: 2026-10-04 · Status: accepted (completes ADR-0026, which kept the `plan` phase for every
story and left its ceremony for a separate change)

## Context

ADR-0026 settled that `plan` stays in the route even for a SIMPLE story, because the phase is not a
document step: it produces `state.allowed_paths` (the only paths the Worker may touch) and
`tasks.md`. What it also noted, and left open, is that a one-file change was paying for the whole
long form: `alternatives_considered` (at least two designs weighed), `impact` (what else uses the
code and how compatibility is kept), `rollback`, `constitution_check` (a table of the rules the
plan touches) and `adr_proposal`. Nothing in `architect.py` varied by complexity — a `grep` for
`complexity` there had no hits. Only the tier did (`architect.plan`: tier 2, tier 1 on COMPLEX).

That is paid in output tokens, in latency, and in the model's attention: a story that changes a
label was asked to argue with the constitution and to describe a rollback it does not have.

## Decisions

### 1. Two forms of the same prompt, chosen by the complexity

`SIMPLE_SYSTEM` keeps every field the engine reads — `approach`, `files`, `tasks` with their
`verify`, `traceability` per acceptance criterion, `risks`, `blocker` — and drops the five the long
form asks for. It also lowers the task range from 2-8 to 1-3, which is what SIMPLE means.

`ArchitectAgent.run` picks it when the story is SIMPLE, has not been sent back for the long form,
and is not a re-plan after failed attempts (`REPLANNED_KEY`): a story whose two attempts failed is
not simple any more, whatever its label says. The chosen form is recorded in `state.extra` under
`LEAN_KEY` for the one node that needs to know.

The empty sections still render: `render_plan` writes `_(nenhum)_` for a missing list and its own
default sentence for `impact` and `rollback`, so `plan.md` keeps its shape and the pre-flight,
the Worker and the panel read the same file as always.

### 2. Two ways back to the long form, because the label is a guess

* `not_simple`, a boolean the short form may set: the Architect itself says the work needs the full
  treatment (a schema, a migration or a contract; a library the constitution does not allow; a rule
  it cannot keep; more than three tasks; existing behaviour whose users must be described). The
  prompt tells it to set the flag *instead of* writing the long form, so the cheap call is not
  spent writing what will be thrown away.
* `needs_preflight(state.allowed_paths)` on the plan's own paths, read in `node_plan` as it already
  was for the autonomy lift. A plan that touches a schema, a migration or a public contract is
  planned twice anyway (ADR-0021/7.5), and the pre-flight reads exactly the impact and the rollback
  the short form never asked for.

Either way `node_plan` records why in `FULL_PLAN_KEY`, emits `plan.not_simple` and sends the story
back to `plan` — once, because the second plan is the long one and `LEAN_KEY` is then false. The
spec is untouched: this is not the `blocker` path, which bounces the spec back to the Product.

## Consequences

* A SIMPLE story pays for five fewer sections. A SIMPLE story that is not really simple pays for
  two plans instead of one — the trade this accepts, on the bet that the first case is the common
  one and the second is the one where planning twice was already the rule.
* The route is unchanged: `plan_review`'s checks in code (ADR-0026) read `files`, `tasks` and the
  traceability, all of which the short form still produces, so the gate judges both forms the same.
* The panel's history says "a história é maior do que parecia" when the story goes back for the
  long form, instead of showing two identical "plano com N tarefas" lines.
* Both prompts keep `<!-- role:architect -->` and the user message's `## Repository outline`, which
  is what the dry-run provider and the tests route on.
