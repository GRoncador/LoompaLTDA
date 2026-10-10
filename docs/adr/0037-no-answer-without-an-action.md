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
