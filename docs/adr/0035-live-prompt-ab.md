# ADR-0035: A live A/B for the prompt changes the bench cannot measure

Date: 2026-10-07 · Status: accepted (plan 14.3, follow-up)

## Context

The prompt bench (ADR-0034) replays one recorded call per case. It can tell whether a candidate
changes a one-shot answer, or the first call of a tool loop. Three ideas of plan 14.3 act later
than that:

- **P5, general solution (Worker):** write the logic that is right for every input, and never tune
  a value until a check passes. It acts while the Worker fixes a red test, deep in its loop.
- **P7, tests through the interface (Worker):** the expected value is a literal from the spec, and
  mocks stay at the system's boundary. It acts when the Worker writes the test.
- **P2, tracer bullet (Architect):** the first task cuts a thin path through every layer. The judge
  only gives an opinion on the plan; the idea's claim is less rework.

On the bench, the Worker's first call did not change with P5 or P7 (it reads the same files), and
the judge tied P2 (14×17). None of them can be validated there.

## Decision

A factory can run a variant of a prompt for a share of its stories and compare the stories of the
two arms by what happened to them.

- **Variants live in `loompa/prompt_ab.py`** as edits on the code's prompt. The tests check each
  one as the bench checks a candidate: every edit is found once, and the JSON contract, the dry-run
  phrases and `{language}` are kept. A variant that no longer fits the prompt runs the code's text.
  `docs/bench/make_candidates.py` takes P2, P5 and P7 from there, so the bench and the live A/B
  read one source.
- **Config:** `schedule.prompt_ab: [{prompt, variant, share}]`, empty by default.
  - `loompa prompts ab on worker.SYSTEM tests-and-general --share 0.5` turns one on;
  - `... off worker.SYSTEM` turns it off;
  - `... show` prints a variant's diff.
- **The arm is drawn per story,** not per task: the effect is on the story. It is a hash of story,
  prompt and variant, stored in the story's state the first time, so every task and every retry of
  the story reads the same text. A change of share never moves a story already running, and a
  story that already has commits when the experiment is turned on stays out of it. The
  assignment is announced once with a `prompt.ab` event, and each `worker.task` carries its
  `prompt_ab` arm.
- **Wired prompts:**
  - `worker.SYSTEM`, the built-in Worker (the OpenCode backend has its own prompt);
  - `architect.SYSTEM`, the full plan (a SIMPLE story keeps the short form).

  Wiring another prompt is one `prompt_for` call where the agent builds its system message.
- **The report** (`loompa prompts ab`) counts the settled stories of each arm (DONE or CANCELLED).
  - Per story: delivered, needed rework (retries, re-plans and restarts), Inspector fails, `TEST`
    findings, blocks, Worker tasks that did not finish, tasks, and cost.
  - `inspector.verdict` now carries the count of each finding prefix for this.
  - The two arms are compared on "the story needed rework" by Fisher's exact test, because with a
    handful of stories per arm a normal approximation lies.
  - Below 10 settled stories per arm the report says it cannot tell yet.

## Consequences

- **Variants available:** `worker.SYSTEM` `general-solution` (P5), `test-seams` (P7) and
  `tests-and-general` (both), and `architect.SYSTEM` `tracer` (P2).
- **One experiment per prompt.** Experiments on different prompts draw independently, so the
  Worker's and the Architect's arms cross (a 2×2).
- **It takes many stories.** A sprint has 3 to 5 stories, so 10 settled stories per arm means
  several sprints. The decision stays the founder's, read from the report. Until it is made, half
  the stories run a prompt the bench could not validate. On the bench that prompt was safe at the
  first call (P5, P7) or tied with the current one (P2).
- **The texts outside the registry** (o1 reproducer, o2 diagnosis on retry, o4 Ops, o5 glossary) are
  not variants yet. They are built in code, not taken from a prompt constant. They can join through
  the same `prompt_for` call when one of them is next.
- **Turning it off** is `loompa prompts ab off <prompt>`. The stories already assigned keep their
  arm and their numbers.
