# ADR-0013: CodeRabbit's PR review goes back to the Worker through a signed GitHub webhook

Date: 2026-09-29 · Status: accepted (Fase 6 item "Webhook do CodeRabbit"; builds on ADR-0008)

## Context

`quality.coderabbit.mode: cli` runs the CodeRabbit CLI inside the Inspector's gate, when it is
installed. The hosted CodeRabbit reviews pull requests instead, and the Deployer already opens one
per delivery (ADR-0008). Its review arrived on GitHub while the delivery waited in the founder's
inbox, so the founder read code a bot had already flagged, or approved it without knowing.

## Decisions

### 1. One endpoint per factory, signed or refused

`POST /api/factories/{slug}/webhooks/github` exists only with `coderabbit.enabled` and
`mode: webhook` (404 otherwise). Every call must carry GitHub's `X-Hub-Signature-256` over the raw
body, checked with the secret named by `webhook_secret_env` (default `GITHUB_WEBHOOK_SECRET`; the
value lives in the secrets files like any key, and `loompa doctor`/`setup` list it). An unsigned
or wrongly signed call is a 401; `ping` answers `pong`.

### 2. Only a CodeRabbit review that asks for changes acts

`pull_request_review/submitted` from `bot_login` (`coderabbitai[bot]`), on a PR whose head branch
or URL belongs to a story, with state `changes_requested` or a summary counting actionable comments
above zero. Everything else is ignored and answered with the reason. The inline comments come from
`gh api` when available; the summary alone is enough without it.

### 3. Back to `dev`, not through the founder's answer

`Scheduler.reopen_delivery` archives the pending delivery, appends the review to
`failure_history` (what the Worker reads on a retry), resets the tasks and injects the state at
the graph's pause node. It deliberately does not use `apply_founder_answer("changes")`: that would
record a bot's review as the founder's own guidance (`founder_notes`). The founder gets one INFO
note in plain pt-BR. The review is framed for the Worker as findings to verify, never as
instructions (web content is data).

### 4. A bot never loops a story

`max_rounds` (default 2) review→fix rounds per story. After that the review is kept in the
story's handoff and the pending delivery's impact tells the founder to look at the PR before
approving: the decision is theirs.

### 5. A second delivery updates the same PR

The story branch is rebased before each delivery, so the Deployer now pushes it with
`--force-with-lease` (it is the Deployer's own branch), and keeps the PR URL it already had when
`gh pr create` refuses a second PR for the same branch.

## Consequences

- Verified with signed payloads against a real dry-run delivery (sent back, re-run, delivered
  again; wrong signature, clean review, other reviewers and the round limit). **Not verified
  against GitHub itself**: that needs a public URL for the dashboard and a repo with CodeRabbit.
- The dashboard binds to localhost; exposing the endpoint (a tunnel, a reverse proxy) is the
  founder's setup, and the signature is what makes that safe.
