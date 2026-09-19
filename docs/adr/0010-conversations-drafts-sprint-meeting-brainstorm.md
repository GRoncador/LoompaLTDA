# ADR-0010: Conversations — chat sessions with a backlog/sprint draft, Sprint Meeting and Brainstorm

Date: 2026-09-19 · Status: accepted (implements phase 5 of `docs/PLANO-2026-09.md`; the Brainstorming row of ADR-0006 §2)

## Context

The morning meeting was one shot: the founder typed goals, the Master answered with a list of
stories, and they were in the backlog. Nothing let the founder say "drop the second one", "make
that urgent" or "start with these two". A brainstorm did not exist at all, although ADR-0006 gave
it to the Analyst with the Product Owner admitting the result. ADR-0008 made the Product Owner
the only writer of the backlog and put a sprint in front of dispatch, so a conversation has to
end in those two doors and cannot invent a third.

## Decisions

### 1. A session is a row of its own; the draft lives in it

`loompa/conversations.py`: a `Conversation` (`C-001`…, `meeting` | `brainstorm`, `open` |
`committed` | `discarded`) with its turns, a `Draft` (sprint goal + cards) and the limits its agent
declared. It is one JSON document in a new `conversations` table (created on open, like
`sprints`). Until the founder commits, the `stories` and `sprints` tables are not touched: a
draft card is not a backlog card, does not show on the kanban and is not admitted by anything.
Sessions survive the terminal and the browser (`chat resume C-001`, the dashboard's 💬 list).

### 2. The model proposes edits, code applies them

The Master and the Analyst answer `{reply, ops, questions}`. The ops are `add`, `update`, `drop`
and `goal`, and `apply_ops` validates each one: an unknown reference or operation is reported and
skipped (it never raises on model output), a repeated title refines the card already in the draft,
a title that matches an open card becomes a reference to that card (or is refused when the card is
already in progress), the text of an existing backlog card cannot be rewritten from a session
(only its priority and sprint membership), and a draft holds at most 40 cards. The founder's
checkboxes, priority selector and ✕ send the *same* ops (`POST .../draft`, `/incluir`, `/tirar`),
so the chat and the panel cannot disagree and a click costs no model call. The draft, not the
transcript, is the memory: each prompt carries the whole draft, the backlog and the last 16 turns.
`run_turn` records the founder's message before calling the model, so a failure never loses it;
a failed call answers in plain pt-BR, and the technical detail goes to a `conversation.error`
event for Ops. The older shape `{stories, clarifications}` is still accepted from small models.

### 3. Who leads, who decides

* **Sprint Meeting — Master.** `MasterAgent.converse` (repository read tools, four tool rounds at
  most, so a turn answers quickly). Commit (`commit_meeting`) sends every card to the backlog
  through `ProductOwnerAgent.add_item` and re-ranks existing cards through `set_priority`; with
  `start_sprint` it calls the existing `start_sprint` for the cards marked "in sprint" (the sprint
  goal is the session's). Every check runs before the first write, ids are saved back into the
  draft as cards are created (a retry cannot duplicate), and a sprint is never started from an empty
  pick list, because `start_sprint([])` means "everything in the backlog".
* **Brainstorm — Analyst.** `AnalystAgent.converse` with repository and web tools (Tavily through
  MCP when configured). The prompt forbids inventing facts; code removes any URL in the reply that
  no web tool returned in that turn, and a missing web search is declared in `conv.limits`.
  Proposed cards are `origin="brainstorm"` and never in a sprint.
* **Admission — Product Owner.** `ProductOwnerAgent.admit_ideas` reviews the ideas (concrete,
  buildable, not covered by an open card, not against the constitution) and may hold one back with
  a reason and change priorities; it never rewrites text. Admitted ideas leave the draft as cards; held
  ones stay in it with the reason and the session stays open. A hold without a reason is not a hold,
  and an unavailable model admits what the founder picked (advisory, like the other reviews).

### 4. `loompa meeting` is a session of one turn

`MasterAgent.meeting(goals)` opens a `meeting` session, runs one turn, commits it to the backlog
(no sprint, as before) and returns the same `{stories, clarifications}`. The session stays in the
history. Questions the Master could not do without still go to the inbox, because no one is at
the keyboard. When the model is down, the goals are still split deterministically, so the command
keeps working with no keys. `--run` and the dashboard's `meeting` endpoint are unchanged.

### 5. Surfaces

* CLI: `loompa chat meeting|brainstorm [text]`, `chat resume`, `chat list`; slash commands
  (`/sprint`, `/backlog`, `/incluir`, `/excluir`, `/tirar`, `/meta`, `/rascunho`, `/descartar`,
  `/sair`). One `asyncio.run` for the whole session (the providers' HTTP clients are bound to the
  event loop); end of input keeps the session.
* API: `GET/POST /conversations`, `GET .../{id}`, `POST .../{id}/messages|draft|commit|discard`.
  One turn or commit at a time per conversation (an `asyncio.Lock` per session); domain errors are
  404/409. `overview.conversations` lists the open ones.
* Dashboard: the header's ☀️ Reunião and 💡 Brainstorm open a two-pane modal (transcript and
  editable draft), 💬 lists sessions to resume. "Começar Sprint" is only enabled with a card marked
  for the sprint.
* Events: `conversation.opened|turn|edited|committed|discarded|error`, `ideas.admitted`.

## Alternatives considered

* **Model returns the whole draft each turn.** Simpler for small models, but a dropped card is
  silent and the founder's edits would race the model's copy. Ops keep every change explicit and
  reportable.
* **Draft cards as `BACKLOG` stories with a `DRAFT` flag.** Every consumer of the stories table
  (Scheduler, kanban, Kaizen duplicate check, the AST authority test) would have to learn a state
  that means "not real yet". A separate table keeps ADR-0008 untouched.
* **A LangGraph graph per conversation.** A chat has no pipeline, checkpoints or interrupts: the
  session row is the whole state, and each turn is one call.
* **Streaming replies.** Deferred: the turn is short and the UI shows "pensando…".

## Consequences

* A session is a plain transcript plus a draft; nothing else in the factory depends on it, and
  deleting the table loses only unfinished conversations.
* Web results in a brainstorm are untrusted like in research (ADR-0009); what an injected page could
  do at worst is add a card to a draft the founder reviews, and the Product Owner still decides on
  admission.
* `LoompaAgent`s stay stateless: the session is loaded, edited and saved around each turn.
* Not verified against a real model: the prompts and the `{reply, ops, questions}` contract were
  exercised with scripted providers and the dry-run provider only. Two `live` tests
  (`test_live_sprint_meeting_conversation`, `test_live_brainstorm_conversation`) check the contract
  with Gemini; they need a key on the founder's machine.
* Not done: turning a brainstorm's admitted ideas straight into a Sprint Meeting (open one and pick
  the cards), streaming, and pruning old sessions.
