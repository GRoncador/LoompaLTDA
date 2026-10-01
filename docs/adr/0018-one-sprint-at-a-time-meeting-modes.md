# ADR-0018: One sprint at a time; a meeting adjusts the running sprint or assembles the next

Date: 2026-10-01 · Status: accepted (founder's request the same day, after item 3; amends
ADR-0008 §3 and ADR-0017 §2)

## Context

ADR-0008 let several sprints run at once and kept `promote`, a lane that ran one backlog card
outside any sprint. After the backlog's doors (ADR-0017) the founder asked for three things: fixes
found by the factory are prioritized by the Product Owner and run inside a sprint, like any card
(no "executar agora"); only one sprint runs at a time; and a Sprint Meeting called while a sprint
runs asks whether it is about the running sprint (adjust it) or the next one (pre-assemble it, not
recommended), the next one being started later by another meeting.

## Decisions

### 1. No lane outside a sprint

`Scheduler.promote`, `POST /stories/{id}/promote` and the kanban's "💡 executar agora" are gone. A
Kaizen fix waits in the backlog where the Product Owner's triage slotted it (ADR-0017 §1) until a
sprint takes it. The sprint proposal now tells the Product Owner that these fixes only run inside
a sprint and should be weighed like the founder's cards (a bug users would meet before polish;
debt the next cards would trip on before them). `loompa sprint start` without ids still leaves
findings out: that CLI sweep is unreviewed, and findings enter a sprint by a meeting, an inbox
decision or by id.

### 2. One running sprint

`SprintBoard.start` refuses while another sprint runs, and `MasterAgent.start_sprint` checks it
before admitting anything, so a refused start moves no card. The CLI gets the same refusal. There
is still at most one `open` sprint: the next one, assembled and waiting. A sprint ends `closed`
(every story terminal) or `cancelled` (new status, from a meeting).

A story sent back to the backlog ("deixar para depois" in the inbox, or taken out in a meeting)
now leaves the running sprint. Before, it stayed in the list in `BACKLOG`, which is not terminal,
so its sprint could never close; with one sprint at a time that would block every later sprint.
A cancelled sprint keeps its list as the record of what it held.

### 3. The meeting asks what it is about

With a sprint running, a meeting opens with the Master's briefing plus the question (in code,
after the model's words) and `conversation.mode` stays empty: chat, edits, proposals and commits
are refused until the founder chooses (`POST .../mode`, `/atual` or `/proxima` in the terminal).
The one-turn `loompa meeting` only fills the backlog, so it sets the mode itself.

- **`current` — adjust the running sprint.** The draft holds the sprint's cards as they are
  (`members`, each with its stage) and the Master runs a different prompt with the sprint laid out
  (stage, phase, what each story waits on, the inbox question, tasks, fix attempts). Edits: a member
  unchecked leaves the sprint and goes back to the backlog keeping its branch (the same path as
  "deixar para depois"); a backlog card or a new card checked joins it, after the Product Owner's
  review (the same proposal call, told to judge only what joins and what it costs the work in
  flight; it cannot take members out); `restart` starts a member over from scratch with a reason
  (the existing `sprint restart`, which is how "retomar o estado inicial" was read);
  `cancel_sprint` calls the whole sprint off and sends its unfinished stories back to the
  backlog. Alternatives, dependencies and blockers are conversation; a question a story asks the
  founder is answered in the inbox. Nothing changes until "Aplicar no sprint"; the backlog is then
  ranked again when cards came back to it.
- **Safety with the engine.** Taking out, restarting or cancelling a story in an active stage
  (spec to review) needs its runner stopped. The commit refuses while any process holds the engine
  lock; the dashboard pauses its own engine around the commit and resumes it. A `loompa run` in
  another terminal has to be stopped by hand.
- **`next` — the next sprint.** The same path as ADR-0017: draft, the Product Owner's proposal,
  the founder's approval. With a sprint running, "Começar Sprint" is not offered; "Salvar como
  próximo sprint" (`commit(plan_next=True)`, `/montar`) needs the same proposal and leaves the
  sprint `open` with its cards and goal. Nothing starts it automatically: when the running sprint
  closes, the inbox note says it is assembled, and the next meeting (with nothing running) opens
  with its cards in the draft and says it is waiting to be reviewed and started; the Product
  Owner reviews it and the founder starts it.

### 4. Panel

The kanban shows "próximo: SP-00X · N cards" next to the running sprint. The meeting modal shows
the choice, a panel for the running sprint (stage per card, check to keep, ↺ to restart, "Cancelar
o sprint", "Pedir a avaliação do Product Owner", "Aplicar no sprint"), and for the next sprint
"Salvar como próximo sprint" while one runs, or "Revisar o SP-00X com o Product Owner" and
"Iniciar SP-00X ▶" when one is assembled.

## Alternatives considered

- **Start the assembled sprint automatically when the running one closes.** Rejected by the
  founder: the next sprint also starts through a meeting, after a fresh review.
- **Let the Master pick the mode from the founder's first message.** A misread would edit the
  running sprint; an explicit choice is one click.
- **Cancel the stories of a cancelled sprint.** Work the founder asked for would vanish; back to
  the backlog lets the Product Owner rank it again.
- **Stop the engine from a CLI chat session.** It does not own another process's engine; refusing
  with the reason is safer.

## Consequences

- Tests that started a second sprint while the first ran now finish the first; the promote test
  became "a fix runs only inside a sprint".
- Unverified against a real model: the running-sprint prompt and the proposal's new rules. Sprint 2
  of `contas` is the first chance (a meeting during it can exercise the `current` mode).
