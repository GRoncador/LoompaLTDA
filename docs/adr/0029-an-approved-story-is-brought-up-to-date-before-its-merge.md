# ADR-0029: An approved story is brought up to date before its merge

Date: 2026-10-05 · Status: accepted (revises the merge-after-approval part of ADR-0016 §5)

## Context

Tamagotchi SP-008: the founder approved S-057, S-054 and S-053 within 20 seconds. All four open
deliveries had branched from the same commit. S-057 merged; S-054 and S-053 conflicted with it
(`index.html`, `tests/casca.test.js`, `tests/sprites.test.js`) and both reached the founder as
"approved, but could not be integrated". The conflicts were trivial — two `describe` blocks
appended at the end of the same test file, one comment edited on both sides — and the factory
already had the machinery that resolves them (`sync_with_base`, `settle_additions`,
`_resolve_with_retries`). It never ran, for four reasons in the code:

1. `merge_into_base` did not abort a failed merge. The main checkout was left mid-merge, with
   conflict markers in the product's `index.html`, while the founder's message said the product
   "continues to work normally".
2. Nothing ordered the merges. S-054's merge failed and left the checkout half-merged; 16 s later
   S-053's merge met that state. Each diagnosis then chose `clean_main_merge` and aborted the
   *other* story's merge.
3. The delivery's rebase is the only point where a story caught up with the base. Between the
   delivery and the approval the base moved, and `_merge_after_approval` left the next step to a
   model choosing among four actions. It never chose `resync`.
4. The diagnosis saw `.loompa/` (specs, learnings, config — always dirty in the main checkout) as
   "uncommitted changes, the founder's work", which the prompt says to ask about.

## Decisions

### 1. The merge into the base never leaves the main checkout half-done

`merge_into_base` computes the conflicts first (`git merge-tree --write-tree`, git 2.38+) and
raises `MergeConflict` without touching the checkout. A merge that fails anyway (an older git, an
error not foreseen) is aborted before the error goes up. A merge already in progress there is
refused rather than stacked on; whose it is, and whether to abort it, stays the diagnosis' call.

### 2. Approved stories merge one at a time

`EngineContext.integration_lock` covers the whole post-approval integration (sync, checks,
merge). An approval that arrives while it is held does not merge directly: it goes to `review`
and waits its turn.

### 3. Catching up is a rule, not a model's choice

The approval merges directly only when the branch already contains the base. Otherwise
`review` merges the base into the story in its worktree (the same `sync_with_base` as `dev`:
append-only blocks settled without a model, the rest to the Worker), re-runs the checks that need
no model (`InspectorAgent.run(judge=False)`: tests, lint, types, smoke, hygiene), and merges.
No judge: the founder accepted the behaviour; only the combination is new.

- Green → merged, without asking again.
- Red → back to `dev` with the failure, then a new delivery. The code changed beyond a mechanical
  merge, so the founder's approval of the earlier code does not cover it.
- Conflicts the Worker could not resolve → `BlockedReason.CONFLICT`, whose answer goes through
  `dev`.

Only a git failure that is not a conflict reaches the Deployer's diagnosis, after one plain retry.
The diagnosis no longer sees `.loompa/` and runs at `reasoning_effort="low"`, 600 tokens: S-054
spent 11k tokens and 220 s choosing one of four actions.

### 4. Integration state survives a restart

`MERGE_FAILED_KEY` is read, not popped, when `review` starts, and cleared where the integration
ends. The runner checkpoints only when a node ends, and a restart mid-integration would otherwise
deliver the approved story again. The same flaw replayed the Product Owner's criteria alignment
on every restart in `dev` (S-057: eight rewrites, eight sections appended to its spec):
`_align_once` keeps the answer in the store under the request and the criteria it was given.

### 5. The founder is not reassured beyond the facts

`EXEC_SYSTEM` now says that the product works, or that its tests pass, only when the material says
so.

## Consequences

- An approval after the base moved costs one test and smoke run in the story's worktree (seconds in
  tamagotchi) and, on a conflict, a Worker call. It no longer costs a founder round.
- A combination that breaks is caught before the base, not after.
- Not addressed here: parallel stories still concentrate their tests in a few large files, which is
  what makes the conflicts likely in the first place.
