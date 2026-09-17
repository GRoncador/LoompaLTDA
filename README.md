# Loompa LTDA — `loompa-core`

An autonomous, multi-agent **software factory** governed asynchronously by a single Founder.

The Founder holds one short morning meeting, the factory works all day in isolated Git
worktrees, and every question, blocker or delivery lands in an **executive inbox** written in
plain business language. No babysitting, no serial blocking, no terminal noise.

```
[ Founder Terminal / Hub ]
            │
   ┌────────┴────────┐
   ▼                 ▼
Factory A         Factory B
(brownfield)      (greenfield)
 .loompa/          .loompa/
```

## Quick start

```bash
uv tool install loompa-core        # or: pip install loompa-core
cd my-project
loompa init                        # detects greenfield vs brownfield, calibrates the factory
loompa meeting "Ship password reset; refactor billing webhooks"
loompa run                         # continuous batch execution (Ctrl-C safe, resumable)
loompa dashboard                   # http://localhost:8765
```

See `PROJECT_BRIEF_OOMPA_LOOMPA_LTDA.md` for the full vision and `docs/adr/` for the
architecture decisions taken while building it.

## Layout

| Path | Purpose |
| --- | --- |
| `src/loompa/cli` | Typer CLI (`loompa init/meeting/run/inbox/status/dashboard/factories`) |
| `src/loompa/config` | Pydantic schemas + defaults for `.loompa/config.yaml` and the model matrix |
| `src/loompa/onboarding` | Greenfield initializer and Brownfield scanner |
| `src/loompa/comms` | Executive (non-technical) message format and jargon filter |
| `src/loompa/engine` | Async story state machine, scheduler and SQLite checkpoints |
| `src/loompa/worktrees` | Git worktree isolation |
| `src/loompa/memory` | Hybrid memory: lexical/AST search + local vector RAG |
| `src/loompa/aci` | Agent-Computer Interface: filtered file/test/lint tools |
| `src/loompa/finance` | Token/cost tracker, pricing table, budget alerts |
| `src/loompa/llm` | Provider adapters, model matrix routing, escalation |
| `src/loompa/agents` | Master, Product, Architect, Worker, Inspector, Deployer, Finance, Kaizen |
| `src/loompa/speckit` | `constitution.md` / `spec.md` / `plan.md` / `tasks.md` templates |
| `src/loompa/dashboard` | FastAPI backend (REST + WebSocket) serving the built UI |
| `dashboard-ui` | Vite + React + Tailwind + Phaser pixel-art office |

## Development

```bash
uv sync --all-extras --dev
uv run pytest
uv run ruff check .
```
