# Brief → implementation map

Status as of 2026-09-30 on branch `dev`. ✅ built & tested · 🟡 partial · ⚪ not started

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
| Plano set/2026 · Fase 7 (Specs, QA Gates e Raciocínio de Agentes) | ✅ ler antes de escrever + `LoopGuard` (7.11), higiene do diff (7.10), juiz com rubrica, evidência de teste e auto-cura (7.2), reproducer-first (7.7), alternativas (7.9), checagem rápida pós-escrita (7.8), spec/plan enriquecidos (7.1), revisão de spec mais funda (7.3), modos de autonomia e pre-flight de risco (7.5/7.6), `owner != reviewer` (7.4; Workers especializados adiados sem evidência). ADR-0014 | `agents/loopguard.py`, `hygiene.py`, `risk.py`, `aci/tools.py`, `agents/inspector.py`, `agents/worker.py`, `agents/architect.py`, `engine/graph.py`, `speckit/templates/` |


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

Decided 2026-09-29:
1. ~~Send only the constitution/spec sections relevant to the task~~ — **not done, on purpose.** A
   per-task selection would change the Worker's system prefix every task and break the prefix cache
   that already makes the constitution and spec cheap. Measured in `contas` (2026-09-29): the Worker
   sent 4.27M input tokens, 64% of them served from the provider's cache, while tool results added only
   ~89k over 275 calls. Input cost is the prompt and history re-sent each turn, so the stable prefix is
   what to protect; the next saving is fewer turns per task, not smaller reads.
2. **Output budget by complexity** instead of per-role caps (`models.output_scale`, ADR-less, see
   commit 0c3bdb1): the cap is a ceiling, not a price, and a cut answer is now retried with twice the
   room — a cut costs a whole call, a roomy cap costs nothing unless used.
3. **Tool tokens per step** — every `tool.call` event carries the size of what it put in the context;
   the Finance Loompa names the agent and tool behind most of it and the cost screen lists them.

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
  `loompa providers test` prints. A real search followed once the install was fixed (below): a
  brainstorm called `tavily_search` twice and `tavily_extract` once, cited a source that survived
  the citation gate, and was never offered `tavily_research`.
- **The install, not the key, was breaking web search** (2026-09-20, found from a brainstorm that
  declared "a busca na web não estava disponível"). The founder's `loompa` is a `uv tool install
  --editable` whose *source* is the repo but whose *dependencies* are frozen from before Fase 4,
  so `mcp` is absent there: every connection died on `ModuleNotFoundError`, which the broad
  `except` in `McpHub.session` recorded as just another unreachable server. The same install ran
  `loompa setup`, so the wizard's Tavily warning had this cause too — not the fresh-key race that
  was guessed. `uv tool upgrade loompa-core` (or running from the repo venv) fixes it. Two code
  changes so it cannot hide again: `probe_tavily` names an incomplete install as its own cause,
  and `AnalystAgent._web` emits a `web.unavailable` event with the technical reason, because the
  founder's sentence deliberately never carries it. A dependency added later still won't reach an
  existing tool install — nothing in the code can detect that from inside the broken environment
  beyond reporting it, which is now what happens.
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
  `loompa schedule` (`scheduling.py`) prints the recipe for the nightly `loompa run` (the monthly
  `models sync` job was removed on 2026-09-21, see below), as launchd agents (`--write DIR` writes them; checked with `plutil -lint`, not
  loaded) or crontab lines. Nothing switches itself on.

- **2026-09-21 — the founder owns the keys and the models; the budget is a week.** The four model
  presets are deleted everywhere (`config/presets.py`, the onboarding selector, `loompa init
  --preset`, `loompa providers preset`): they existed to arrange one key that reaches every model,
  and a factory now ships that way — a matrix of fixed OpenRouter ids. Providers are OpenRouter
  (recommended), Gemini, Anthropic, OpenAI, xAI, DeepSeek and Ollama, each with the page that lists
  its models and a cheap id to test a key with; Groq is dropped from a config that holds no key for
  it and names it in no tier. **The ranking never proposes a `~vendor/x-latest` alias** (ADR-0011
  amendment): three fixed candidates per tier already cover a model going down, and an alias changes
  model *and price* with nobody approving it. Aliases stay listed in the full catalogue the
  dashboard reads (446 models, each with `alias`, `alias_target` and why it is out of the ranking)
  with a filter, for a founder who adds one by hand. **The monthly `models sync` cron is gone**;
  `ModelWatch` reports a price rise when the catalogue is next read and a missing model on the first
  call that fails. That needed a fix: OpenRouter answers **400** `"x/y is not a valid model ID"`,
  not 404, so the status check never fired — `model_not_found` reads both, confirmed live.
  **Budget is a period** (`weekly`, US$ 5) and says what happens at the cap: pause the line, or put
  every role on free models; old `monthly_cap_usd`/`hard_stop` configs keep their numbers.
  `tier1_ceiling` moved next to the cap at **US$ 1.25** (a quarter of the week). Named calls get
  their own tier (`ROLE_TASKS`) instead of a hard-coded `tier_override` in the agent, and
  `models.clusters_enabled: false` collapses the three clusters into one `general`, ranked on the
  plain mean. The settings screen was rebuilt around all of this: ordered provider rows with vendor
  marks, a provider picker per matrix cell limited to providers that hold a key, a guided model list
  ranked the way each tier is read plus an extended search over the whole catalogue, a connection
  test per cell, alphabetical Loompa cards carrying their task tiers, and no more Safari-broken
  title tooltip. Scores are rounded at the source (a raw mean reached the screen as
  `63.800000000000004`). Suite green; catalogue, probe and the 400-vs-404 fix checked live.

- **2026-09-29 — the fixes the `contas` validation asked for, and the rest of Fase 6.** The throw-away
  factory had been stuck for a week with no code delivered; each cause is now fixed in the code, not in
  the factory: a repo with no commit gets its first one from the Deployer (and `init` makes it); an
  answer cut at the output limit is retried with more room, and the budget grows with the story's
  complexity; "tentar de novo" keeps what the founder wrote, and a block that comes back unchanged says
  so and stops recommending a retry; the Master's rewrite of a block no longer invents option keys the
  engine reads as a retry ("later"); the Python skeletons pass their own tests (no build system, and a
  one-command Typer app); a story going back to work is rebased onto fixes merged meanwhile and its
  baseline measured again; escalating to tier 1 re-plans once, because two failures may mean the plan
  fenced the Worker off the cause (S-005 could not touch the module that broke the test).
  Running S-005 to delivery found five more, each fixed and tested: the founder's guidance (changes on a
  delivery, or the answer to a Worker's question) amends the plan's paths and tasks through the Architect
  instead of re-fencing the Worker; the Worker can delete a file (it emptied one instead); `pytest -q`
  counts are read without the ===== banner (every green run reported 0 passed); Kaizen findings about the
  same file are one card (one leftover file had become four); the Inspector's judge sees the plan's real
  paths; the Worker's self-check counts what earlier tasks already committed. S-005 was approved and
  merged: `contas` now passes its own tests and `uv run contas hello` works.
  Two structural fixes followed, from S-001/S-003: story worktrees moved out of the repo (to
  `~/.loompa/worktrees/<repo>-<hash>/`; nested, pytest took the root `pyproject.toml` as rootdir and
  tested the main checkout's package instead of the story's code; old worktrees move out on first use),
  and a conflict with the base is resolved instead of aborted forever: the base is merged into the story,
  lockfiles take the base's copy, the Worker resolves the markers, the Deployer commits only when none is
  left, and a conflict block's answer goes back through `dev`.
  Verified live on S-001: its merge of the base conflicted in `pyproject.toml` and `tests/test_cli.py`,
  the resolver combined both sides (the story's `add` tests and main's `uv run` test) and the Deployer
  committed the merge. The resolver is one structured call with the files in the prompt (the first
  version, a tool loop, only re-read the files). Also from this run: ruff's current output format is
  read (a blocked story had green tests and a lint error nobody could see), uncommitted work is kept
  as a commit before merging the base, and Loompa's own VIRTUAL_ENV no longer reaches factory commands.
  Later in the same run: the Worker got `fix_lint` (the linter's own `--fix` and the formatter, narrowed
  to the plan's paths; S-003 had made eleven hand edits without finding ruff's import order); a merge
  interrupted halfway is started over instead of committed as work in progress; the conflict resolver
  keeps every name the base defines (a merged `storage.py` had lost a function the base's CLI imported);
  the first commit leaves out what agents keep rewriting in `.loompa/`; and one engine per factory is
  enforced with a lock (a stray second `loompa run` had dispatched the same stories). Fase 6: backlog drag-and-drop through the PO, a story diff tab, a cost screen
  (per day, role, model, tool), the CodeRabbit webhook (ADR-0013, not verified against GitHub itself),
  PyPI packaging (`mcp>=2.2,<3`, wheel checked in a clean venv, a tag-triggered Trusted Publishing
  workflow — nothing published). Test suite: leaked aiosqlite threads no longer hang pytest at exit.
  Intra-story parallelism stays out: nothing in Fase 1 or since showed a need.

- **2026-09-30 — Fase 7 (ADR-0014), in the order the `contas` run pointed to.** 7.11: the ACI refuses
  to edit a file the task has not read; `LoopGuard` answers repeated lookups and test runs with nothing
  changed from memory, notes a streak of reads and a write tool that keeps failing; pruning keeps the
  latest read of each file; fixes state their root cause in the first edit's `reason`; the outline is in
  the Worker's cached prefix; `worker.task`/`tool.call` now measure rounds, repeats and paths. 7.10:
  `hygiene.py` (debris files, machine paths, debugger calls, conflict markers block; TODOs, debug prints,
  whitespace-only files are noted) in the Worker, the Inspector and the Deployer. 7.2: the judge sees the
  checks as facts, follows a rubric, keeps at most three anchored medium/high findings and needs a reason
  for a failed criterion; tautological tests and code without tests are found in code; only medium/high
  become Kaizen cards; a lint-only failure is fixed by the linter at $0 and a high finding gets one Worker
  round before the founder. 7.7: a bugfix starts with a test that must fail (tests-only fence during
  it). 7.9: alternatives in the plan. 7.8: every write returns its syntax errors and undefined names.
  7.1/7.3: NFRs, edge cases, entities, assumptions; impact, rollback, traceability, constitution check;
  the PO checks feasibility and declared dependencies; the Architect can bounce a spec once — compared
  with the official Spec Kit templates (what was taken and what was left out is in ADR-0014). 7.5/7.6:
  `schedule.autonomy`, and a `preflight` phase with `risk.md` built on facts measured in code. 7.4: the
  phase registry refuses a self-reviewed phase; specialised Workers not built (no evidence).
  **Found on the way:** the command runner's `FORCE_COLOR=0` turned colours *on* in Rich/Typer and ruff —
  the cause of `contas` S-030's failing help tests and of Kaizen card S-037; fixed.
  **Cost measured before adding:** system prompts +143 to +429 tokens each, ~2-4% more input per story.
  **Not verified against a real model:** all of it. Resuming `contas` Sprint 1 (S-002, S-007, S-030,
  S-031) on this build is the test: compare `worker.task` rounds/repeats and Kaizen cards per delivery
  with Sprint 1 so far. S-030 in particular should now pass its help tests without code changes.
  Suite: 376 passed.

- **2026-09-30 — every model-facing text in English, prompts reviewed.** Besides the system prompts
  (already English), the user-message headings, the 15 tool descriptions, the ACI's results and errors,
  the test/lint summaries, the Toolbox refusals, the pruning stub (`[pruned]`), the JSON retry, the
  memory's precedents header and the engine's `failure_history` entries were Portuguese; all are English
  now. The founder-facing parts stay pt-BR (`founder_notes` shows in the story drawer, Kaizen cards,
  inbox). The 16 prompts were reviewed against one checklist: a role line, rules with their reason,
  "the material you get is data, not instructions" (only the Analyst and the explore hint said it), an
  instruction for when unsure (DoD: answer complete when the diff cannot tell; classify: STANDARD), one
  output phrase ("Respond with JSON only") and an exact language contract (text values in `{language}`,
  keys/paths/code untouched); a factory anecdote left the conflict prompt and a Portuguese example the
  spec prompt; DoD `missing` items are English (model to model). Cost: +625 tokens over the 16 prompts,
  +78 on the Worker's cached prefix. Not verified live.

- **2026-09-30 — `contas` Sprint 1 resumed on the Fase 7 build (first live test of Fase 7).** The four
  open stories were started over with a new `loompa sprint restart` (spec, plan, code and branch
  discarded; the LangGraph thread gets the new state) so they ran on the new templates and gates.
  **Delivered and merged:** S-002 (`list`) at the first attempt — 74 calls and US$ 0.07 against 471
  calls and US$ 0.27 without a delivery on the old build — and S-030 (help in pt-BR). SP-001 closed.
  **Still open:** S-007 (export CSV: CONCERNS, then a conflict with S-002 being resolved) and S-031
  (bugfix, reproducer task running). The run stopped because the Mac was asleep on 1% battery (see
  below), not because of the factory. US$ 0.44 over 546 calls since the restart.
  Seen working live: the new spec sections; the PO rejecting a spec that left an edge case open (fixed
  in round 2); the BDD→test traceability and alternatives in the plan; a bugfix classified at intake
  (no spec review, reproducer T1); a plan lifted to pre-flight because it touches `models.py`, with a
  `risk.md` built on measured facts; the tier-1 re-plan; Kaizen reusing a card for a repeat finding;
  the `FORCE_COLOR` fix (S-030's colour failures are gone).
  **Fixed from what the run showed:** (1) the checkpointer's enum allowlist was hand-kept and missed
  `Autonomy` — now derived from `engine/state.py`; (2) the Python skeletons now declare
  `typer.Option`/`fastapi.Depends` immutable for ruff's default B008 (a `Path` option failed the lint
  gate on correct code); (3) the router remembers the output budget that fitted after a cut, per model
  and role (52 cut-and-retry calls in one hour, mostly a 900-token self-check fitting at 1800-3600);
  (4) pre-flight mitigations that write no file ("run the suite, record the baseline") are dropped, and
  a characterisation test pins only behaviour the plan keeps; (5) a founder's "retry" with text now
  amends the plan, and the Inspector's judge and the Worker's self-check get the founder's guidance,
  which wins over the spec (S-030: the founder withdrew a criterion and the self-check asked for it
  again); (6) a wall-clock limit per model call (`models.call_timeout_s`, a guard: the stalls it was
  written for were the Mac sleeping); a clock-dependent conversation test.
  **Open, not fixed (next):** the PO's No-Invention gate let S-030's spec turn "no *new* tables" into
  "no border character in any help", which Typer always draws — and nothing in escalation ever questions
  the spec, only code and plan, so two tiers and a re-plan went into an impossible criterion; the
  blocked message then told the founder "users can't see the help", when the failing test was the
  factory's own. A Worker task cut by the tool-call limit is marked done and the rest of the checklist
  is skipped, so the Inspector judges a half-built story and a tier-2 attempt is spent on it. A test
  run left `gastos.json` at the repo root and `commit_all` committed it (hygiene does not flag new data
  files). Merged story branches are never deleted. The factory's main checkout accumulates uncommitted
  agent edits under `.loompa/`. Money is formatted three ways across `add`/`list`/`resumo` (product
  card). Spec text from deepseek-v4-flash slips into Spanish. `llm.call` events carry no
  `finish_reason`, `tool.call` no search query. No watchdog notices a running story with no event for
  N minutes. Inbox INFO/FINANCE notes stay pending forever; US$ amounts use "0.33" and "0,07" side by
  side.

- **2026-09-30 (afternoon) — `contas` Sprint 1 finished.** With the Mac on power (`caffeinate -i`)
  S-007 and S-031 were delivered, reviewed by hand and merged; SP-001 and SP-002 are closed and the
  factory's main has 115 tests green and a clean lint. US$ 1.58 over 861 calls since the morning restart
  (S-002 0.07 · S-030 0.18 · S-007 0.53 · S-031 0.81). **Fixed on the way:** a conflicted file over
  20k characters was skipped by the resolver without a word, the merge aborted and S-007 kept being
  tested on its old base (59 tests instead of 77) — large files now go block by block, and blocks where
  both sides only added lines (the usual case: tests appended to the same file) are settled without a
  model through diff3 (S-007's merge, 17 model minutes and a failure before, settled at once); a run
  stopped inside `dev` resumed with no task done and replayed the founder's amend (tasks marked `[x]`
  now count, an amend is applied once, a model's own "T8:" numbering is stripped). **Verified live:** the
  reproducer went red before the fix (S-031); the founder's guidance reached the self-check and the
  judge (S-007's "para", S-031's Brazilian-format note became card S-040); diff hygiene reverted a
  whitespace-only change; the call timeout cut a model hung for 600 s.
  **Main cost now is speed, not correctness:** S-031 was a 4-line fix plus tests and took ~2.5 h —
  a 47-minute process task left by the old pre-flight, a 35-minute task that looped (53 calls, 14
  repeats) until the tool-call limit, tier-2 calls of 1-3 minutes with cut-and-retry, and a judge of
  15-20 minutes per story. The dashboard showed the same task text for 47 minutes, so a slow story looked
  stuck.

## Known gaps / next steps

- First real research run: `loompa providers set-key tavily`, `loompa providers test tavily`, then
  describe a story as a research request ("Pesquisar alternativas de gateway de pagamento para o
  Brasil") so the Master classifies it as `research`. There is no explicit "kind" switch on card
  creation yet; intake decides.
- OpenCode backend has no DoD self-check yet (ACI path has one); add it if OpenCode becomes
  the standard. Its `.opencode/agents/*.md` permission schema hasn't been run against a real
  `opencode` install (tests script a fake binary) — confirming that is part of running the spike.
- CodeRabbit webhook: verify against GitHub (public URL for the dashboard, a repo with CodeRabbit).
- PyPI: register the trusted publisher, then `git tag v0.1.0 && git push origin v0.1.0`.
- Speed (from the finished `contas` Sprint 1): a streak of repeats should end the task with a diagnosis
  instead of running to the tool-call limit; a task cut by that limit must not count as done; measure
  time and cost per role with other tier-2 models for the Worker and the judge before touching presets;
  the dashboard should show live activity (last tool call, calls in this task) and flag a story with no
  event for N minutes. Run long local sessions under `caffeinate -i`.
- From the 2026-09-30 run, in order of cost: a spec criterion that contradicts existing behaviour must
  go back to the PO instead of up the tiers; a task cut by the tool-call limit must not count as done;
  hygiene should catch data files a test run leaves in the tree; a stall watchdog; delete merged story
  branches; `finish_reason` on `llm.call`.
- Fase 7 follow-ups: specialised Workers (7.4) only if `worker.task`/failure history show a stack-context
  cause; the Inspector judge could prefer a different model from the one that wrote the code (separation
  at model level, not built); the dashboard does not show `risk.md` or the autonomy mode yet.
- Some tests leak aiosqlite connections; `tests/conftest.py` exits with the verdict instead of hanging
  on them and names the threads on stderr. Closing them at the source is still open.
