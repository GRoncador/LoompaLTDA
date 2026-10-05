# ADR-0027: An integration branch for separate pending deliveries, and keeping up with the delivery underneath

Date: 2026-10-04 · Status: accepted (extends ADR-0021's gate before `plan` and its 11.5 rule that a
delivery the Inspector passed is good enough to build on)

## Context

ADR-0021/11.5 let a story build on a dependency that is built and green but not accepted yet: the
dependent's worktree starts from that dependency's branch, so it has the code, and only its *merge*
waits for the founder. The rule was limited to one such dependency, because a worktree starts from
one branch:

```python
if len(out.unmerged) > 1 and not tip_of(rows, out.unmerged):
    out.pending.extend(out.unmerged)
    out.founder.extend(out.unmerged)   # the story waits for an acceptance
```

So a story depending on two deliveries that are *not* a chain stopped, even with both green. Two
Inspector PASSes did not open the gate; only the founder's acceptance of one of them did. Starting
from either branch alone was never an option: the story would have been planned and built against a
tree holding half of what it depends on, which is worse than waiting, because the plan looks valid.

A second, quieter problem came out of the same reading. `WorktreeManager.get` cannot know what a
worktree was created from, and answers with the default branch:

```python
return Worktree(story_id=story_id, path=path, branch=branch, base=self.default_branch())
```

Everything downstream reads `wt.base`: the baseline the Inspector takes on the base, and
`sync_with_base`. So a story written on top of an unaccepted delivery synced only with the base. If
the founder asked for changes in that delivery and the Worker fixed them, the dependent never saw
the fix — it kept the snapshot of the branch from the moment its worktree was created.

## Decisions

### 1. The Deployer builds an integration branch, and the gate stops waiting

`plan_gate` no longer sends separate pending deliveries to the founder. It reports them in a new
field, `integrate`, and stays OPEN. `gate_plan` then asks the Deployer for one branch
(`WorktreeManager.integrate`): a branch off the base with each delivery's branch merged into it,
built in a throw-away checkout. The story's worktree starts from it.

The authority rules are unchanged: only the Deployer merges (ADR-0008), and `integrate` calls
`_require_deployer`.

### 2. A conflict between two unaccepted deliveries stops the story and asks

If the merges conflict, nothing is kept — the branch and its checkout are removed — and the story
blocks with `BlockedReason.INTEGRATION`. The factory does not resolve it, although it has the
machinery to (`_resolve_with_retries`): the conflict is between two deliveries the founder has not
accepted, and resolving it would pick a side, inside a third story's branch, on code that may still
be sent back. The options are "I will accept one of them first" (the story returns to the gate and
parks, which is the old behaviour), back to the backlog, or cancel. Accepting one settles it: that
one lands in the base and the story starts from the other's branch, the 11.5 path.

### 3. A story has parents, not a parent

`built_on` (one story) plus the integration's `deps` make up `parents(row)`. `branch_carries` walks
them breadth-first, so `tip_of` still works and a fourth story built on this one inherits the whole
set; `merge_gate` holds the story until **every** parent is in the base; `gone_under` reports the
first parent that was cancelled or sent back. `BUILT_ON_KEY` keeps its meaning and its shape, so
stories recorded before this change read the same.

### 4. The sync follows the parents' branches, then the base

`dependencies.sync_refs` lists what `node_dev` brings into a story's branch: each parent's branch
while that parent is not in the base, and then the base. `sync_with_base` takes those refs and
merges them in order, stopping at the first conflict (resolved as before, then `finish_sync`). Once
a parent is merged, its branch drops off the list and its code arrives with the base.
`_ensure_worktree` also restores `wt.base` to the branch the worktree really started from, so the
Inspector's baseline is taken on a tree that has the code this story was written on top of.

## Consequences

* A story on two separate green deliveries now runs instead of waiting; the founder is asked only
  when the two deliveries actually disagree, which is information about the product.
* The integration branch is created once, as a starting point; drift is handled by the sync refs,
  not by rebuilding it. A conflict that appears later between a parent's new commits and the story
  is an ordinary sync conflict, resolved by the Worker as any other.
* One more branch per such story (`loompa/integration/<story>`), deleted and rebuilt if the set of
  pending deliveries changes, and removed outright on conflict. It is not pruned by
  `prune_merged_branches` until it is merged into the base, which it never is — a cleanup worth
  adding when branch hygiene is next touched.
* The founder's message names titles only, never branches or files: the audit (`executive_audit`) is
  asserted in the tests.
