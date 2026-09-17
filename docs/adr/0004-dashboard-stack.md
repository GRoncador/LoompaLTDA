# ADR-0004: Dashboard — FastAPI serving a prebuilt Vite/React bundle; Phaser office

**Status:** accepted · **Date:** 2026-09-17

## Context

The brief specifies FastAPI + WebSockets + SQLite backend and a Next.js/Vite + Tailwind +
shadcn/ui frontend with a Phaser.js pixel-art office. The package must remain
`pip install loompa-core` friendly.

## Decision

- **Vite + React + Tailwind** (no Next.js): a static SPA is trivial to embed in the wheel
  under `loompa/dashboard/static/`. Next.js needs a Node runtime at serve time; that breaks
  the single-command promise.
- **No shadcn/ui dependency**: a handful of hand-written Tailwind components keeps the build
  minimal and avoids a component generator in CI. Visual parity is easy to reach later.
- **Phaser 3** renders the 2D office from a JSON layout; agent states
  (`IDLE/WORKING/TESTING/BLOCKED`) are driven by the same WebSocket event stream the Kanban
  and Inbox use.
- The backend exposes REST for CRUD (factories, stories, inbox replies, meeting) and one
  WebSocket (`/ws`) that fans out engine events. The FastAPI app is importable and testable
  without the UI.

## Consequences

- `loompa dashboard` works right after `pip install`; `dashboard-ui/` is only needed to
  change the UI. A prebuilt bundle is committed on release.
