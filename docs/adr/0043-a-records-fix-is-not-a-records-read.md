# ADR-0043: A records fix is not a records read; questions carry measured facts

Date: 2026-10-11 · Status: accepted

## Context

On 11/10 Gustavo asked why tamagotchi's S-106 kept asking him things. S-106 was the line card
(ADR-0036 §4) for one failing test: AC6 of `tests/casca_body_scripts.test.js` read
`.loompa/specs/S-077/plan.md`. The fix touches one test file. It took 16:06 → 19:30, US$ 0.20,
and three founder questions, each caused by the answer to the one before.

It was not an endless loop: every question was a different point, and the stall detector
(ADR-0037 §2) counted one each time. It was a cascade of factory defects. Read in `state.db`,
the traces and the worktree:

1. **The records gate misread the fix.** `factory_records_in_tasks` flagged any task that named a
   `.loompa/` path and a test file as "has a test read". Every honest task of a story whose goal
   is to *remove* a records read names both. Four plan rounds, back to the spec, all tasks
   withdrawn.
2. **An empty plan closed as resolved.** With every task withdrawn and nothing built, the plan
   review sent the story to `resolve`. S-106, a line card, had failures to run, so the founder got
   question 1: "a história foi encerrada como já resolvida, mas…", although nobody had asked to
   close it. **S-101**, the same fix filed by the founder's brainstorm (epic "Enxugamento"), had
   no line failures to run and was **cancelled as resolved** over a red base at 17:08.
3. **The line opened a duplicate.** `_already_fixing` matched only the failing test's name word
   for word. S-101 ("Trocar o critério AC6 de casca_body_scripts…") was in the running sprint for
   that test, and the line opened S-106 anyway. Both ran at once.
4. **The workaround caused the next bug.** The founder had to tell the Architect not to write the
   literal path. The Architect wrote "exclude every hidden entry" instead, so the reproducer's
   copy of the repository lost `.git`, and a publishing test that runs `git remote -v` failed
   inside it.
5. **The self-check undid a needed change.** Task 2 fixed AC6 and also fixed the copy filter. That
   filter lives in the story's other planned file, and with it the suite had 753 tests green.
   The task text said "single file", and the self-check asked to revert the other file. The
   Worker had no way to bring back the committed version and asked the founder (question 2). The
   question called the change "three lines of comments" and recommended undoing it.
6. **The undo turned the suite red.** Task 3 rebuilt the committed file by hand, which took
   26 minutes and 32 calls. The suite went to 752 passed and 1 failed, which was question 3.
7. **The bugfix route asked for a reproducer the base already had.** The line card's failing test
   already reproduced the bug. "Reproducer first" added a test file that copies the repository
   and runs the whole suite again inside every `npm test`: more suite in a product whose suite is
   already 5.3× its code.

## Decision

1. **A clause that takes a records read out is not one.** `factory_records_in_tasks` collects
   `.loompa/` references clause by clause. It skips a clause that removes the read, excludes the
   folder, describes today's failure (ENOENT, "hoje falha") or checks that nothing reads it.
   The diff scan (`hygiene.scan_diff`, `reads_records`, blocking) still refuses a test that opens
   a record, so a miss here costs a hand-off later, never a red base. Tested with the real task
   texts of S-101 and S-106 (ten plans, none flagged now). S-077's real positive still fires.
2. **An empty plan asks; `resolve` says who thought it was over.** When the records gate
   withdraws every task and nothing is built, the story blocks with a QUESTION, `resume="plan"`,
   titled "precisa de um plano novo". The options are to re-plan, later or cancel. It never goes
   to `resolve`. When `resolve` finds failures still red, the message says whether the founder
   asked to close (`RESOLVE_KEY.founder`, set by the "resolved" answer) or the team concluded
   there was nothing left, and that the story stays open.
3. **The line adopts by label and file.** `_already_fixing(failing, holders)` also matches an
   open story that names the failing test's label in the same text as the file holding it. The
   label is node's `AC6`, or the function of a pytest id. The file can be named by its path or
   its stem (`casca_body_scripts`). `test_files_holding` finds those files and also feeds the
   records note in the card.
4. **The self-check keeps the story's other planned files, and questions carry measured facts.**
   - `WorkerAgent._keep_fenced_changes` drops a self-check item that asks to undo or revert, or
     calls something "outside the task", when every path it names is in the plan's fence and at
     least one is a file the task does not name (event `worker.dod_scope_kept`). "Restore the test
     you removed" in the task's own file still goes.
   - A Worker question passes the Master, as `executive` facts, every uncommitted file with its
     line count and whether the change runs or is comments only (`hygiene.unsaved_changes`).
     `EXEC_SYSTEM` already forbids contradicting those facts.
5. **`restore_file`** (Worker, a write tool inside the fence, ADR-0009 amended) brings one file back
   to the story's last commit (`to: "saved"`) or to the main branch's version (`to: "main"`).
   It restores exact bytes, and deletes the file when that version does not have it.
6. **A line card plans without a new reproducer.** For a bugfix that is the line card, the
   Architect gets `BASE_RED_NOTE` with the failing tests instead of `REPRODUCER_NOTE`. No
   reproducer task is prepended, and `REPRO_KEY` is `{"status": "base_red", "failing": […]}`.

## Consequences

- A story whose purpose is to stop a test reading records can be planned honestly, and a plan
  the gate empties never closes a story.
- The founder no longer reads "foi encerrada" about a story that is open, nor "comments only"
  about code.
- **Not benched:** `BASE_RED_NOTE` and the `restore_file` description are new model-facing text;
  they go to the bench reminders in `docs/PLANO-2026-10.md`. The self-check prompt is unchanged
  (the filter is code).
- The running dashboard keeps the old code until it restarts. S-107 (a publishing test that
  fails in a copy without `.git`) was born from the reverted file; with the founder's answer to
  question 3 (keep `.git` in the copy) it no longer applies, and the next meeting's grooming
  should retire it.
