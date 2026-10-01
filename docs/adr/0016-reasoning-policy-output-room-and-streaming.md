# ADR-0016: Two reasoning states, output room as a backstop, and streamed calls

Date: 2026-09-30 · Status: accepted (Plano set/2026 · item "Perfis de raciocínio"; amends ADR-0015 §9
and the cut handling of 2026-09-29; builds on ADR-0011)

## Context

The live 8a smoke run (ADR-0015 §9) cut the Architect's plan for a SIMPLE story at 4k, 8k and 16k
tokens on glm-5.3-flash and again on deepseek-v4-flash: 12 minutes, US$0.05, story blocked. The
first reaction (lower the effort on a cut that was all reasoning) made the plan in 45 s, but the
founder asked whether a planning call should ever think less, and whether the budgets were the real
problem. The same call was then replayed on purpose (about US$0.11 in all):

| model | effort sent | reasoning tokens | time |
| --- | --- | --- | --- |
| glm-5.3-flash | none (provider default) | 12–18k, all three runs finished at 32k room | 2.4–3.2 min |
| glm-5.3-flash | `max` | 9–20k | 2–4 min |
| glm-5.3-flash | `high` | 0.2–0.6k | ~25 s |
| glm-5.3-flash | `medium` | ~0.2k | ~17 s |
| glm-5.3-flash | `low` | 0.3–0.4k | 15–47 s |
| deepseek-v4-flash | none | > 16k (cut) | 5 min |
| deepseek-v4-flash | `medium` / `low` | 0.2–1.1k | 75–105 s |

What it showed:

1. **Effort labels are not a scale across models.** On glm-5.3-flash `high` thinks *less* than
   the default; `medium` and `high` behave like `low`. Only two states were reliable on both
   models: the provider's default, and `low`, which never thought more and never answered better.
2. **The default converges.** The plan needed ~20k tokens; the ladder 4k → 8k → 16k stopped just
   short and paid for three discarded attempts. The budgets (4096 × complexity factor 1 / 1.5 / 3)
   were never measured: 4096 came from the Fase 1 scaffold, the factors from the S-002 cut.
3. **The default plans were better**: a pure module split out, more real risks named, and the
   only plan that got Click's `no_args_is_help` exit code right. The `low` ones were alike.
4. **Where the time goes**: `contas` Sprint 1 ran at the default; the Worker was 72% of the model
   time (992 of 1374 min, 3195 rounds) and 78% of the cost. A Worker round at `low` in the smoke
   run took 2.0 s and 162 tokens against 4.2 s and 571 tokens at the default.
5. **Wall clock, not tokens, capped long calls**: the 600 s call timeout at DeepSeek's ~53 tokens/s
   (served by Relace) ends a call near 32k tokens whatever the budget.

A per-model calibration of three or more depth levels was considered and dropped: it would cost
US$0.20–0.50 per model and per provider, and the evidence shows only two states exist anyway.

## Decisions

### 1. Two reasoning states: the default, and `low` only to think less

- **Default** (no effort sent, or the founder's per-model `reasoning_effort` when set): every call
  that decides the product or repairs a failure — classification, spec, spec review, plan, re-plan,
  pre-flight, the founder's amendment, the judge, the PO's criteria review and idea admission, the
  Analyst's research, the compliance and metrics Loompas, conflict resolution, the fix pass, the
  self-check pass and the reproducer retry.
- **`low`**: calls where thinking adds little and a mistake is caught downstream — the Worker's
  task rounds and its "is the task complete" check, the delivery summary, the founder's options on
  a block, folding a lesson into the constitution, copywriting, and chat turns (already `low`).
- `medium`, `high` and `max` are never sent by the factory. "More reasoning" for a complex story or
  a problem comes from the default effort with room to finish, and from a stronger tier.
- The founder's per-model `reasoning_effort` stays a manual override for default calls.

### 2. The Worker goes back to the default when something goes wrong

A Worker task starts at `low`. The first sign of trouble moves the rest of that task to the default
(`llm.reasoning_raised` says why): a failing test run, a syntax or lint problem in what it just
wrote, a LoopGuard note, or lookups answered from memory twice in a row. The reproducer task (whose
tests are meant to fail) does not raise on a failing run. A later attempt of the story (anything
after the first failure), the fix pass, the self-check pass and the reproducer retry run at the
default throughout. The story ladder stays as it is — tier 2, tier 2 again, tier 1 re-planned,
the founder — but its second rung is now at the default everywhere; there is no "tier 1 high"
rung, because `high` is not a deeper state on these models.

### 3. Output room is a backstop, not an estimate

A call pays only for what it writes, so the budget only decides when a call is cut:

- `models.light_output_tokens` (16384) for `low` calls; `models.full_output_tokens` (128000) for
  default calls; never above the candidate's own `max_output_tokens` when the founder set one.
  128000 fits every provider of glm-5.3 and qwen3.8-max and all but 4 of 33 (glm-5.3-flash) and 1 of
  29 (deepseek-v4-flash) that accept tools.
- A cut answer gets twice the room up to `models.max_output_ceiling` (128000), then the next
  candidate at the **same** effort. The effort is never lowered on a cut: the rule of ADR-0015 §9
  and the older "less thinking at the ceiling" are withdrawn. The cut attempt keeps its tail and
  reasoning tokens in the trace (ADR-0015 §9).
- `models.output_scale` and the global `models.max_output_tokens` no longer size calls.
- Worst case of a runaway answer at 128k: about US$0.07 on glm-5.3-flash, US$0.16 on
  deepseek-v4-flash. No loop detector is built: no cut so far was a loop (each converged with room);
  the trace keeps the tail of any cut, and a detector comes if Sprint 2 shows real loops.

### 4. Calls are streamed; the timeout is silence, not duration

OpenAI-compatible providers (OpenRouter, the preset) are called with `stream: true` and
`stream_options.include_usage`. Content, reasoning and tool-call deltas are assembled as they
arrive (Gemini's `extra_content` signatures included), the final chunk carries the usage and the
billed cost, and a mid-stream error is an `LLMError`. A server that answers with plain JSON is still
read as before. The call fails as retryable when no token arrives for `models.stream_idle_s` (300);
keep-alive comments prove the connection, not progress. There is no wall-clock limit on a streamed
call: the output room ends a runaway. Providers without streaming here keep `call_timeout_s`.

While a call streams, `llm.progress` (throttled to one every 30 s, with the tokens so far) feeds
the stall watchdog, so a long, progressing call is never taken for a hang, and the card can read
"pensando · 12k tokens".

### 5. Integration problems get more than one try

- A conflict when the base is merged into a story is resolved at the default on tier 2; if markers
  are left, once more on tier 1; then the founder, as before.
- A git failure the Ops Loompa does not recognise (merge, push, PR) gets one diagnosis by the
  Deployer at the default: the model reads the command, its error and `git status`, and picks one
  action from a closed list (retry, re-sync the story with its base, abort the merge and requeue
  the story, ask the founder). The code runs it through `agent.git` only (ADR-0008); at most two
  diagnoses per story, then the founder.

### 6. The judge checks that tests call the product the way a user does

The smoke run merged `converter -40 C` failing for a real user ("No such option: -4") because the
test called it with `--`. The judge's rubric now treats a test that reaches the product by a path
no user takes (an argument separator the spec never mentions, a mocked parser, a call around the
command line) as not proving the criterion.

## Consequences

- Planning and judging take longer (1–3 min per call at the default) and cost about US$0.01 each;
  the Worker, where 72% of the time went, should be roughly twice as fast. The net is expected to
  be faster per story at a similar cost — expected, not proven: Sprint 1 is the all-default
  baseline, and Sprint 2 measures (`loompa trace --stats`, `llm.reasoning_raised`, first-attempt
  pass rate per role). If `low` hurts a role, that role goes back to the default between sprints.
- A model that ignores `low` costs nothing extra: it simply thinks as at the default.
- OpenRouter checks the balance against the requested room; an account near its spending cap
  refuses calls a little earlier.

## Not decided here

- A loop detector on the streamed reasoning (only if Sprint 2 shows real loops).
- Which tier-2 models to keep; `loompa trace --stats` measures, the founder decides.
