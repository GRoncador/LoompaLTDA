# ADR-0026: The plan's own gate — coverage checked in code, review by evidence, complexity from the plan

Date: 2026-10-04 · Status: accepted (extends ADR-0006's pipeline-as-data with one phase, and
ADR-0014's separation of duties to the plan; narrows the blind spot ADR-0016's escalation paid for)

## Context

The route had no review between `plan` and `dev`. Two consequences, both found by reading the code
on 2026-10-04:

1. `PHASES["plan"]` and `PHASES["preflight"]` were registered with `reviewer="product_owner"`, and
   `Phase.reviewer` is read nowhere except `register`'s separation-of-duties assertion. The plan
   declared a reviewer it never had. The only second look at a plan was `preflight`, owned by the
   Architect: the author checking its own work, which is exactly what `register` refuses inside a
   phase (AIOX's `executor != quality_gate`, Fase 7 item 7.4).
2. Completeness was self-asserted. `_traceability` already writes `**sem teste planejado**` for a
   criterion the Architect planned no test for, and nothing blocked on it; `constitution_check`
   with `ok=false` only added a line under "riscos". The one external check of the acceptance
   criteria is the Inspector's judge at `test` (`inspector.run`), which runs after the whole
   implementation exists. A criterion the plan forgot therefore costs a full development cycle,
   and often an escalation, to discover.

And `STANDARD` is not a middle: `master.classify` falls back to it for anything it cannot label
(`Complexity(str(data.get("complexity") or "STANDARD"))`), while the prompt defines only the two
ends. So the bucket holding the stories nobody was sure about was the one with the least scrutiny
of its plan: `spec_review`, no `preflight`, no plan review.

## Decisions

### 1. A `plan_review` phase, owned by the Product Owner

`BASE_ROUTE` becomes `intake → spec → spec_review → plan → plan_review → dev → test → review`, for
every kind and complexity, research excepted (it has no plan). `with_preflight` now inserts
`preflight` after `plan_review`: the gate is cheap, and a plan it sends back must not pay for the
pre-flight twice. One rewrite round at most, as with `spec_review` — `plan_review_rounds` — and the
escalation's re-plan resets it so the new plan gets its own round.

### 2. The checks run in code, for every story, at no token cost

`loompa/plan_check.py` measures three things against the repository and the spec:

* an acceptance criterion the traceability skips, or marks with no planned test;
* a task that names no file at all (the plan prompt requires each task to name the files it touches);
* a task that asks to **change** a file that is not in the repository — the plan was written against
  a tree that is not this one.

A plan carrying any of them is re-planned once, with the problems named in the hand-off the
Architect's prompt now reads. Problems that survive the round join `failure_history` (where the
Architect and the Worker see them) instead of holding the line.

What is deliberately *not* checked: a fence path that does not exist yet. A new module in a new
folder is how a feature normally starts and the Worker creates it; only a clause that says it is
changing something already there can be measured. The first version of this gate refused
`web/save.js` in a legitimately new folder.

### 3. The review call is paid by evidence, not by label

`ProductOwnerAgent.review_plan` (task `product_owner.plan_review`, tier 2, tier 1 on a COMPLEX
story) judges coverage, scope, proof, order and fence over plan plus tasks, with the measured
problems given to it as facts. It is called only when the plan asks for it: a COMPLEX story, a
route that earned its pre-flight, a plan whose own size promoted it, a re-plan after failures, or
problems the round did not clear. A SIMPLE or plain STANDARD plan that passes the checks goes
straight to the Worker. Why tier 1 on COMPLEX while `spec_review` stays on tier 2: this is the last
gate before code is written, and the cycle it prevents costs more than the call.

### 4. The plan promotes the story intake under-labelled

A plan with more than 6 tasks or more than 8 product paths makes the story COMPLEX, lifts autonomy
to `PREFLIGHT` and inserts the phase — the same move `node_plan` already makes from the plan's own
risky paths (`needs_preflight`), from a different signal. The plan is better evidence of size than
a guess made before anyone had read the code. The founder's explicit `schedule.autonomy` still
wins, as everywhere else.

### 5. The plan step stays, including for SIMPLE stories

Considered and rejected: dropping `plan` for SIMPLE. `plan` is not a document step — it produces
`state.allowed_paths` (the only paths the Worker may touch) and `tasks.md`. Removing it would leave
a story with no fence and no task list. What a simple story overpays for is the artifact's
ceremony (alternatives, impact, rollback, ADR proposal), and that is a separate change to the
prompt, not to the route — made in ADR-0028.

## Consequences

* Every story pays three regex-level checks and, when they are clean and the story is small, no
  review call at all. `--dry-run` passes its own gate: the simulated plan now carries one
  traceability line per criterion and a task naming the file it writes.
* The dashboard sees `plan.checked`, `plan.approved` / `plan.rejected` and `story.complexity`
  (`plan_review` projects onto the PLAN column).
* `Phase.reviewer` on `plan` means something again. `preflight` is still Architect-owned and still
  self-reviewing, now downstream of a real review; left as it is on purpose — it checks a plan
  against facts measured in code, not against the story's intent.
* Nothing founder-facing changes: the gate's problems name paths and criterion numbers and stay in
  English, inside the model-facing hand-off and `failure_history`.
