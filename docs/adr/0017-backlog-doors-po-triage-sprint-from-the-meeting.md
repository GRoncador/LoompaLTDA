# ADR-0017: The backlog's doors — the Product Owner reads every entry, a sprint starts from its proposal

Date: 2026-10-01 · Status: accepted (Plano set/2026 · item 3: 10.1, 10.2, 10.3, 10.5, 10.6, 11.2,
11.4; amends ADR-0008 §1/§3 and ADR-0010)

## Context

ADR-0008 made the Product Owner the only *writer* of the backlog, but not its *reader*. Mapping the
four ways work reaches the factory (Sprint Meeting, Brainstorm, the kanban's "Iniciar sprint" button
and its "Nova história rápida" field) showed two doors with no model call at all: the quick story
(`POST /stories`) and the Kaizen loop both called `add_item` directly, with nothing but a
normalized-title check against duplicates. The only content review (`_review_ideas`) ran when a
brainstorm closed. On the way out, "Iniciar sprint" dispatched the whole pending backlog in one
click, because `start_sprint(None)` falls back to "the founder's cards in priority order". The
founder asked for both doors to be guarded: no card is born unread by the Product Owner, and no
sprint starts outside a Sprint Meeting.

## Decisions

### 1. Triage at every direct entry (10.1, 10.6)

`ProductOwnerAgent.triage_request` (a quick story) and `triage_findings` (one call per Kaizen
capture, all of its findings together) read the request against the open backlog, the
constitution and, for the founder's requests, the decisions on record. The answer is a `Triage`
(`loompa/backlog.py`): admit or refuse with a reason code (`duplicate`, `contradicts`, `vague`,
`too_big`), the card *as the Product Owner would file it* (title, description, kind, epic) and a
slot (`after` one waiting card, or `top`). A refusal still carries the classified card, because the
founder has the last word.

- **Kaizen** has no refusal: findings are never lost (ADR-0008 §4). The Product Owner may rewrite a
  finding's wording, point it at an open card that covers the same problem *by meaning* (the old
  check compared normalized titles and file names only), and slot it. The `[Bug colateral]`-style
  label is kept by code. Still automatic and immediate, no founder in the loop.
- **Quick story, approved**: filed as the Product Owner wrote it. Whenever the card's text is not
  literally the founder's, everything the founder wrote becomes the card's `founder_notes`, so the
  spec review's "No Invention" gate still traces each criterion to the founder's own words.
- **Quick story, refused**: nothing is written. A conversation of the new kind `review` opens with
  the Product Owner explaining the reason. Each founder message is read again with the whole
  exchange; an approval is filed at once (through `Conversations.commit`). If the founder insists —
  at least one message after the refusal, so the card records *why* — `commit(force=True)` files it
  as the Product Owner classified it, with `forced` and the objection on the card, visible in the
  drawer. `too_big` offers to take the text to a Brainstorm.
- **Kind**: the kind the Product Owner gives a card at triage is the card's; intake keeps it and
  still decides complexity and any epic split. The founder sees the same kind on the board before
  and after admission.
- **Advisory when the model is down**, as every Product Owner review: the request goes in as
  written with `reviewed: false` on its record. A provider outage never loses a founder's card or a
  finding.

`state.extra["po_triage"]` keeps what the triage decided: `reviewed`, `kind`, `reason`, `original`,
`rewritten`, and `forced`/`objection` when the founder overrode a refusal.

### 2. A sprint starts from the Product Owner's proposal (10.2, 10.3)

- A Sprint Meeting opens with the Master's **briefing**: facts counted in code (running sprints,
  stories in progress, deliveries waiting for review oldest first, stories stuck on the founder,
  backlog size and findings, last week's deliveries) and worded by one `low`-effort call; a
  deterministic pt-BR text stands in when the model cannot (and in dry-run).
- The meeting draft starts with the cards already planned into the open sprint (inbox decisions
  put Kaizen cards there), so the meeting sees and can drop everything the sprint would dispatch;
  a card left out is removed from the open sprint when the sprint starts.
- The Master hands the sprint to the Product Owner (`"consult_po": true` in its turn, or the
  founder's "Pedir a proposta" button / `/proposta`). `propose_sprint` picks cards from the draft
  and the backlog, orders them 1–5 and notes how they relate (text only: dependency as data is
  item 10.7, a separate ADR). The proposal is applied as draft edits through `apply_ops`, so the
  founder can still change it. It cannot add or rewrite cards.
- **`commit(start_sprint=True)` refuses** without a proposal, and refuses a sprint card the
  proposal never saw (added after it). Founder adjustments to cards it saw are fine.
- The panel loses its one-click start: `POST /sprints/start` is gone and `/meeting` no longer takes
  `run`. `loompa sprint start`, `loompa meeting --run` and `MasterAgent.start_sprint` stay — the CLI,
  automation and the `live` tests need an unreviewed start. The `promote` lane on a Kaizen card
  stays too (ADR-0008 §3): it runs one card the Product Owner already triaged, never the backlog.

### 3. Order: slot the new card, rank only when planning closes, the founder's drag wins (10.5)

- A new card is slotted right after the card the triage named (`Backlog.place`). No other card
  changes place: numbers only grow where they must to keep the order (ties sort by creation), and
  an evenly spread renumbering happens only if the bottom runs out of room (999).
- A full ranking (`ProductOwnerAgent.rerank`) runs only when a Sprint Meeting or a Brainstorm
  commits, and only when two or more unpinned cards wait.
- A card the founder drags is **pinned** (`stories.priority_pinned`, a column migrated on open; the
  drag sends `dragged`). Pinned cards are fences: the ranking reorders only the cards between two of
  them, so a pinned card never moves and no card crosses it in either direction (the plan asked
  only that none passes ahead; stopping both is simpler to reason about and still lets the Product
  Owner order every segment). Cards a ranking leaves out keep their slot. The founder unpins with
  the 📌 on the card, or asks in the meeting (`"pinned": false` on an `update` op).

### 4. Kanban and drawer (10.6, 11.2) and ids (11.4)

- "🗓 Reunião de Sprint", "💡 Brainstorm" and the "💬 open conversations" menu move from the global
  header to the kanban header; "Iniciar sprint" is gone. The quick-story field shows that the
  Product Owner is reading, then the card as it was filed, or opens the review conversation.
- The story drawer's tabs follow the order Spec Kit writes them (spec, plan, tasks, diff, log; spec
  first). The duplicated `max-h-[50vh]` is gone: header, details and tabs stay put and only the
  tab's content scrolls, down to the bottom of the window, with a scrollbar that is always shown.
- Story, sprint and conversation ids come from the highest number used + 1, compared as integers,
  never from the row count (a deleted row made the count repeat an id).

## Alternatives considered

- **A ranking pass on every write** (the plan's first 10.5): Kaizen writes several cards per
  delivery, so several calls per story, each reading the whole backlog, priorities shifting on every
  write, and the founder's drag undone. Dropped by the founder on 30/09.
- **A sprint started by the proposal itself** (commit asks the Product Owner and starts): the
  founder would never see the proposal before dispatch, which is the point of 10.2.
- **Quick story refused into a Brainstorm**: a simple bug would need two approvals; the founder
  chose a direct conversation with the Product Owner.
- **Keeping the review conversation's draft editable from the panel**: the request changes through
  the conversation only, so what the Product Owner read is what gets filed.

## Consequences

- A quick story now costs one Product Owner call (seconds on the presets) and the kanban field
  waits for it; a Kaizen capture costs one call for all its findings; a meeting costs one briefing,
  one proposal and one closing ranking more than before.
- Tests that started a sprint from a meeting call `propose` first; dashboard tests start sprints
  the way the CLI does.
- Unverified against a real model: the four new prompts (triage of a request, triage of findings,
  proposal, ranking) and the briefing. `contas` Sprint 2 opens with this meeting and exercises all
  of them; the decisions here are meant to be judged there.
