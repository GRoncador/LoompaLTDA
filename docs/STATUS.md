# Brief → implementation map

Status as of 2026-09-18 on branch `dev_fable`. ✅ built & tested · 🟡 partial · ⚪ not started

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
| §8C LangGraph async graph, non-blocking branches, SQLite checkpoints, interrupt/resume | ✅ (ADR-0005) | `engine/langgraph_engine.py` |
| §8D Git worktree isolation | ✅ | `worktrees/` |
| §8E Spec Kit triad | ✅ | `speckit/` |
| §8F ACI (paginated reads, patches, compacted logs) | ✅ | `aci/` |
| §9 Dashboard: Phaser office, inbox, kanban, multi-factory, WebSockets, agent drawer | ✅ | `dashboard/`, `dashboard-ui/` |
| §9 PR creation | 🟡 `gh pr create` when a remote + `gh` exist; otherwise local branch + merge on approval | `agents/deployer.py` |
| Plano set/2026 · Fase 0 (estabilização) | ✅ locked-db retry, Ops-triaged runner crashes, `live` marker, ADR-0006 | `store.py`, `engine/scheduler.py`, `tests/test_live.py`, `docs/adr/0006-*` |
| Plano set/2026 · Fase 0b (provedores, modelos e chaves por fábrica) | ✅ secrets files, presets, `loompa providers`, settings screen + API, tier per agent, secret guard | `config/secrets.py`, `config/presets.py`, `config/settings.py`, `cli/providers.py`, `dashboard/app.py`, `SettingsModal.tsx` |
| Plano set/2026 · Fase 1 (pipeline como dado e revisões) | ✅ route/kind/complexity/handoff, phase registry, spec_review (PO, No Invention), graded QA gate, DoD, complexity→tier, epic split | `engine/phases.py`, `engine/graph.py`, `agents/product_owner.py`, `agents/inspector.py`, `agents/worker.py`, `llm/router.py` |

## Deliberate divergences from the brief

1. ~~No LangGraph~~ — reverted: LangGraph adopted (ADR-0005) for industry alignment; the
   custom engine was replaced by a `StateGraph` with LangGraph's SQLite checkpointer.
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

## Token & cost optimizations

Done (2026-09-17):
- **Prompt-cache friendly Worker prompt** — the stable blocks (rules, constitution, spec, plan,
  allowed paths) form the system prefix, identical across every task and retry of a story, so
  DeepSeek/Gemini prefix caches hit automatically and Anthropic gets an explicit `cache_control`.
- **Tool-history pruning** — after `schedule.worker_keep_tool_results` (default 6) tool results,
  older outputs collapse to a one-line stub, so long tasks stop re-paying for every file read.

Suggested next (not implemented):
1. Send only the constitution/spec sections relevant to the task (semantic selection) and let
   the Worker fetch the rest on demand via `read_file`.
2. Per-role output caps: Product/Architect/Inspector judge rarely need more than ~1,500 tokens.
3. Finance Loompa: record input tokens per tool step to point at agents that read too much.

## Plano set/2026 — progress

- **Fase 0** done (2026-09-18): `database is locked` is retried at the Store, the memory store and
  the LangGraph checkpointer; a runner crash outside the nodes goes through the Ops triage
  (backoff, then a plain pt-BR inbox note) and a story is never dispatched twice. `uv run pytest --live
  -m live` runs one real cycle: the terminal asks provider, model and key (hidden input, nothing
  written anywhere; the key lives only in the test process and a throw-away factory under tmp).
  CI stays scripted. ADR-0006 written. **Pending on the Founder's machine:** the first live run,
  no key was available during the build session.
- **Fase 0b** done (2026-09-18): keys only in `~/.loompa/secrets.env` (hub) or `<repo>/.loompa/.env`
  (factory, gitignored), config.yaml keeps env var names and `save_config` refuses key-shaped
  values; `loompa init` has a "Provedores e modelos" step (presets gratuito/economico/maximo,
  hidden prompts, connection test) and `loompa providers list|set-key|test|preset`; dashboard
  has ⚙ Configurações (providers, keys, tiers, roles, budget, Tavily) via
  `GET/PUT /api/factories/{slug}/settings` and `POST .../settings/providers/{name}/test`, the
  new-factory modal gained the same step, and the agent drawer shows/edits the role's tier.
  Defaults: `gemini-2.5-flash-lite` heads tier2; Groq provider added; roles are an open set.
- **Fase 1** done (2026-09-18): `StoryState` carries `kind`, `complexity`, `route`, `phase` and
  `handoff`; `engine/phases.py` is the phase registry (name, node, owner, reviewer, kanban stage)
  and `build_route()` decides which phases a story visits (SIMPLE stories and ordinary bugfixes
  skip `spec_review`). `node_intake` asks the Master to classify; requests that bundle several
  deliverables become an epic with child stories (`origin=epic`) and the parent closes with an
  INFO note. `ProductOwnerAgent.review_spec` runs the "No Invention" gate with one rewrite round
  (feedback travels in `handoff`). The Inspector returns PASS/CONCERNS/FAIL/WAIVED with
  SEC-/PERF-/TEST-/ARCH- findings: CONCERNS ship and feed Kaizen cards, WAIVED asks the Founder
  (`BlockedReason.WAIVER`, options fix/waive/drop). The Worker runs a DoD self-check after each
  task and finishes what is missing once. `ModelRouter.candidates(..., complexity=)` runs SIMPLE
  stories on tier2 and lifts product/product_owner/inspector/analyst to tier1 on COMPLEX. Legacy
  stories without a route resume from their kanban stage. Kanban cards show kind, complexity,
  current phase and QA verdict.
- Next: Fase 2 (OpenCode spike) or Fase 3 (Backlog service with PO as single writer, git guard,
  Sprint, batch approvals), per the Founder's call.

## Known gaps / next steps

- First real end-to-end run (`uv run pytest --live -m live -q`, interactive prompts) still to be executed by the Founder.
- CodeRabbit webhook mode; PR review comments feeding back into the Worker.
- Nightly cycle scheduler (`loompa run --watch` exists; a cron/launchd recipe is not shipped).
- Dashboard: drag-and-drop priority, story diff viewer, finance charts.
- Packaging: publish to PyPI; `uvx loompa` verified locally via `uv run loompa` only.
