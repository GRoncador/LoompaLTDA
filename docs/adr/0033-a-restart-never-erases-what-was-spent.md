# ADR-0033: A restart never erases what was spent

Date: 2026-10-05 · Status: accepted (the founder's rule)

## Context

`loompa sprint restart` (and a meeting's `restart` op) started a story over in place. The spec,
plan, branch and state were discarded under the same id, so the attempt's spec and code were lost.
Its events and cost stayed in the history, but they were mixed with those of the new attempt under
one card.

On 2026-10-05, tamagotchi S-061 had to start over after two plans and a tier-1 escalation. The
founder asked for this to keep history and cost for analysis.

## Decision

Whatever is reset to its start (a story, a sprint) follows one rule:

- **A model already ran on it, or it cost something:**
  - The original is **closed with its outcome** and keeps its history, cost, spec, plan and
    branch:
    - for a story, the state is `cancelled`, and `state.extra.outcome` holds
      `{kind: "restarted", as, reason, at}`;
    - for a sprint, the status is `cancelled`, and the outcome is in kv
      `sprint_outcome:<id>`.
  - A **copy** takes its place, from zero: the same request, epic, priority, kind and
    dependencies, and the same sprint. Dependents follow the copy.
  - The copy's id is the original's number with the next letter:
    - S-061 → S-061B → S-061C;
    - SP-009 → SP-009B.

    The lineage reads in the id, and `state.extra.restart_of` records it. The id generator reads
    only the digits, so ordinary ids go on from the highest number.
  - The copy starts at intake when the original was in the pipeline.
  - The answers the founder gave the original while it ran stay with it, unless
    `--manter-notas` is given.
- **Nothing ran and nothing was spent:** the same id is reset in place, with its worktree, branch
  and spec discarded.

`Scheduler.restart_story` and `Scheduler.restart_sprint` apply it. `loompa sprint restart` takes
story ids or a sprint id.

For a sprint that ran, the original keeps its delivered stories. The new sprint runs only the
unfinished ones, each restarted by its own rule.

## Consequences

- The cost of each attempt is its own card's, and nothing reported is ever cut.
- Ids may carry one letter (`S-\d+[A-Z]?`). The few regular expressions that read ids accept it.
