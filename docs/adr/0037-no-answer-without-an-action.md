# ADR-0037: No answer without an action, no loop without a breaker

Date: 2026-10-10 · Status: accepted

## Context

On 10/10 Gustavo could not finish tamagotchi's S-094, the card the line watch (ADR-0036 §4)
opened to make the base green again. It blocked eight times in 24 hours. Each time the founder
chose the recommended option: four times "encerrar como já resolvida", twice "encerrar sem
nenhuma alteração". Each answer led back to the same task.

The chain, read in `state.db`, the traces and git:

1. S-077 wrote a test that reads `.loompa/decisions/…`. The merge leaves the factory's records
   out, so the base went red (CA1, CA2).
2. S-092 was already fixing exactly those failures in the sprint. The line watch still opened
   S-094, because it only checks whether its own card is open. S-092 merged at 18:31 and turned
   CA1/CA2 green. From then on S-094 had nothing to do.
3. S-094's spec turned the forbidden fix into criteria: create the two documents under
   `.loompa/decisions/` and change no test. The plan gate saw the violation twice and let the
   plan through on the second round.
4. The loop:
   - The Worker stopped on task 1 and asked *before* writing, so the fence never refused a
     record and ADR-0036 §6 never triggered.
   - Every option of a question was read as guidance for `Architect.amend`, which can only add
     files and tasks. The result was `plan.amended files=[]` seven times.
   - The Worker went back to the first open task.
   - The repeat detector compared the model's sentences, which change every time, so the ways out
     never reached the founder.
5. The base is still red on another test of the same kind (AC6, from S-087). It reads
   `.loompa/specs/S-077/plan.md`, a file the story edited in its worktree and the merge dropped.
   The Inspector had passed it.

The sprint around it:

- **Cost:** US$ 5.27. Seven of nine stories were re-planned from scratch after one task stopped
  on a loop guard; US$ 1.59 and ~9.4 h of model time spent before those escalations were
  discarded.
- **Founder questions:** 15 of 21 were about the factory's records.

Each section below is one decision. The code comments cite these sections.

## §1 Every option of a question is an action the engine carries out

**What happened.** The founder's choice had no path in code. "Already resolved" became guidance
for an Architect who could not withdraw a task or close a story.

**Decision.**

- **The options are actions.** Each option of a team question carries one of the actions in
  `comms/answers.py`, and its key says which:
  - `guide` (`opt1`, `opt2`, …): the label goes to the team as guidance, as before;
  - `respec`: the spec is written again with the guidance, then planned and built from a clean
    branch (`_back_to_spec`);
  - `resolved`: the story goes to the new `resolve` phase. For a line card, that phase runs the
    failures the card was opened for on the base. If any still fails, the founder hears it.
    Otherwise the story is cancelled with the outcome `resolved`, and the sprint report counts
    it apart from missing work;
  - `skip` and `drop`: as before.
- **Who chooses the action.** The Master's rewrite (`EXEC_SYSTEM`) says the action of each
  option. When it does not, the label's own words decide; otherwise the option is guidance. The
  founder reads under each non-guidance option what the factory will do.
- **The ways out are always there.** "Deixar para depois" and "Cancelar" join every team
  question that does not already offer them.

**Not measured.** The `EXEC_SYSTEM` change is a contract change (one new field), not a rewording.
It went in without a bench run, so it is on the plan's list of prompts to measure.

## §2 A loop is measured where the story stands, not in the model's words

**What happened.** The repeat detector of ADR-0036 §6 fingerprints a question by its cause in code
only when the fence refused a file. S-094's Worker asked before writing anything, so the
fingerprint fell back to its sentence, which was new every time. The founder was never told the
story was going round, and never offered to stop it.

**Decision.**

- **Where the story stands.** `_progress` reads the story's position in code terms: the phase,
  the task the Worker would do next and its text, the fence, the criteria, and how many tasks
  and commits it has.
- **Counting.** Each question counts how many times in a row it was asked from that same point
  (`stalled_asks`).
- **The second question there** opens with a plain sentence saying the team stopped at the same
  point again. It keeps the team's options, which now include the ways out (§1).
- **The third question there** is not asked. The node marks the story `park` and sends one INFO
  note ("voltou ao backlog"). The Scheduler, once the run has stopped, puts the story back in the
  backlog the way `withdraw_story` does. A park that a stopped engine left behind is done at the
  next start.
- **The counter resets** when the story moves: a task done, a commit, a new fence or new criteria.

## §3 An amendment can withdraw a step

**What happened.** `Architect.amend` could only widen a plan: new paths, new tasks after the old
ones. When the founder's answer made a step pointless, the step stayed open, and the Worker always
starts at the first open task. In S-094, T1 to T3 stayed open behind six amendments, two of them
adding tasks that "discard T1 to T3".

**Decision.**

- **Withdrawing a step.** `AMEND_SYSTEM` answers `drop_tasks`: the open tasks the guidance
  withdraws. The code checks that each one is open, then marks it in the checklist as withdrawn
  (`[x] … — (retirada pelo ajuste do plano)`). It counts the task as done and records it in
  `tasks_withdrawn`.
- **Nothing left to build.** When an amendment withdraws every step left and the branch has no
  commit, the story goes to `resolve` (§1) instead of running a Worker with nothing to do.

**Not measured.** The new `drop_tasks` field is a contract change of `AMEND_SYSTEM`, on the list
of prompts to measure.

## §4 The line follows its failures, not its card

**What happened.** The line watch (ADR-0036 §4) opened S-094 for CA1/CA2 while S-092, in the same
sprint, was fixing exactly those. S-092 merged and the failures left the base. The line state
kept S-094 as its card, now pointing at a different failure (AC6) that the card did not mention.
S-094 went on with nothing to fix. Nothing told the spec writer that the failing test read the
factory's records.

**Decision.**

- **The card keeps its failures.** It stores the failures it was opened for (`targets` in the
  line state).
- **It closes when they leave the base.** When none of its targets fails any more, the card is
  resolved (§1), with the stories merged since in `by`. How:
  - a card waiting in the backlog or for the founder closes at once; its pending question is
    archived and its graph is told the way an answer is;
  - a card in the pipeline is recorded under `line_resolved`, and its next node runs `resolve`
    instead (a state written from the watch would be overwritten by the node running it).

  The founder gets one note ("O conserto da versão principal não é mais necessário"). Failures
  still red get a card of their own in the same check.
- **One fixer per failure.** Before opening a card, the watch looks for an open story that
  already names one of the failing tests word for word (names shorter than 12 characters, like
  "CA1", say nothing). If it finds one, it adopts it: pinned, joining the running sprint when it
  was waiting in the backlog.
- **Tests that read the records.** When a failing test lives in a test file that reads `.loompa/`,
  the card says so and says what fixing it means: rewrite the test or remove it, never create or
  edit the factory's records.

## §5 The factory's records are refused at every step, in code

**What happened.** ADR-0036 §5 and §6 refused, in the plan, the tasks that write or test
`.loompa/`, and turned a refused write into the plain ways out. The class kept leaking through
the steps around them:

- **the Inspector.** It ran the suite in a worktree that still held the story's uncommitted edits
  to `.loompa/`. S-087's test read `.loompa/specs/S-077/plan.md`, which it had edited. It passed
  there and failed on the base once the merge dropped the edit (AC6).
- **the spec.** Nothing looked at the criteria. S-094's ten criteria asked to create two
  documents under `.loompa/decisions/` and to change no test.
- **the plan gate.** On its second round it let a plan that still wrote the records go to the
  Worker, who could only ask the founder.
- **the fence.** `.loompa/specs/<id>/` was in every story's fence, though the worktree has no such
  folder. Workers made 248 calls under `.loompa`/`specs` in SP-013, and 53 failed.

**Decision.**

- **The Inspector judges what will land.** Before the Inspector runs (at `test`, and before the
  merge queue's checks), the Deployer puts the worktree's `.loompa/` back as the branch has it
  (`reset_factory_records`). Ignored files, such as the factory's databases, stay.
- **The spec check.** A criterion that names a record (`.loompa/decisions/`, `.loompa/specs/`,
  the lessons, the constitution) sends the spec back once, with the rule and the criteria to
  rewrite. Other `.loompa/` paths do not count: "the server never serves `.loompa/config.yaml`" is
  a product criterion. If every criterion still names one after the rewrite, the story goes to
  `resolve`: there is nothing for the product.
- **The plan gate.** A plan that still writes or tests the records after its re-plan sends the
  spec back once (the criteria likely ask for it). If the plan still carries them after that,
  those tasks are withdrawn (§3). When nothing is left, the story goes to `resolve`.
- **The fence** no longer holds `.loompa/` paths.
- **Diff hygiene** blocks a test line that opens a file under `.loompa` (`reads_records`). Merely
  mentioning the folder does not count, so a server test that fetches `/.loompa/config.yaml` is
  fine.

## §6 A task that cannot finish climbs alone

**What happened.** The escalation ladder (ADR-0016 §5) counts failures per story. At the second
failure on tier 2, the story went to tier 1 and was re-planned from a clean branch: the attempt
was kept under `refs/loompa/attempts/`, and its tasks were redone. In SP-013, 7 of 9 stories
went this way. In most of them the second failure was one task that stopped on the loop guard or
the call limit, with the tasks before it done and green. S-069 had done 3 tasks and redid 10
after the re-plan. The work discarded before those escalations cost US$ 1.59 (30% of the sprint)
and ~9.4 h of model time.

**Decision.**

- **One task stopped.** When the failure is a task the Worker could not finish (`unfinished`), the
  plan is not the suspect. The story goes to tier 1 on the same branch, with its tasks done and
  its commits. The Worker Sr starts at the task that stopped. `story.escalated` carries
  `task_level`.
- **The Inspector's FAIL** is still the evidence that the plan may be wrong (the fence keeping the
  Worker away from the cause, `contas` S-005). It keeps the re-plan from a clean branch once per
  story.
- **The counts do not change.** Two tries on tier 2, then tier 1, then the founder.

**Measure.** The next sprint's report shows how many escalations re-planned, and the cost before
and after each one. The saving can be at most what SP-013 discarded; if task-level escalations
end in persistent failures more often than re-plans did, this is reverted.

## §7 The Worker runs the tests that cover a file instead of reading them

**What happened.** In SP-013 the Worker read `tests/` 493 times (742k tokens) against 249 reads of
the product (327k), and made 214 edits to tests against 56 to the product. The product's suite is
5.3× its code (25,046 lines against 4,731; `casca.test.js` alone has 4,631), and 23 test files
compare the text of `index.html` or `sw.js`. Most of the loops that §6 now escalates without a
re-plan were a task reading test file after test file without writing (S-070 T4: about 20 files;
S-088 T1: 30). Step 1 of the Worker's prompt asked it to read "the tests that cover" the file it
changes, and `src/casca.js` is named by 22 test files.

**Decision (10/10, at the founder's request, without a bench run).**

- **A new tool.** `tests_covering(path)` lists the test files that name a file, at no model cost:
  its path or file name, an import of its stem, or its dotted module for Python. They come most
  mentions first. It is offered to the Worker only, so the planning roles' tool lists stay as the
  bench measured them, and the loop guard counts it as exploring.
- **The prompt change.** Step 1 of the Worker's prompt reads the file it changes, lists the
  covering tests with the tool, runs them with `run_tests`, and reads only the ones that fail or
  that the task changes. The live A/B's edits on this prompt still fit, so both arms get the same
  step.
- **In tamagotchi,** the founder asked for a one-time diet of the suite. The Product Owner found
  it too big for one story. A brainstorm split it into S-101 to S-105 (epic "Enxugamento da suíte
  de testes"), which wait in the backlog for a Sprint Meeting:
  - S-101: the AC6 first, the test that keeps the base red;
  - S-102: an inventory, as research;
  - S-103 to S-105: replacing, cleaning up and merging tests, which depend on the inventory.

**Measure.** In the next sprints: `read_file` calls on test files per task, tasks ending on the
loop guard, and the Worker's cost per delivery against SP-013. If they do not drop, the old step 1
comes back.

## §8 Findings that are never cards

**What happened.** In SP-013 the Kaizen loop filed 15 cards; Kaizen stories are 49 of the
factory's 101. Three of them show the noise:

- **S-098** came from the learning "Plano seguiu com ressalvas do Product Owner", a remark about
  how S-094's planning went. As a product card, it asked to "criar os dois documentos em
  `.loompa/decisions/`".
- **S-091**, "A suíte de verificações já falha na versão principal", waited in the backlog after
  the base it described was fixed.
- **S-100** was titled "[Oportunidade] Oportunidade] …": the triage's rewrite lost the `[` of its
  label, and the label cleaner only knew `[Label]`.

**Decision.**

- **Reservations are process notes.** The learnings "Spec/Plano seguiu com ressalvas do Product
  Owner" are marked `process`. Such learnings, and findings whose title asks for one of the
  factory's records, are recorded in `learnings.md` and never become cards. They do not go to the
  triage either, so no model call is paid for them.
- **Stale base-red findings close.** When the base goes green, the line watch closes, as resolved
  (§1), the Kaizen cards titled `BASE_RED_TITLE` that wait in the backlog.
- **The label cleaner** also strips a known label that lost its `[`.

## §9 One proposed decision per story

**What happened.** Each plan with an `adr_proposal` wrote a new numbered file. S-094's two plans
left `0004-s-094-…` and `0005-s-094-…` under `.loompa/decisions/`. They took the numbers the
failing test expected for S-077's records, and the Architect's next plan listed both as
"precedentes recuperados da memória".

**Decision.** A story's proposal is one file. `write_adr(…, story_id=…)` rewrites the story's
record while it is still `proposed`, keeping its number. An accepted record is never rewritten.
