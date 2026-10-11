# ADR-0038: The backlog is groomed at every Sprint Meeting

Date: 2026-10-11 · Status: accepted

## Context

On 11/10 Gustavo had to ask by name for tamagotchi's S-098 to be retired. S-098 was a Kaizen card
asking to "criar os dois documentos em `.loompa/decisions/`". That is the one fix the factory can
never deliver, and the opposite of a newer card (S-104). The backlog then held 17 waiting cards:

- 9 of them filed by the factory;
- one with a doubled label;
- one already covered by work in progress;
- two that could be one.

The pieces existed, but nothing ran them on its own:

- `review_cards` (contas SP-004) retires or keeps old cards, only when the founder asks for it, and
  only with those two verdicts;
- `rerank` orders the backlog when a meeting is saved, from the session's notes alone.

Gustavo asked for the Product Owner to check, at every Sprint Meeting, which waiting cards are
outdated, which can be merged and which split, keeping the backlog clean and in an order that fits
where the project is and where it is going. The change to a card is confirmed by the Master when
the factory filed the card, and by the founder when the card came from the founder.

## Decision

- **When.** Before the Product Owner's sprint proposal in a meeting that plans a sprint, and only
  once per meeting (`draft.groomed`). Every sprint starts from that proposal, and it is the step
  where the founder already waits on the Product Owner. At the meeting's opening, the panel would
  wait minutes for the answer. A meeting about the running sprint does not groom.
- **What.** `ProductOwnerAgent.groom` (`GROOM_SYSTEM`) reads up to 30 waiting cards. The line's
  card and the cards already in the sprint draft are left out. It also reads the delivered
  stories, the constitution and the company documents, and it has read-only tools on main. For
  each card it chooses `keep`, `retire`, `merge` (`into` another waiting card) or `split` (2-4
  parts). Code checks the answer (`groom_proposals`):
  - only the cards it was given;
  - the first verdict on a card stands;
  - a merge only into a waiting card that is not itself leaving;
  - a split only with 2-4 titled parts.
- **Who confirms.** It depends on the card's origin (`FACTORY_ORIGINS`):
  - **a card the factory filed** (`kaizen`): the Master checks the change as a second pair of
    eyes (`confirm_grooming`, `CONFIRM_GROOMING_SYSTEM`). Confirmed, the change is written into
    the draft at once. Held, or when the Master could not answer, it goes to the founder: the
    cautious side is a question, never a lost card;
  - **a card from the founder** (a request, a brainstorm, an epic): the change waits in the draft
    as a proposal. The founder answers it with the panel's buttons, `/aceitar` and `/manter` in
    the CLI, or in the chat (the `groom` op).
- **Nothing changes before the meeting is saved** (ADR-0010). A change in the draft is made of
  edits the meeting already knows:
  - the card is retired;
  - a merge adds the card's content to the card that stays (`amend`, applied on commit);
  - a split adds its parts as new cards.

  `groom` with `accept: false`, or "manter" on a retired card, undoes the whole change, its parts
  and merged text included. On commit each card that left keeps why, in `outcome`: `retired`,
  `merged` (with `into`) or `split`.
- **The order.** The proposal no longer sees the cards that are leaving. `rerank` now also reads
  where the product is going (the constitution and the company documents), so the order follows
  the founder's goals and not only the session.

## Not measured

`GROOM_SYSTEM`, `CONFIRM_GROOMING_SYSTEM` and the `groom` op in the meeting's contract went in
without a bench run. The first meetings show whether the verdicts hold up: how many proposals the
founder accepts, and whether a retired card comes back.
