# Brief → implementation map

Status as of 2026-09-19 on branch `dev`. ✅ built & tested · 🟡 partial · ⚪ not started

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
| Plano set/2026 · Fase 2 (spike OpenCode) | ✅ `worker.backend` (aci\|opencode), `OpenCodeWorker` roda `opencode run` no worktree com agente `.opencode/agents/loompa-worker.md` restrito a `allowed_paths`, Ops trata falhas do subprocesso como qualquer crash, custo aproximado (tier `opencode`), `loompa worker backend` + settings API; comparação ACI×OpenCode é o Founder rodando a mesma story com cada backend e olhando kanban/finance (sem harness de report novo). ADR-0007 | `config/schema.py::WorkerConfig`, `agents/opencode_worker.py`, `engine/graph.py::node_dev`, `cli/worker.py`, `docs/adr/0007-*` |
| Plano set/2026 · Fase 4 (ferramentas para todos os papéis, MCP, Analyst) | ✅ perfis de permissão por papel (`Toolbox`), loop de ferramentas genérico em `LoompaAgent`, arquivos protegidos, cliente MCP (`loompa/mcp/`, SDK oficial) com Tavily, `AnalystAgent` e rota `research` (fontes conferidas em código, limitação declarada em código, revisão do PO, entrega ao Founder). ADR-0009 | `agents/toolbox.py`, `agents/base.py`, `mcp/client.py`, `agents/analyst.py`, `engine/graph.py`, `engine/phases.py`, `config/schema.py`, `docs/adr/0009-*` |
| Plano set/2026 · Fase 5 (conversas) | ✅ sessões de chat persistidas com rascunho de backlog e de sprint (só a Master/Analyst propõem operações, o código valida), Sprint Meeting (Master), Brainstorm (Analyst → Product Owner admite), `loompa meeting` como sessão de um turno, `loompa chat`, API e modal no dashboard. ADR-0010 | `conversations.py`, `agents/conversation.py`, `agents/master.py`, `agents/analyst.py`, `agents/product_owner.py`, `cli/chat.py`, `dashboard/app.py`, `ChatModal.tsx`, `docs/adr/0010-*` |
| Plano set/2026 · Fase 6 (parcial: `models sync`) | ✅ catálogo da OpenRouter ranqueado por custo-benefício (tier1: melhor nota sob teto de preço; tier2: mais barato acima de um piso), proposta na Caixa de Entrada, aplicada só com aprovação e fora de sprint. ADR-0011 | `llm/catalog.py`, `models_sync.py`, `cli/models.py`, `engine/scheduler.py`, `tests/test_models_sync.py`, `docs/adr/0011-*` |

## Deliberate divergences from the brief

1. ~~No LangGraph~~ — reverted: LangGraph adopted (ADR-0005) for industry alignment; the
   custom engine was replaced by a `StateGraph` with LangGraph's SQLite checkpointer.
2. **No LLM SDKs** — one OpenAI-compatible `httpx` adapter covers DeepSeek/Gemini/OpenRouter/Ollama;
   native Anthropic adapter is optional (ADR-0002). Model names are config, not code.
3. **No ChromaDB/LanceDB** — SQLite + NumPy cosine; FastEmbed optional, hashed n-gram embedder
   offline (ADR-0003).
4. **No Next.js / shadcn** — Vite SPA embedded in the wheel so `pip install` ships the dashboard
   (ADR-0004).
5. **Backlog cards do not auto-run** — they wait for a sprint (`loompa sprint start`, the kanban
   button or `meeting --run`); Kaizen findings additionally need an explicit yes (a delivery
   decision, `sprint add` or `promote`), as the brief frames them as catalogued for evaluation.
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
  CI stays scripted. ADR-0006 written. **Confirmed on the Founder's machine (2026-09-19):**
  `uv run pytest --live -m live -q --live-provider gemini --live-model gemini-3.5-flash-lite`
  passes both live tests against the real Gemini free tier.
- **Fase 0b** done (2026-09-18): keys only in `~/.loompa/secrets.env` (hub) or `<repo>/.loompa/.env`
  (factory, gitignored), config.yaml keeps env var names and `save_config` refuses key-shaped
  values; `loompa init` has a "Provedores e modelos" step (presets gratuito/economico/maximo,
  hidden prompts, connection test) and `loompa providers list|set-key|test|preset`; dashboard
  has ⚙ Configurações (providers, keys, tiers, roles, budget, Tavily) via
  `GET/PUT /api/factories/{slug}/settings` and `POST .../settings/providers/{name}/test`, the
  new-factory modal gained the same step, and the agent drawer shows/edits the role's tier.
  Defaults: `gemini-3.5-flash-lite` heads tier2; Groq provider added; roles are an open set.
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
- **Fase 2** done (2026-09-19): `worker.backend` (`aci` default, `opencode`) picks the Worker
  class in `node_dev`. `OpenCodeWorker` keeps Loompa's per-task/per-commit loop but runs
  `opencode run --format json --agent loompa-worker --model <router's pick>` inside the
  story's worktree; before the first task it writes `.opencode/agents/loompa-worker.md`
  scoped to `state.allowed_paths` (deny by default elsewhere), mirroring the ACI tool's own
  guard. The model's own `DONE:`/`BLOCKED:` line closes each task. Ops still owns
  retries/cooldowns: a missing binary or a non-zero exit is a plain `RuntimeError`, a timeout a
  `TimeoutError`, both triaged like any other node crash. Cost has no real token counts from
  the subprocess, so it's approximated (chars/4) through the same pricing table, tagged tier
  `opencode` so Finance can tell it apart. `loompa worker backend [aci|opencode]` and
  `PUT .../settings` (`worker_backend`) switch it without a restart. ADR-0007. The
  ACI-vs-OpenCode comparison itself is the Founder running the same story once per backend and
  reading the existing kanban/finance views — no new report generator was built for a decision
  meant to close, not to maintain.
- **Fase 3** done (2026-09-19, ADR-0008): `loompa/backlog.py` is the only write path for cards and
  only the Product Owner can build it (`add_item`, `set_priority`, `set_status`, `admit`); Master,
  Kaizen, the dashboard, `promote` and the founder's skip/drop go through it and a test walks the
  source AST to prove nothing else creates or ranks cards. Repeated titles no longer duplicate an
  open card. `WorktreeManager.as_role()` + `LoompaAgent.git`: merge, rebase, push, pull, checkout,
  reset, branch deletion and PR creation raise `GitAuthorityError` for every role but the
  Deployer (raw `git()` included); the OpenCode agent file denies the same commands. `Sprint`
  (`SP-001`, open/running/closed) with `loompa sprint start|add|status`: the Master runs the
  Sprint Meeting, the Product Owner admits the cards, and the Scheduler no longer dispatches
  anything still in BACKLOG (in-flight stories, epic children and `promote` still run). The sprint
  closes and emits `sprint.done` when all its stories are terminal; a story blocked on the founder
  waits alone. `FounderMessage.decisions[]` / `FounderAnswer.decisions`: a delivery offers each
  suggested card (sprint / backlog / drop) as its own decision (`loompa inbox reply -d S-007=sprint`,
  dashboard buttons, `POST /inbox/{id}/reply` with `decisions`); cards decided once are not asked
  again and undecided ones stay in the backlog. Dashboard: sprint chip + "Iniciar sprint" button,
  decisions in the inbox, `GET /sprints`, `POST /sprints/start`. Not done here: dashboard UI was
  type-checked and built but not looked at in a browser.
- **Fase 4** done (2026-09-19, ADR-0009): `agents/toolbox.py` gives every role a permission profile
  (worker: repo + its plan's paths + tests; inspector: repo + tests; architect/product/PO/analyst/
  master: repo read, `.loompa/specs/` write, `docs/` only inside a worktree) enforced when a tool
  is *called*; `LoompaAgent.tool_loop` is the Worker's old loop made generic and
  `ask_json_with_tools` lets the Architect and Product Loompas read the repository before they
  answer (`schedule.agent_tool_iterations`, 0 = one-shot). Credentials, `.git` and factory state
  are unreadable by any tool. `loompa/mcp/` is the MCP client on the official SDK: streamable
  HTTP or stdio, key in a header / URL parameter / child env (never in config.yaml), tools filtered
  by role and `allow` globs, an unreachable server becomes a declared limitation. `tools.tavily`
  is now an `McpServerConfig` (old configs still load); `loompa providers test tavily` and the
  settings screen test the same MCP path. `AnalystAgent` + route `intake → research →
  research_review → founder → done` for `kind=research` (no worktree, no merge): cited URLs must
  have been returned by a web tool in that run, the "no web search" limitation is added by code,
  the Product Owner reviews (two objective checks no reviewer can waive), follow-ups become cards
  offered on the delivery, the report is `research.md` (story drawer tab) and is indexed as a
  precedent. `--dry-run` never opens MCP. The dashboard research tab was type-checked and built,
  not viewed in a browser.
- **Tavily against the real server** (2026-09-20, ADR-0009 "Verified"): `Bearer` in the
  Authorization header works, so the `auth: query` fallback is not needed. Two fixes came out of
  the founder's onboarding run: (1) the default `allow` glob `*search*` also matched
  `tavily_research` — an agentic tool that bills on its own and answers in prose instead of the
  checkable sources the research route requires — so `allow` now names `tavily_search` and
  `tavily_extract` exactly, and a factory onboarded with the old glob is corrected on load;
  (2) when the MCP server failed but the REST API accepted the key, the wizard printed a fixed
  sentence blaming `tools.tavily.auth` and threw the real cause away. `probe_tavily` now retries
  once (a key pasted seconds after creation is often not live on the MCP gateway yet), names the
  cause in plain pt-BR, and keeps the technical reason in `ProbeResult.reason`, which
  `loompa providers test` prints. **Not verified:** an actual `tavily_search` call — listing tools
  proves the connection, not that a search returns usable sources.
- **Fase 5** done (2026-09-19, ADR-0010): a conversation is a row in a new `conversations` table
  (turns, a `Draft` of cards + sprint goal, limits the agent declared); until the founder commits, the
  stories and sprints tables are untouched. The model answers `{reply, ops, questions}` and
  `apply_ops` validates every edit (unknown refs skipped, repeated titles refine the card, titles that
  match an open card become references to it, existing cards can only change priority and sprint
  membership, at most 40 cards); the founder's checkboxes send the same ops. **Sprint Meeting**:
  `MasterAgent.converse`; commit sends the cards to the backlog through `ProductOwnerAgent.add_item`
  and, with "Começar Sprint", starts the sprint for the marked ones. **Brainstorm**:
  `AnalystAgent.converse` with repository + web tools, unverified URLs stripped from replies, a missing
  web search declared in `conv.limits`; `ProductOwnerAgent.admit_ideas` decides which ideas enter the
  backlog (held ones stay in the draft with the reason). `loompa meeting` is now a session of one turn
  (same output, the session stays in the history). `loompa chat meeting|brainstorm|resume|list` with
  slash commands, `GET/POST /conversations…`, and a two-pane `ChatModal` (☀️ Reunião, 💡 Brainstorm, 💬
  to resume). Seen in headless Chrome against the dry-run provider (brainstorm → backlog, meeting →
  uncheck a card → goal → Começar Sprint). **Live against Gemini (Founder's machine, 2026-09-19):**
  `test_live_sprint_meeting_conversation` passed; `test_live_brainstorm_conversation` failed with a 400
  ("Function call is missing a thought_signature") — Gemini 3 signs every function call and wants the
  signature back on the next request, and the adapter dropped it. Any role that uses tools with
  Gemini 3 can hit this (Architect/Product/Analyst/Worker), not only chats. Fixed in the OpenAI-compatible adapter (`ToolCall.extra` keeps the
  provider's `extra_content`, replayed to Google endpoints only; a history that another model made gets
  one retry with the documented `skip_thought_signature_validator` bypass). **Re-run the brainstorm live
  test to confirm the fix against the real API** (it was only checked against a mocked Gemini-shaped
  endpoint); the bypass retry has no live coverage at all. **Second live run:** the brainstorm passed,
  and `test_live_first_real_cycle` then failed for a different reason — the model replied "I prepared
  the story" and sent only a `goal` op, so the one-turn `loompa meeting` returned zero stories. Two
  fixes: the turn contract now states that every story described in `reply` needs its own `add` op,
  and `meeting()` falls back to the deterministic split of the goals when the model drafts nothing and
  asks nothing (the guarantee the pre-Fase-5 `meeting` had on its error path; a `meeting.recovered`
  event and a warning in the live test keep the salvage visible). A refused edit is now saved on the
  turn (`Turn.ignored`), because the first diagnosis was blind: only `changes` was persisted.
- **Third and fourth live runs (2026-09-19): two bugs of ours, neither in the providers.** Both
  conversation tests failed with a bare `'list' object has no attribute 'get'`. Cause: Google's
  OpenAI-compatible endpoint answers a 429 with a *list* body, `[{"error": {...}}]`, and
  `_retry_after_seconds` read it as an object. It is evaluated inside the
  `raise QuotaExhausted(..., retry_after=...)` expression, so the crash replaced the quota error
  before it existed and escaped the router's fall-through: a routine free-tier rate limit killed the
  whole turn. Fixed, plus a guard that turns any unparseable 200 into a retryable `LLMError` carrying
  the body, so a future provider quirk falls through to the next candidate instead of escaping raw.
  Two live runs were wasted first on guessed payload shapes; what found it was one line of
  `log.exception` in `run_turn` (the traceback goes to the log, never to the event —
  `/api/factories/{slug}/events` feeds the dashboard). Live after the fix: **Gemini 4/4**.
- **Models: OpenRouter is now the recommended preset**, first in `MODEL_PRESETS` and so the onboarding
  default — one key for every tier, spending cap in OpenRouter's own dashboard. Candidates were read
  from the live `/api/v1/models` catalogue, filtered to those supporting `tools` and excluding `:batch`
  (queued, wrong latency for a tool loop): `z-ai/glm-5.3` on tier1, `z-ai/glm-5.3-flash` and
  `deepseek/deepseek-v4-flash-0731` on tier2, free DeepSeek as fallback. Cheap *paid* models lead on
  purpose: OpenRouter's `:free` pool is throttled and rotates. Their real prices are in `pricing`,
  because `price_for` otherwise falls back to a generic $1.00/$3.00 and would bill a $0.09 model
  twelve times over.
- **`reasoning_effort` (new).** On a reasoning model the output budget pays for the thinking *and* the
  answer. The chat turn caps it at 1800 tokens, so `z-ai/glm-5.3` spent all 1800 reasoning and returned
  an empty string (`finish_reason='length'`). Mandatory reasoning is the norm at the tier1 frontier
  (GLM 5.3, Qwen Max and Grok 4.6 all force it; of the shortlist only `kimi-k3` does not), so changing
  model would only postpone it. `reasoning_effort` follows the path `max_tokens` already takes: a
  default per `ModelCandidate`, a per-call override that wins; `run_turn` asks for `"low"`, the
  Architect on a complex story still thinks at full effort. Anthropic accepts and ignores it (thinking
  there is a token budget, not an effort label). `ask_json`/`ask_json_with_tools` now log the reply,
  `finish_reason` and output tokens when the answer is not JSON — that log is what identified this.
  Live: **OpenRouter 4/4 on both tiers**, full suite green.
- Next: Fase 6 (technical backlog: CodeRabbit webhook, dashboard priority drag,
  story diff, cost charts, token suggestions 1-3, PyPI).
- **`loompa models sync` built (2026-09-20, ADR-0011), the first item of Fase 6.** Ranking rules were
  decided against the real catalogue (447 models, 85 pass the filters), not in the abstract: no
  benchmark means *out and counted*, never scored 0; a model that forces reasoning without a way to
  lower the effort is out (that is what broke tier1); tier1 is best-under-a-ceiling and tier2 is
  cheapest-above-a-floor, because a ratio always picks the cheapest. Governance as the Founder asked:
  an inbox decision, applied only when nothing is in flight (otherwise on the next `close_sprints`)
  and never over a configuration edited since. On the live catalogue tier1 comes out as Qwen3.8 Max,
  Grok 4.6, GLM 5.3 and tier2 as GLM 5.3 Flash, GPT-5.6 Luna, Qwen3.8 27B — Luna, Qwen 27B and Grok are
  **not validated live**. Also fixed: `LIVE_DEFAULT_MODEL["openrouter"]` now reads the preset's tier2
  lead; `apply_preset` copies its candidates instead of sharing them across factories.
  **Follow-up (same day): the OpenRouter preset now uses `-latest` aliases** (ADR-0011 §6) and
  `models sync` understands them. Checked live: 8 aliases answered a text and a tool call, and the four
  `live` tests passed 4/4 on `~z-ai/glm-flash-latest` and on `~z-ai/glm-latest`. Two things only the
  live catalogue could show: aliases have no benchmark (rated through their target) and the cost
  tracker bills the *requested* id, so every alias has a `pricing` entry. Open trade: an alias can move
  to a new model without the inbox approval — nothing detects that yet.
  **Later the same day:** the two dead `:free` ids are gone from the `gratuito` preset and pricing.
  `AliasWatch` tells the founder, once per change, when an alias starts answering with another model
  (from `response.model` and from the catalogue's `alias_target`; ADR-0011 §6). Onboarding: `loompa setup`
  (models, keys tested as they are typed, web search, OpenCode, GitHub) and `loompa doctor` read one
  checklist (`config/services.py`, ADR-0012); `loompa init` ends in the wizard. Fixed on the way: an
  OpenCode subprocess never saw a key stored in the secrets file, it now gets its model's key. Checked
  live: `doctor` against the real OpenRouter key passes. **Not verified:** OpenCode itself (not installed
  here), and the dashboard settings screen does not show the checklist yet.
  **Then:** the Gemini 2.5 ids (`gemini-2.5-flash`/`-pro`, leaving the catalogue on 2026-10-20) are replaced
  by `gemini-3.8-flash` in the default tiers and in `gratuito`/`economico`/`maximo`. Verified only against
  OpenRouter's catalogue, whose ids mirror Google's own (`gemini-3.5-flash-lite` is the same in both and
  passed live); **a Gemini key is needed to confirm the direct id and that it is on the free tier.** The
  `gemini-3.5-flash-lite` price was 3x/6x too low (copied from 2.5) and is now the catalogue's.
  `loompa schedule` (`scheduling.py`) prints the recipe for the nightly `loompa run` and the **monthly**
  `loompa models sync`, as launchd agents (`--write DIR` writes them; checked with `plutil -lint`, not
  loaded) or crontab lines. Nothing switches itself on.

## Known gaps / next steps

- First real research run: `loompa providers set-key tavily`, `loompa providers test tavily`, then
  describe a story as a research request ("Pesquisar alternativas de gateway de pagamento para o
  Brasil") so the Master classifies it as `research`. There is no explicit "kind" switch on card
  creation yet; intake decides.
- OpenCode backend has no DoD self-check yet (ACI path has one); add it if OpenCode becomes
  the standard. Its `.opencode/agents/*.md` permission schema hasn't been run against a real
  `opencode` install (tests script a fake binary) — confirming that is part of running the spike.
- CodeRabbit webhook mode; PR review comments feeding back into the Worker.
- Dashboard: drag-and-drop priority, story diff viewer, finance charts.
- Packaging: publish to PyPI; `uvx loompa` verified locally via `uv run loompa` only.
- The full test suite intermittently hangs after reaching 100% on some machines (a thread-join flake at
  teardown, pre-existing). It runs to completion on the Founder's machine.
