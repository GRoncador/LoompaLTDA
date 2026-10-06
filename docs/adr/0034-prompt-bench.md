# ADR-0034: A prompt bench before any prompt change

Date: 2026-10-06 · Status: accepted (plan 14)

## Context

Loompa's prompts follow a fixed shape: a role, rules with their reason, the material marked as
data, what to do when unsure, and "Respond with JSON only". Many rules were born from real
failures. Swapping text without measuring swaps a validated rule for a guess. Each line added to
the Worker's prompt is also paid for on hundreds of calls.

The Founder asked to bring in ideas from Matt Pocock's skills and from other references the AI
community validated:

- the Anthropic and OpenAI prompting guides;
- INVEST and examples-first for user stories;
- Chain of Density.

Each idea must be measured. The prompts must stay product-agnostic: no factory, story id or
product example in their text.

## Decision

### Cases (14.1)

`loompa prompts harvest` takes real calls from the factories' traces (`.loompa/traces/`):

- up to N calls per prompt and per call shape, spread over stories, with repeated inputs dropped;
- each call keeps its messages, answer, model, tokens and cost;
- each call keeps the story's outcome: stage, review rounds, the Inspector's verdict, the
  founder's notes.

The prompt is not declared anywhere: every `*SYSTEM` constant of the agents is found by its
`<!-- role:… -->` marker. A recorded system message is matched to its constant by text
similarity, so recordings made before a rewrite still count.

The three call shapes:

| Shape | What the call is | How it is replayed |
| --- | --- | --- |
| `one_shot` | system + user | as recorded |
| `first` | the opening call of a tool loop | with the same tools offered |
| `final` | the call that closed a tool loop | the exploration flattened to text, no tools |

Cases live in `~/.loompa/bench/cases/<factory>/`, never in a repository: like the traces, they
carry product code.

### Compare (14.2)

`loompa prompts compare <prompt>` replays the same cases through two variants. A variant is a
prompt text, a model, or both:

- A is the code's prompt by default (`--candidate-a` to change it);
- B is set with `--candidate`, `--model-a` and `--model-b`.

The tail an agent appends to its prompt is kept from the recording: the Worker's story block, the
explore hint.

Each answer is measured three ways:

- **Checks in code.**
  - The answer is valid JSON.
  - It keeps the contract of today's prompt: keys, coarse types and enum values, parsed from its
    "Respond with JSON only" line. The code that reads the answer did not change with the
    candidate, so the old contract holds.
  - For text the founder reads: it passes `audit_executive_text`, it is Portuguese, it has no
    JSON inside the string, it is not a task list, and it keeps the prompt's sentence limit.
  - The decision fields match the recorded decision.
- **A blind pairwise judge.**
  - The two answers come in an order drawn from the case id.
  - The judge is the factory's strongest model from a maker that made neither variant. The first
    run had a MiMo judge give a MiMo variant 10 of 11.
  - A rubric of what the reader needs goes with the prompt.
  - There is no judge for `first` calls: a judge cannot weigh a tool call.
- **Cost.** Tokens, cost and latency.

The report is written to `~/.loompa/bench/runs/` with every answer, so a number can be read case
by case.

### Contradiction audit

`scripts/prompt_contradictions.py` asks a strong model, for each long prompt, which rules cannot
both hold in some realistic case. The idea comes from OpenAI's GPT-5 guide: contradictory
instructions cost reasoning models more than an arbitrary choice would.

## What the bench found on its first day

- **Truncated context.** All 40 tamagotchi specs were longer than the 4000-character head the
  Worker received (median 8.2k). Its plan, 33 of 37, was cut too. As a result:
  - the Worker built from about half the acceptance criteria;
  - it never saw the criteria the Product Owner revised, nor the founder's adjustment to the plan;
  - the Product Owner reviewed specs cut mid-word.

  `speckit.artifacts.fit_sections` now gives each reader whole sections by its own priority, and
  says what it left out (commit d14806a).
- **Founder text on the free tier.**

  | Prompt | Tier 3 (nemotron-3-ultra free) | Tier 2 (mimo-v2.6-flash) | Judge |
  | --- | --- | --- | --- |
  | Delivery summary | lost | won | 2×10 |
  | Blocking options | lost | won | 4×7 |
  | Meeting brief | lost | won | 4×6 |
  | Sprint report | 27% invalid JSON, 115 s a call | — | 5×3 |

  Tier 2 costs about US$ 0.0002 a call, so these tasks default to tier 2 (config revision 6,
  commit 6c86f67).
- **A model leaving the free list.** `qwen/qwen3.8-27b:free` stopped being free (404).

## Consequences

- No prompt text changes without a `compare` report. Candidates are built by
  `docs/bench/make_candidates.py` as edits on today's text, one idea per candidate, on top of the
  agnostic baseline.
- The same bench chooses models per task (it settled the founder-text tier), and it will measure
  the prompts of Fases 15 to 17.
- What the bench cannot measure is said, not assumed:
  - a `first` call shows what the Worker does first, not whether the task ends better;
  - a `final` replay gives the candidate the exploration the old prompt chose.

## State on 2026-10-06

- **The judge cut answers.** Until 5055d45 it read each answer cut at 6000 characters, so long
  specs and plans read as truncated. Any result on `final` calls from before that is void,
  including the first "tracer bullet" run.
- **14.3 paused by the Founder.** He wants each change shown before it is measured, with its
  sources, and prompts kept product-agnostic. `docs/bench/make_candidates.py` builds:
  - P0: today's prompts with factory names and examples replaced by general reasons;
  - P1-P6: one idea each on top of P0.

  `scripts/prompt_contradictions.py` found 5 conflicting rules (P6). Three of the 17 comparisons
  ran (`docs/bench/summarize.py ~/.loompa/bench/logs/r2`). **No prompt in the code was changed.**
- **14.3 revised, still for the Founder's review.** `docs/bench/PROPOSTA-14.3.md` maps 31
  patterns from Matt Pocock's skills and 14 other references (the Anthropic, OpenAI and Google
  guides, obra/superpowers, Spec Kit, Specification by Example, INVEST) onto the prompts. For
  each it says where the pattern enters the code, or why it stays out.
  - The proposal is P0 completed plus P1-P10, on 16 prompts, and 6 texts outside the bench.
  - `make_candidates.py` writes one file per idea and a `proposed` file per prompt.
  - Every candidate is checked: same contract, dry-run markers kept, `{language}` formats, no
    product word.
  - `PROMPTS-14.3.md` has each prompt, current and proposed, in full.
  - `run_round3.sh` measures it all; it has not run yet.
