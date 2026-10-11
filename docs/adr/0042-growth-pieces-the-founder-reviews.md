# ADR-0042: Growth, first rung — pieces the founder reviews

Date: 2026-10-11 · Status: accepted

## Context

The plan's Phase 17 is the first rung of growth: pieces the founder reviews. It depends on two
earlier phases:

- Phase 13 for positioning, the ideal customer and the brand's voice;
- Phase 16 for the product's kind and phase.

The plan sets a hard rule: **nothing is sent, published or paid**. Everything goes to the founder
for review. Measuring (rung 2, analytics read-only) and acting (rung 3, sending and advertising)
stay under "Depois": each needs a new decision, with LGPD and the platforms' rules in view.

## Decision

- **Commercial products only** (`factory_kind.profile(...).growth`). The panel's "📣 Growth"
  button appears only on them.
- **17.1, the landing page, is a code story.** It enters through `Conversations.quick_story`, so
  the Product Owner triages it (ADR-0017). Being a screen, it goes through the design gate
  (ADR-0039) like any other.
- **17.2-17.4, the pieces** (`loompa/growth.py`, `agents/storyteller.py`, `GrowthWriter`). Three
  prompts, product-agnostic, each forbidding invented features, numbers, prices, testimonials and
  customers:
  - **plan** (17.3): the marketing and acquisition plan. Its sections are audience and message,
    channels, messages per channel, measurable goals, the first four weeks, and risks (LGPD
    consent, platform rules, no unsolicited messages).
  - **copy and emails** (17.2): landing and store texts, or an e-mail sequence with subject,
    preview, body and day. The prompt says that marketing e-mail goes only to people who opted in.
  - **posts** (17.4): one text per network, and an art brief the Designer turns into the
    network's format. The art uses the final slot, with the brand as reference (ADR-0040). The
    formats are Instagram feed 1080×1080, Instagram stories 1080×1920, Facebook 1200×630,
    LinkedIn 1200×627, X 1600×900 and a square ad 1080×1080.
- **One decision per piece.** Each piece lives in `.loompa/design/growth/G-0XX/`, with its
  manifest and files, and goes to the founder as one decision:
  - **approve**: the pieces become files to download. An approved plan becomes
    `company/marketing.md`, read by the Storyteller and the PM.
  - **adjust**: a new round with the note. The models run in a task beside the reply
    (`Scheduler` keeps it in `SIDE_TASKS`), so the answer itself stays pure state.
  - **discard**.
- **There is no code path that sends, publishes or pays.** No network, e-mail or ads API is
  configured or called anywhere in the factory.

## Consequences

- The Storyteller's growth work costs one text call per piece. Posts also cost about US$ 0.08 a
  network in pictures (two drafts and a final), counted in the period's budget.
- The marketing plan is now a company document, so every later prompt that reads the company sees
  the channels and goals the founder approved.

## Not done, on purpose

- Rungs 2 and 3: analytics through MCP, sending, publishing and ads.
- Video.
- A schedule of posts.
- A bench run for the three new prompts. It comes on the first real calls, per the plan's
  reminders.
