# ADR-0036: What tamagotchi's sprints 11 to 13 taught the factory

Date: 2026-10-09 · Status: accepted

## Context

On 09/10 Gustavo asked for an analysis of tamagotchi's last sprints, looking for faults and
opportunities in Loompa. The window was SP-011 (closed), SP-012 (closed) and SP-013 (paused,
3 founder stories plus 5 Kaizen cards). The data came from `state.db`, git and the product's test
suite.

The cost per delivery went from US$ 0.09 (SP-010) to 0.13 and then 0.41. The base ended red, with
3 failing tests, one of them a real offline bug. Most of the time and money did not go to the
work itself:

- **Infrastructure:**
  - a misbehaving upstream: 32 answers of 96k tokens, 5.5 h of model time;
  - an empty account: 256 refusals in two days that nobody was told about;
  - a Mac asleep: 7 h on 1% battery, then ~11 h with the lid closed.
- **Self-feeding processes:**
  - 12 of 18 stories were the factory's own findings;
  - lessons in the constitution taught the wrong things;
  - tests read the factory's own records.

Each section below is one decision. The code comments cite these sections.

## §1 A 402 means no credit, for the whole account

**What happened.** Xiaomi's account ran out of credit on 07/10 at 03:57. It answered 402 to 256
calls. The router treated the 402 like a rate limit: a 5-minute pause per model, retried forever.
All traffic went through OpenRouter (and into §2), and the founder never heard about it.

**Decision.**

- A 402 pauses every model of that provider for `models.billing_pause_s` (6 h).
- It emits `llm.billing` and sends one FINANCE note per provider (`ModelWatch.billing_refused`).
- The note's flag is cleared when a call through that provider answers again.
- Saving the settings tries the provider again at once.
- `loompa doctor` and `providers test` say "conta sem saldo" instead of "limite de uso".

## §2 Tool-call storms

**What happened.** One OpenRouter upstream of `xiaomi/mimo-v2.6-flash` (Makora) wrote 32
degenerate answers. Each asked for 2,500 to 5,300 tool calls, up to 96% of them repeats, written
for 5 to 18 minutes until the 96k ceiling. They held 63 thousand of the 67 thousand tool calls of
the window. The other seven upstreams of the same model never did this. Nothing stopped them:

- the loop detector read only text;
- `finish_reason` said `tool_calls`;
- `_tool_loop` ran every call.

**Decision.**

- **Detect it in the stream.** `tool_storm` flags more than 40 calls in one answer, or the same
  call 10 times. It is checked as each call ends, and raises `LoopDetected(storm=True)` with
  `served_by`.
- **Route around the upstream.** On a storm, the router tries the same model again with
  OpenRouter's `provider.ignore` set to that upstream's slug (the format was checked live), and
  leaves that upstream out for 6 h. The `llm.upstream_avoided` events keep it out across a
  restart. A second storm rests the model for that role and moves to the next candidate.
- **Founder's permanent choice.** `ProviderConfig.ignore_upstreams` holds upstreams the founder
  never wants. tamagotchi's config now has `makora`.
- **Cuts at the ceiling.** An answer that filled its output room and ended on anything other than
  `stop` counts as cut. An answer that says it stopped is believed, because some providers leave
  reasoning out of `max_tokens`.
- **Last line of defence.** `_tool_loop` runs each distinct call once, at most 20 per answer, and
  tells the model so.
- **Factory health.** The `llm.storm` finding is grouped by upstream.

## §3 A delivery never carries a combination nobody ran

**What happened.** S-070 was tested at 23:21 on a base without S-077. S-077 merged at 23:24. The
delivery's clean rebase at 23:25 handed the founder an untested combination. The approval then
merged it on the spot, because the branch already held the base. S-077's test pinned the exact
list of files to pre-cache, and S-070 had added one.

**Decision.**

- When the branch is behind the base, `node_review` brings the base in the way the merge queue
  does (`_bring_up_to_date`): merge, conflicts to the Worker, a new baseline, suite and smoke
  again.
- If that combination is red, the story goes back to `dev` (`story.reintegrating`,
  `after=delivery`).
- The Deployer delivers without rebasing. A base that moves after that is caught at approval
  (`_merge_now` sends the story to the queue).

## §4 Stop the line

**What happened.** Nobody watched the base itself. Five stories found its failures in their
baselines. Kaizen filed S-091, S-092, S-093 and 12 duplicates. The founder was asked about
"falhas vindas de outra história".

**Decision.** `engine/line.py` (`LineWatch`) runs the base's suite once, in the background,
whenever the base moved (from the factory's first merge on).

- **Green to red:**
  - one fix card goes to the top of the backlog, pinned, and into the running sprint;
  - the card lists the stories merged since the last green;
  - one INFO note goes to the founder.
- **Red to green:** an event and a note.
- **While the card is open:**
  - a story's baseline files no copy of it;
  - the Worker gets the list of tests already failing on the base, told not to fix them and not
    to stop for them.

## §5 The plan gate keeps the factory's records out of the product

**What happened.**

- S-077 had two tasks that wrote ADRs under `.loompa/decisions` and two tests that read them. The
  merge drops `.loompa/` from every branch, so the base went red.
- Five stories spent a Worker task each copying their checklist into `specs/S-0xx/tasks.md` in
  the product.

**Decision.** `plan_check.factory_records_in_tasks` turns these into plan problems:

- a task that writes under `.loompa/` (a decision goes in `adr_proposal`);
- a test that reads `.loompa/`;
- a copy of the records in the product (a path with `S-NNN`, or `spec`/`plan`/`tasks.md` under
  `specs/`).

A criterion about a record (decision, ADR, record) may be traced to `review:` even in a story that
changes code. The gate's creation verbs now include cria/grava/salva/gera.

The Architect's and Spec's system prompts were not changed. The bench rule is to measure prompt
changes first, and the gate's problems already reach the Architect as feedback on the plan.

## §6 A question with the same cause is a repeat

**What happened.** The founder was asked six times in 27 minutes to "allow the decisions folder"
on S-090. Each yes became a plan amendment the Architect never grants: it never opens `.loompa/`.
The repeat detector compared the model's sentence, which changed every time, and only covered
persistent failures.

**Decision.**

- **Writes to `.loompa/`.** A refused write there goes back to the Architect once, with the
  reason (`_drop_records_task`); `_widen_fence` no longer spends a call on those paths.
- **If the step is still there,** the founder gets a code-written message: back to the backlog,
  cancel, or retry without the step. It never offers to allow it.
- **Repeats.** `block(cause=...)` fingerprints a question by its cause in code terms (the files the
  fence refused). The same cause twice in a row gets the repeated-problem message.

## §7 A finding recommends the backlog, and a sprint has a cap for findings

**What happened.** A collateral bug used to recommend "Incluir neste sprint". One click on
"Aprovar" on S-077 put four findings into SP-013, which started with 3 stories and ran 8.

**Decision.**

- `card_decision` always recommends the backlog.
- `schedule.kaizen_sprint_cap` (default 2): the running-sprint option is offered only to the most
  urgent findings, while the sprint still has room for findings that came from deliveries.
- The founder's explicit choice still wins.
- The Product Owner's triage prompt keeps writing technical titles; the founder's view goes
  through §8.

## §8 Founder text from technical titles

**What happened.** Real inbox lines read like this:

- "entre um arquivo do projeto e um arquivo do projeto";
- "teste de algo que funcionava deixou de funcionar";
- "um arquivo do projeto ()";
- "para o um valor do código".

**Decision.**

- "regressão" becomes a noun: "volta de um defeito"; "teste de regressão" becomes "teste contra a
  volta de defeitos".
- `_tidy` runs after the replacements:
  - runs of placeholders become "dois/três/alguns …";
  - a doubled article goes;
  - empty parentheses go;
  - a file that opens a title goes.

## §9 What the constitution learns, and what an agent reads of it

**What happened.**

- The constitution grew three near-copies of "do not re-read a file" after storms. Those were
  lessons blaming the team for the service.
- Two lessons said to create `tasks.md` "in the checkout", one demanded a test per criterion
  against `review:`, and one asked to extend existing test files against the
  one-file-per-story rule.
- `grounding(3000)` cut the file plainly, so the Worker read the rules and one and a half lessons,
  never a new one.

**Decision.**

- **Refused lessons** (each becomes a `constitution.lesson_refused` event instead of a line):
  - a lesson about tool use (the LoopGuard enforces it in code);
  - a lesson about the factory's records;
  - a lesson about extending existing test files;
  - a lesson close to one already there (word Jaccard ≥ 0.22, calibrated on the real lessons:
    the re-read trio scored 0.24–0.36, unrelated pairs ≤ 0.17);
  - any lesson from a story with storm events.
- **What an agent reads:** `LoompaAgent.constitution` serves the rules whole, then the newest
  lessons that fit, saying how many old ones were left out.
- **tamagotchi:** the 7 refused lessons were removed by the same function (5,896 → 3,540
  characters). The file is in the product's working tree, uncommitted, like the lessons were.

## §10 The sprint report

**What happened.**

- SP-011 and SP-012 sent two "Sprint concluído" notes each.
- SP-013 started without a goal.
- SP-011's "10h02, +481%" held 7h20 of a Mac asleep.

**Decision.**

- **One report.** `Store.claim` (a conditional UPDATE) lets exactly one pass write the report. A
  claim older than 15 minutes is owed again.
- **A goal.** A sprint started without one gets "Entregar: <títulos>".
- **Asleep time apart.** The report shows the time the computer slept (`engine.slept`) and the
  active time, compared with the previous sprint. The code-written summary mentions sleep when it
  passed 10% of the duration, with plural agreement fixed and no ".." after the goal.
- **Battery.** `loompa run` warns when the Mac is on battery. `caffeinate -s` holds nothing there,
  and that policy is unchanged.

## §11 The prompt A/B, balanced by complexity

**What happened.** The Worker experiment gave p=0.01 against the variant. But the variant held 3
COMPLEX stories of 6 and the control 1 of 8. Split by complexity:

| Complexity | Variant (rework / settled) | Control (rework / settled) |
| --- | --- | --- |
| COMPLEX | 3/3 | 1/1 |
| SIMPLE | 1/1 | 1/5 |
| STANDARD | 2/2 | 0/2 |

**Decision.**

- `balanced_draw` gives a story to the arm that is behind its share within the story's
  complexity; a tie uses the old hash.
- The report adds `by_complexity` and `confounded` (when the arms' share of one complexity differs
  by more than 25 points), and `loompa prompts ab` prints both.

## Not done, on purpose

- **No system prompt was edited.**
  - §5 and §9 hold the rules in code.
  - The Worker's "Already failing on the base" block (§4) is new model-facing context. It was not
    benched; it can be measured with `loompa prompts compare --b-from`.
- **Mac sleep policy is unchanged.** `caffeinate -s` still releases on battery, as designed in
  12.3. The founder is told; nothing forces the Mac awake.
- **The product's red base is left to the factory.** On the next `loompa run`, the line watch (§4)
  checks it and files or points to one fix card.
