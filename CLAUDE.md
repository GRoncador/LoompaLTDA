# Loompa LTDA — notes for AI collaborators

- Package: `loompa-core` (`src/loompa`), Python ≥ 3.11, managed with `uv`. Run `uv sync --dev`,
  `uv run pytest -q`, `uv run ruff check .` before committing.
- Frontend: `dashboard-ui/` (Vite + React + Tailwind + Phaser). `npm run build` writes the bundle
  into `src/loompa/dashboard/static/` — commit the bundle when the UI changes.
- Orchestration is LangGraph (`engine/langgraph_engine.py`, ADR-0005); node logic stays in `engine/graph.py`.
- Authority is enforced in code (ADR-0008): stories are created/ranked/admitted only through `ProductOwnerAgent`
  (`loompa/backlog.py`), git merge/push/PR only through the Deployer's `agent.git`, and BACKLOG cards run only
  after a sprint start (`loompa sprint start`, `meeting --run`) — don't call `store.upsert_story` or the raw `git()`.
- Architecture decisions live in `docs/adr/`. Read them before changing the engine, LLM layer,
  memory or dashboard stack. The product brief is `PROJECT_BRIEF_OOMPA_LOOMPA_LTDA.md`.
- Founder-facing text (inbox, reports) is Portuguese (pt-BR) and must pass
  `loompa.comms.audit_executive_text` — no stack traces, paths or error names. Agent prompts are English.
- Everything must work with zero API keys via the dry-run provider (`--dry-run`); tests use
  `MockProvider` scripts, never the network.
- Commit style: semantic (`feat:`, `fix:`, `docs:`, `chore:`, `test:`), work on `dev_fable`.
