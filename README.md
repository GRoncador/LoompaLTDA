# Loompa LTDA — `loompa-core`

An autonomous, multi-agent **software factory** governed asynchronously by a single Founder.

The Founder holds one short morning meeting, the factory works all day in isolated Git
worktrees, and every question, blocker or delivery lands in an **executive inbox** written in
plain business language. No babysitting, no serial blocking, no terminal noise.

```
[ Founder terminal / HQ dashboard ]
            │
   ┌────────┴────────┐
   ▼                 ▼
Factory A         Factory B          each: .loompa/{config.yaml, constitution.md, specs/,
(brownfield)      (greenfield)              learnings.md, decisions/, state.db, memory.db, worktrees/}
```

## Quick start

```bash
uv tool install loompa-core            # or: pip install loompa-core
cd my-project
loompa init                            # greenfield vs brownfield detection + calibration
loompa meeting "Recuperação de senha; refatorar webhook de cobrança"
loompa run                             # batch execution in isolated worktrees (Ctrl-C safe, resumable)
loompa inbox list                      # evening review: decisions & deliveries in plain language
loompa inbox reply <id> --option approve
loompa dashboard                       # http://127.0.0.1:8765 — pixel-art office, inbox, kanban
```

Everything also works with **zero API keys** via `--dry-run` (scripted models, real git/tests):

```bash
loompa meeting "Tela de login; Exportar CSV" --dry-run && loompa run --dry-run
```

## Models & cost

Providers and the tiered model matrix live in `.loompa/config.yaml` (defaults in
`src/loompa/config/defaults.yaml`). Set the keys you have; the router falls through in order and
skips providers without keys or with exhausted quota:

| Env var | Provider | Default use |
| --- | --- | --- |
| `DEEPSEEK_API_KEY` | DeepSeek (`deepseek-chat`, `deepseek-reasoner`) | Tier 2 executors / Tier 1 reasoning |
| `GEMINI_API_KEY` | Google AI Studio (OpenAI-compatible endpoint) | Free-tier fallback for both tiers |
| `ANTHROPIC_API_KEY` | Anthropic | Last-resort Tier 1 / Tier 2 |
| `OPENROUTER_API_KEY`, Ollama | any OpenAI-compatible endpoint | add to `providers:` |

Every call is metered (exact tokens × configured pricing). The Finance Loompa alerts the inbox at
80 % of `budget.monthly_cap_usd` (default US$ 30) and the scheduler pauses at 100 %.

## How a story flows

```
BACKLOG → SPEC (Product) → PLAN (Architect) → DEV (Worker, worktree) → TEST (Inspector)
        → REVIEW (Deployer: rebase, PR, delivery message) → AWAITING_FOUNDER → DONE (merge)
```

* **Escalation ladder**: Tier 2 ×2 → Tier 1 ×1 → `BLOCKED_AWAITING_INPUT` with an executive
  inbox message. Other stories never wait.
* **Spec Kit**: nothing is coded without `constitution.md` + `spec.md` + `plan.md` + `tasks.md`.
  One task = one semantic commit; the Worker is physically fenced to the plan's paths.
* **ACI**: agents get paginated reads, exact edits, unified-diff patches and compacted test/lint
  output only — never a raw shell.
* **Kaizen**: out-of-scope findings become `learnings.md` entries + backlog cards; lessons from
  escalated fixes are appended to the constitution and indexed in local memory.
* **Hybrid memory**: AST/ripgrep for code; local SQLite vector RAG (FastEmbed optional, $0) for
  ADRs, constitution, learnings and past resolutions.

## CLI

| Command | Purpose |
| --- | --- |
| `loompa init [path] [--stack python-fastapi\|python-cli\|node-react\|custom]` | Onboard a repo (Brownfield scanner / Greenfield initializer) |
| `loompa meeting "goals" [--run] [--file transcript.txt]` | Morning meeting → stories |
| `loompa run [--watch] [--parallel N] [--dry-run]` | Continuous batch execution |
| `loompa inbox list\|reply\|batch` | Founder inbox (batch decisions) |
| `loompa status`, `loompa report`, `loompa kaizen` | Kanban, end-of-day executive report, learnings |
| `loompa ask compliance\|metrics\|storyteller "…"` | On-demand support Loompas |
| `loompa memory index\|search` | Organizational memory |
| `loompa factories list\|use\|remove` | Multi-factory hub (`~/.loompa/factories.yaml`) |
| `loompa dashboard [--dry-run] [--no-engine]` | Localhost HQ |

## Development

```bash
uv sync --dev && uv run pytest -q && uv run ruff check .
cd dashboard-ui && npm install && npm run dev      # UI dev server proxied to :8765
npm run build                                      # writes src/loompa/dashboard/static (committed)
```

Architecture decisions: `docs/adr/`. Brief → implementation map: `docs/STATUS.md`.
