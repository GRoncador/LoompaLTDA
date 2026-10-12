# ADR-0044: Publishing — platform adapters, staging by default, production by acceptance, periodic upkeep

Date: 2026-10-11 · Status: proposed (Phase 18, not built)

## Context

On 11/10 Gustavo asked what a factory can do to configure and maintain a product in production
and staging on platforms such as Firebase and Cloudflare. Today it can do nothing past git: the
Deployer rebases, merges, queues merges (ADR-0029) and opens a PR when there is a remote. The
Inspector's smoke and visual checks (ADR-0030) run on a local server. `firebase.json` appears only
in `hygiene.ROOT_CONFIG`; nothing deploys, reads production logs or watches a provider's bill.

The discussion settled four points:

1. **Maintenance is periodic, not real-time.** No daemon and no on-call. A check runs when the
   line starts and the last one is older than a period, and right after a deploy. Its findings go
   to the backlog; nothing runs outside a sprint (ADR-0018 stands).
2. **Publishing is a decision that can come at any time**: at onboarding, or in the middle of a
   product that started 100% local and later pivots.
3. **Whatever the factory cannot do, the founder gets as a guided step**, without asking for it:
   why, numbered steps, ready links, where to paste a key, and a check that confirms it worked.
4. **Hosting cost is watched like token cost**, by Finance, against the product's budget.

## Decision

### 1. A catalogue of platform adapters

Each supported platform is an adapter in code (`loompa/platforms/`) with one interface:

```
requirements() · guide(step) · verify(step)
deploy(env) · promote(release) · rollback(release) · status()
logs(since) · costs(period) · budget()
```

- **Writes go through the CLI or the provider's REST API, never through MCP.** `firebase deploy`,
  `wrangler deploy` and the like are deterministic, have exit codes, cost no tokens (Tier 3) and
  are faked in the dry-run. With MCP the model would pick the call; for production, code decides.
- **Reads may go through MCP** (Firebase's and Cloudflare's official servers, `loompa/mcp`,
  ADR-0009): logs, errors, metrics and docs are where a model explores. Their output is data.
- **The factory deploys only where an adapter exists.** The Architect or the PO may research and
  suggest a platform for the spec and budget, but the choice is the founder's, in a meeting.
  A platform outside the catalogue becomes a factory improvement (ADR-0022), never an improvised
  deploy.
- First adapters: **Firebase** (Hosting, Functions, Firestore) and **Cloudflare** (Pages,
  Workers). Both have staging for free: Hosting preview channels, Pages preview deployments and
  Worker versions. Each adapter is written against the supplier's current docs, not from memory.

### 2. Publishing is a decision taken in a conversation

- The onboarding spec records `publish: local | <platform> | undecided`.
- In a Sprint Meeting or the chat, a model that hears the intent ("put it online", "show it to
  clients") proposes a `publish` op in the draft (ADR-0010). Nothing happens in the turn.
- On commit, `ProductOwnerAgent` files an **environment story** through triage (ADR-0017) with the
  platform, the environments (staging, production) and the budget. It runs in a sprint like any
  other story.

### 3. The environment story splits itself

The adapter's `requirements()` lists what must exist, and each item is either:

- **factory**: config files (`firebase.json`, `wrangler.toml`), build scripts, the first staging
  deploy and its check; or
- **founder**: anything that needs their identity or card: account, billing, API token, DNS,
  terms of service. The factory never does these for them.

Founder items are dependencies of the environment story only (ADR-0021); the rest of the sprint
goes on.

### 4. Every founder dependency is a guided card (general rule)

Whenever the factory needs the founder to act (here, and from now on anywhere else), the inbox
card carries, in pt-BR and through `audit_executive_text`:

1. why, in one sentence;
2. numbered steps with **direct links**, filled with what is known (project, account, the exact
   page that creates the token, the permissions to tick, a suggested token name);
3. a field in the dashboard where the key is pasted; it is written to the secrets file and never
   passes through a model;
4. a **"pronto, verificar"** button: `verify(step)` tests it now and says in plain words what is
   still missing.

The steps come from `guide(step)`, a template kept in the adapter, because console links and
permission names change and a wrong step costs the founder's trust. For an unexpected provider
error, a model may draft steps from researched docs; the card marks them as guidance and the check
is still code. This reuses the wizard/doctor machinery of ADR-0012.

### 5. Staging by default, production by acceptance

- **Credentials per environment, least scope**: a Firebase service account with only the deploy
  role per project (separate staging and production projects preferred) and a Cloudflare API token
  limited to the product's resources. Values live in the secrets files; the adapter injects them
  into the subprocess environment only. A model never sees them (`is_protected` already blocks
  `.env`).
- **After a merge**, `deploy("staging")` runs automatically. The Inspector's smoke and visual
  checks (ADR-0030) run against the **staging URL**.
- **Production** asks the founder in the inbox ("versão X está no ar em teste, verificada;
  publicar?"). On acceptance, code calls `promote()` on **the same artefact** that passed staging,
  with no rebuild (`hosting:clone`, Worker versions). Without a recorded acceptance, `promote`
  refuses: authority in code, as for the merge (ADR-0008).
- **Data migrations** (Firestore rules and indexes, D1, KV schemas) are never automatic at first:
  they need an explicit acceptance per migration, because not all of them can be undone.
- **Rollback** is proposed in the inbox when the post-deploy check gets worse. An automatic
  rollback is a later opt-in.

### 6. Periodic upkeep

- A **health register** per factory (store table): source (log, error, uptime, cost), finding,
  severity, first and last seen, and the story it became, if any.
- **Lazy trigger**: on `loompa run` or the dashboard's line start, when the last check is older
  than `upkeep.every_days` (default 7); and right after every deploy.
- The Deployer reads `logs()` and `status()` with read-only credentials; a cheap-tier model groups
  and de-duplicates against the register and proposes what to do.
- **Findings enter through the PO's triage**, never as cards written by the Deployer. A severe one
  also sends a plain pt-BR inbox note.

### 7. Finance watches the hosting bill

- On the same trigger, `costs()` and `budget()` are read and added to the token spend already
  tracked (ADR-0015, ADR-0024), against the product's budget.
- A projection over the ceiling is an inbox note with a suggestion (plan, cache, region, an
  expensive job).
- Firebase's Blaze plan has **no hard cap**, only budget alerts. The environment story always
  includes a budget alert set **at the provider** as a safety net independent of the factory. An
  optional billing cut-off (it takes the service down) is offered only with that consequence
  spelled out.

## Consequences

- A product can go from local to published in one meeting decision plus guided founder steps,
  and the factory keeps it deployed, checked and costed without a daemon.
- Production can only change through the founder's acceptance; the model never holds a write
  credential.
- New code: `loompa/platforms/` (adapters + fakes), a Deployer profile in `agents/toolbox.py`
  for deploy and read-only logs, the `publish` op, the health register, the guided-card machinery
  and the dashboard key field. Everything works in the dry-run with fake adapters.
- New model-facing text (the `publish` intent, the upkeep triage prompt) goes through the bench
  (ADR-0034) before it is adopted.
- Open before building: each provider's billing API coverage (Cloudflare's especially) and how
  much of Firebase's logs its MCP server exposes. These are read in the supplier docs at the start
  of each adapter.
