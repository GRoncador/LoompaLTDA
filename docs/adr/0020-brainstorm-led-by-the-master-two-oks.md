# ADR-0020: The brainstorm is led by the Master, consults roles, and closes with two OKs

Date: 2026-10-01 · Status: accepted (Plano set/2026 · item 5: Fase 10.4; amends ADR-0010 §3, whose
brainstorm was the Analyst alone with the Product Owner admitting ideas)

## Context

ADR-0010 gave the brainstorm to the Analyst: it proposed cards in a chat, and the Product Owner
admitted or held them when the founder sent them. Three things did not match what the founder
wants from a brainstorm (plan 10.4):

- **One voice.** Every idea went through the Analyst, whose angle is research. An idea that changes
  the architecture never heard from the Architect; one that needs no research still paid for an
  agent shaped for it.
- **Cards too early.** The Analyst drafted cards turn by turn, so the founder discussed cards
  before agreeing on what the idea was.
- **One OK.** The founder's single click sent ideas to the Product Owner, who wrote them straight
  into the backlog. The founder never saw how the idea would be cut into cards before it was done.

## Decisions

### 1. The Master leads; roles are consulted, not chained

`MasterAgent.brainstorm` runs every founder turn (`Conversations.agent_for` returns the Master for
both meetings and brainstorms). It keeps two things in the draft: the **direction** (one paragraph:
what the idea is, for whom, what is in and out) and the **ideas** (the pieces of the direction, the
same `add`/`update`/`drop` ops and `DraftItem`s as before, never in a sprint). It reads the
repository but has no web search, and its prompt tells it to consult the Analyst instead of stating
outside facts. The reply's URLs survive only if the founder wrote them or a consulted role's web tool
returned them.

The Master asks for opinions in its answer (`consult: [{role, question}]`); code runs at most two per
turn, one per role, and only roles in the roster (`agents/brainstorm.py::CONSULTANTS`):

| role | weighs | tools |
| --- | --- | --- |
| analyst | outside knowledge: market, competitors, prices, standards, external data | repository + web (Tavily via MCP, when configured) |
| architect | the architecture and the code as they are: what the idea touches, risk, size, recorded technical decisions | repository |

A role joins the brainstorm when the factory has it: a legal or UX role is one more row. This is not a
pipeline: nobody is consulted by default, and the same role can be asked again later.

### 2. Opinions are preliminary and checked in code

A consulted role answers `{summary, attention, cost, benefit, counterpoints, sources}`. The prompt
forbids accepting or rejecting the idea and asks for counterpoints that name a contradicted decision
(constitution, ADRs, specs, which come in through `precedents`). In code: a URL counts only if a web
tool of that consultation returned it, a repository path only if it exists, and the dropped ones are
counted under `attention`; repository paths stay in the opinion for the models and are not shown to
the founder; a role that fails leaves a failed opinion (`failed=true`), never an invented one; the web
limitation is declared by code (`conv.limits`), as before. Each opinion is an `Opinion` (`O1`, `O2`…)
in `draft.consults` and a turn of that role in the transcript.

The founder can ask a role directly (`POST …/consult`, the panel's "Pedir parecer" with the text box as
the question, `/consultar`), and set an opinion aside or bring it back (`dismiss`/`restore` ops): the
Master and the Product Owner then read it as set aside.

### 3. Two OKs

- **First OK — the direction** (`POST …/approve`, "✅ Aprovar o rumo", `/aprovar`). The Master marks
  `ready` when it thinks the direction is clear, but approving is the founder's act. The approval
  records the direction and the ideas it covered, and the Product Owner proposes the split
  (`propose_split`): which cards, in what order, what each card comes from (`ideas`), which waiting
  card receives an addition instead of a new card (`into`), and which ideas stay out (`held`, with a
  reason).
- **Second OK — the split** (`commit`, "✅ Gravar no backlog", `/backlog`). The founder may change a
  card's priority or take a card out first (`edit(..., split=True)`; text is not edited here — going
  back to the conversation is the way). `apply_split` files new cards (`origin="brainstorm"`, the kind
  the Product Owner gave, `extra.brainstorm` with the conversation, the approved direction and the
  ideas) and adds to waiting cards through the new `Backlog.amend`. Then the backlog is re-ranked, as
  every planning session ends (ADR-0017).

Talking again, asking for an opinion or changing the ideas after the first OK takes it back: the
approval and the split are dropped and the conversation continues ("↩ Voltar à conversa" does the same
on purpose). Ideas the split did not use — held by the Product Owner, or whose card the founder took
out — stay in the session, which stays open for another round, as held ideas did before.

### 4. The split is checked in code

`_split` turns the Product Owner's answer into cards it can stand for: `into` must be a card waiting
in the backlog (an addition never reaches work in progress; a card `into` something already running
becomes a new card with a note), a title that matches an open card points at it, a hold without a
reason is not a hold, and an idea the answer forgot becomes its own card with a note instead of
vanishing. With the model unavailable, each idea becomes one card as written and the approval says
`reviewed=false`.

### 5. `Backlog.amend`

The Product Owner may add text to a card that is still waiting: it is appended to the description as
"Acrescentado (brainstorm C-00X): …" and recorded in `state.extra.amended`. A card that left the
backlog keeps the spec it started from. ADR-0010's rule still holds for every other path: a session
cannot rewrite an existing card's text.

## Alternatives considered

- **Every role answers every turn.** Simpler to explain, but it is the "fixed pipeline with everyone"
  the founder ruled out, and it multiplies cost and latency per message.
- **A synthesis call after the opinions.** The Master could summarise the opinions in the same turn;
  it doubles the calls per consulted turn. The founder reads the opinions directly, and the Master
  weighs them in its next answer.
- **The split edited as text by the founder.** It would make the split a second conversation. Going
  back to the conversation and approving again keeps one place where the text changes.
- **Keep `admit_ideas` as the second OK.** The Product Owner's verdict would arrive after the founder's
  last click: exactly the single OK the founder asked to replace.

## Consequences

- A brainstorm turn can take longer: the Master's call plus up to two consultations (the Analyst may
  use up to eight tool rounds). The panel says who is thinking.
- `admit_ideas`, `IDEAS_SYSTEM` and the Analyst's chat prompt are gone; `AnalystAgent.converse` no
  longer exists. Research stories (`kind=research`) are unchanged.
- Events: `brainstorm.consulted`, `brainstorm.approved`, `brainstorm.split`, `brainstorm.filed`,
  `brainstorm.reopened`, `backlog.amended`.
- Not verified against a real model: the Master's brainstorm prompt, the consultation prompt and the
  split prompt. `contas` Sprint 2 has a brainstorm planned (bank statement formats), and
  `test_live_brainstorm_conversation` covers the whole path with a real provider.
