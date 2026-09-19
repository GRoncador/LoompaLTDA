# ADR-0008: Backlog authority, sprints and batch decisions

Date: 2026-09-19 · Status: accepted (implements ADR-0006 §5, phase 3 of `docs/PLANO-2026-09.md`)

## Context

ADR-0006 promised authority in code: one writer for the backlog, git operations reserved to the
Deployer, sprints that scope dispatch and batch answers on the delivery. It left the shapes
open. Before this change Master, Kaizen, the dashboard and the founder's answers each created or
ranked stories directly, any agent could call the raw `git()` helper, the Scheduler ran every
card outside a single exception (Kaizen cards) and each delivery asked one question at a time.

## Decisions

### 1. `Backlog` service, held only by the Product Owner

`loompa/backlog.py` exposes `add_item`, `set_priority`, `set_status` and `admit`. The constructor
refuses any owner whose role is not `product_owner`; `ProductOwnerAgent` is the only place that
builds one, and it forwards the same four methods. Master (meeting, epic split), Kaizen, the
dashboard's create-story endpoint, `Scheduler.promote` and `apply_founder_answer` (skip/drop) go
through the agent. A test walks the AST of `src/loompa` and fails if any other module calls
`upsert_story`/`next_story_id` or ranks a card with `update_story(priority=...)`.

* `add_item` returns the open card that already has the same normalized title instead of
  creating a duplicate (Kaizen findings that repeat now point at the existing card).
* `set_status` accepts only `BACKLOG` (deferred) and `CANCELLED`; every other stage is the
  engine's. Progress writes (stage, checkpoints, cost) stay with the engine: that is runtime
  state, not backlog authority.
* `admit` moves a card out of `BACKLOG` to `SPEC` with `phase="intake"`. It is the only door into
  the pipeline.

### 2. Git authority is a role on the manager, not a convention

`WorktreeManager` carries an `actor`; `as_role(role)` returns a view for that role and
`LoompaAgent.git` hands every agent the view for its own role. Merge, rebase, push, pull,
checkout, switch, reset, cherry-pick, tag and branch deletion, whether called through a named
method or the raw `git()` (including global options such as `-c`), raise `GitAuthorityError`
unless the actor is `deployer`. The check runs before anything is touched (`remove(delete_branch)`
first tests, then removes). PR creation moved from the Deployer's subprocess calls into
`WorktreeManager.open_pull_request`. The OpenCode agent file denies the same commands and `gh`
in its `bash` permissions (last matching pattern wins, so the denials follow the wildcard).
Ordinary work inside a story's own branch (status, diff, log, add, commit) stays open.

### 3. Sprints gate admission, not in-flight work

`Sprint` (`SP-001`…) is `open` while planned, `running` once started and `closed` when every
story is terminal. The Master conducts the Sprint Meeting (`start_sprint`): it collects the
stories (explicit ids, else the draft, else the founder's cards in priority order; Kaizen
findings only when named), the Product Owner admits each, and the sprint starts.

The Scheduler no longer dispatches anything in `BACKLOG`. It still runs every story that is
already in the pipeline, sprint or not, so stories in flight before this change, a resumed
story or a `promote` keep going. Consequences worth stating:

* A blocked story (`AWAITING_FOUNDER`) waits alone; the rest of its sprint keeps running. The
  sprint closes, emits `sprint.done` and drops a plain pt-BR inbox note once nothing is left,
  checked every scheduler cycle and right after an inbox answer.
* Epic children join the parent's sprint and are admitted at once: the founder already cleared
  the parent.
* `promote` stays as an explicit "run this one now" lane outside any sprint (hotfix).
* Several sprints may be running; nothing serializes them. A story belongs to one sprint.
* `loompa meeting --run` and the dashboard's meeting-with-run start a sprint with the stories
  just created; plain `meeting` only fills the backlog.

### 4. Batch decisions, not batch messages

`FounderMessage.decisions[]` holds independent side decisions (`id` = the card, options,
`chosen`); `FounderAnswer.decisions` maps decision id to option key. A delivery keeps its
`approve`/`changes` options and adds one decision per card the work suggested: `sprint`
(put it in the sprint being planned), `backlog` (leave it), `drop` (cancel). The recommended
option is `sprint` for bugs and `backlog` for the rest.

* Each decision is applied on its own by `ProductOwnerAgent.resolve_finding`; unknown ids,
  unknown options and cards already decided are ignored.
* An answer with only decisions leaves the message pending, so the founder can settle the
  cards now and the delivery later.
* Cards decided once are not offered again when the same story delivers again.
* "Findings never get lost": Kaizen still files a card for every finding (now through the
  Product Owner) and `node_review` sweeps any finding no earlier phase filed (for example a risk
  the founder accepted) before delivering. A card nobody decides simply stays in the backlog and
  the end-of-day report counts it.

## Alternatives considered

* **Strict sprint mode** (nothing runs outside a sprint, in-flight included): would freeze
  stories that exist today and make `promote`, resumes and epic children special cases anyway.
* **Sprint membership as the only gate, no `admit` at start**: the Scheduler would have to join
  sprints and stories on every cycle, and the kanban would show admitted cards as backlog.
  Admitting at start keeps the columns truthful and the Scheduler oblivious to sprints.
* **Making `Store.upsert_story` refuse callers**: the store has no notion of caller, and tests
  legitimately seed rows. The static test plus the owner check on `Backlog` give the same
  guarantee where it matters, at review time.
* **`decisions[]` replacing `options`**: would break every consumer of the single-decision
  message (CLI, dashboard, existing answers) for no gain.

## Consequences

* Dispatch needs an explicit step (`loompa sprint start`, the kanban button, or `meeting --run`).
  `loompa run` prints how many cards are waiting.
* `stories` rows of existing factories keep working; the new `sprints` table is created on open.
* Unverified: the OpenCode permission patterns have not been run against a real `opencode`
  install (see ADR-0007); the Loompa-side guard does not depend on them.
