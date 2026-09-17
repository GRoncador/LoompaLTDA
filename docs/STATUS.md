# Brief → implementation map

Status as of 2026-09-17 on branch `dev_fable`. ✅ built & tested · 🟡 partial · ⚪ not started

| Brief section | Status | Where |
| --- | --- | --- |
| §2 `loompa init` greenfield/brownfield, stack audit, draft constitution, onboarding report | ✅ | `onboarding/`, `factory.py` |
| §2 Multi-factory hub, per-factory `.loompa/` (SQLite state + vector memory + worktrees) | ✅ | `config/store.py`, `factory.py` |
| §3 Morning meeting (text) → stories; end-of-day batch review | ✅ | `agents/master.py`, `cli/ops.py`, dashboard |
| §3 Voice dictation (local Whisper) | 🟡 endpoint + UI recorder; needs `[voice]` extra | `dashboard/app.py` `/transcribe` |
| §3 Executive non-technical messages (context / impact / options) | ✅ deterministic filter + LLM rewrite + audit gate | `comms/executive.py` |
| §5 Master, Product, Architect, Worker, Inspector, Deployer, Finance, Kaizen | ✅ | `agents/` |
| §5 Compliance, Metrics, Storyteller (on demand) | ✅ one-shot `loompa ask` | `agents/support.py` |
| §6 Model matrix, tiers, fall-through, pricing, $10–30 budget alerts | ✅ | `config/defaults.yaml`, `llm/router.py`, `finance/` |
| §6 Escalation Tier 2 ×2 → Tier 1 → BLOCKED_AWAITING_INPUT | ✅ | `engine/graph.py::node_test` |
| §7 Kaizen: learnings.md, backlog cards, constitution lessons, daily summary | ✅ | `agents/kaizen.py`, `agents/architect.py` |
| §8A Hybrid memory (AST/lexical + local vector RAG) | ✅ | `memory/` |
| §8B CodeRabbit | 🟡 CLI hook when `quality.coderabbit.enabled` and the binary exists; no webhook receiver | `agents/inspector.py` |
| §8B pytest gate + BDD acceptance judge (PASS/FAIL) | ✅ | `agents/inspector.py` |
| §8C Async graph, non-blocking branches, SQLite checkpoints, interrupt/resume | ✅ (custom asyncio engine, see ADR-0001) | `engine/` |
| §8D Git worktree isolation | ✅ | `worktrees/` |
| §8E Spec Kit triad | ✅ | `speckit/` |
| §8F ACI (paginated reads, patches, compacted logs) | ✅ | `aci/` |
| §9 Dashboard: Phaser office, inbox, kanban, multi-factory, WebSockets, agent drawer | ✅ | `dashboard/`, `dashboard-ui/` |
| §9 PR creation | 🟡 `gh pr create` when a remote + `gh` exist; otherwise local branch + merge on approval | `agents/deployer.py` |

## Deliberate divergences from the brief

1. **No LangGraph** — explicit asyncio state machine with SQLite checkpoints (ADR-0001). Same
   vocabulary (graph, node, interrupt/resume), ~1/10 of the dependency surface, fully testable.
2. **No LLM SDKs** — one OpenAI-compatible `httpx` adapter covers DeepSeek/Gemini/OpenRouter/Ollama;
   native Anthropic adapter is optional (ADR-0002). Model names are config, not code.
3. **No ChromaDB/LanceDB** — SQLite + NumPy cosine; FastEmbed optional, hashed n-gram embedder
   offline (ADR-0003).
4. **No Next.js / shadcn** — Vite SPA embedded in the wheel so `pip install` ships the dashboard
   (ADR-0004).
5. **Kaizen cards do not auto-run** — they wait in the backlog for the Founder's approval
   (dashboard button or `promote`), as the brief frames them as catalogued for evaluation.
6. **Baseline-aware Inspector** — brownfield suites that are already red on `main` are recorded as
   tech-debt cards instead of blocking every story (found during the CLI smoke test).

## Known gaps / next steps

- Real-LLM end-to-end run against DeepSeek/Gemini (all tests use scripted providers).
- CodeRabbit webhook mode; PR review comments feeding back into the Worker.
- Nightly cycle scheduler (`loompa run --watch` exists; a cron/launchd recipe is not shipped).
- Dashboard: drag-and-drop priority, story diff viewer, finance charts.
- Packaging: publish to PyPI; `uvx loompa` verified locally via `uv run loompa` only.
