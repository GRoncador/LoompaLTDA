# ADR-0011: `loompa models sync` — rank the OpenRouter catalogue, propose through the inbox

Date: 2026-09-20 · Status: accepted (the "proposed, not built" item of `docs/STATUS.md`; Fase 6 of `docs/PLANO-2026-09.md`)

## Context

The OpenRouter preset (ADR-0002 addendum) was chosen by hand from `/api/v1/models` on 2026-09-19.
Models come and go: on 2026-09-20 the two free DeepSeek ids the `gratuito` preset still names are
no longer in the catalogue, and ten models carry an `expiration_date`. The catalogue is public and
carries what a ranking needs: prices, `supported_parameters`, `reasoning` limits and Artificial
Analysis indices (`coding_index`, `agentic_index`, `intelligence_index`). The founder asked for a
cost/benefit refresh with two conditions: he approves it in the inbox, and it never swaps models in
the middle of a sprint, because the prompts are tuned for the model in use.

The catalogue on that day: 447 models; 378 take `tools`; 185 of those have a benchmark; 31 force
reasoning and expose no effort to lower; 96 ids are `:batch`/`:free` variants; 18 are `~aliases`.

## Decisions

### 1. Nothing is dropped silently, and an unrated model is not scored 0

The open question was the ~44% of tool-capable models with no benchmark. A filter like
`coding or 0` removes them without a word, and a ratio would rank the cheapest of them first.
They are **left out and counted** (`unrated`), and every other exclusion is counted with its
reason (alias, variant, no tools, not text, invalid price, short context, expiring, reasoning
that cannot be limited). `loompa models sync` prints that list on every run.

### 2. A model with mandatory reasoning must accept a low effort

What broke tier1 was `z-ai/glm-5.3` spending the whole output budget thinking (ADR-0002 addendum,
`reasoning_effort`). The chat turn asks for `"low"`; a model that forces reasoning and lists no
`low`/`minimal` in `reasoning.supported_efforts` cannot honour it, so it is excluded. Models where
reasoning is optional are unaffected.

### 3. Tier1 is "best under a price ceiling", tier2 is "cheapest above a quality floor"

Quality is the mean of `coding_index` and `agentic_index` (what a Worker and a tool-using planner
do). Cost is blended 3 input : 1 output per 1M tokens (a tool loop re-reads its context).

* tier1: highest quality with blended cost ≤ `--tier1-ceiling` (default US$ 5).
* tier2: lowest cost among models with quality ≥ `--tier2-floor` × the best in the catalogue
  (default 0.80).

A plain quality/price ratio was rejected: it always puts the cheapest acceptable model first, in
both tiers, and the founder values developer quality over cost. The defaults were read off the
real catalogue: tier1 lands on Qwen3.8 Max, Grok 4.6 and GLM 5.3 (the preset's own tier1 is in
it), and tier2 keeps GLM 5.3 Flash, the model validated live. Both are options, not constants.
Each tier gets at most one model per vendor (`--picks`, default 3), so a fallback is not the same
vendor having the same bad day.

### 4. It rewrites only the OpenRouter slot, and keeps the rest

Candidates of other providers stay exactly where they are; the new OpenRouter block takes the
place of the first OpenRouter candidate. A `:free` candidate still in the catalogue stays as the
last fallback, one that left is dropped and reported. Per-candidate settings (temperature,
`max_output_tokens`, `reasoning_effort`) survive when the model stays. The new models' prices go
into `pricing` in the same step: without them `price_for` falls back to the generic $1/$3 and
would bill a $0.09 model twelve times over. A factory that uses no OpenRouter model gets no proposal.

### 5. Governance: inbox decision, never mid-sprint, never over an edit

`ModelSync.propose` posts a `DECISION` (`approve` ★ / `reject`) from the Ops Loompa, in plain
pt-BR, models by name with score and price, and stores the proposal in `kv` under the message id.
A newer proposal withdraws (archives) the one still waiting. Nothing changes until the answer:

* `Scheduler._apply_answer` hands every answered message to `ModelSync.on_answer`, which acts only
  on ids it stored, so the dashboard and `loompa inbox reply` behave the same.
* If work is in flight (a running sprint, or any story between backlog and done — a story waiting
  on the founder counts, it resumes on whatever is configured), the approval is kept and an INFO
  note says so; `Scheduler.close_sprints` applies it once nothing is in flight.
* The proposal carries the tiers it was made from. If they differ at apply time, nothing is
  written and the founder is told to ask for a new proposal.

The command is on demand. Running it weekly is a cron/launchd line (the recipe belongs to Fase 6);
it is idempotent, and posts nothing when the current list is already the answer.

### 6. `-latest` aliases (addendum, 2026-09-20)

The OpenRouter preset now names `~vendor/model-latest` aliases (`~z-ai/glm-latest`,
`~x-ai/grok-latest`, `~openai/gpt-sol-latest`, `~z-ai/glm-flash-latest`, `~openai/gpt-luna-latest`,
`~google/gemini-flash-latest`) and `openrouter/free` as the last tier2 fallback, so a version that
leaves the catalogue cannot leave a tier with dead ids. What the real catalogue and a live run showed:

* An alias has its own price and reasoning limits but **no benchmark**: it is rated by the model it
  points to today (`alias_target`). A first version of the ranking found zero eligible aliases
  because of that.
* The cost tracker bills the id that was *asked for*, not the one that answered, so every alias needs
  its own `pricing` entry (`defaults.yaml`); an unpriced alias is billed at the generic $1/$3.
  Sync therefore also proposes a price update when the price of an id in use moved (`repriced`), even
  if the list is the same.
* The mode follows the factory: aliases in the config → the ranking considers aliases only;
  otherwise concrete ids only (`--ids alias|pinned|auto` overrides). Same models in another order
  are left as the founder ordered them. `openrouter/free` is kept like a `:free` model.
* Verified live on 2026-09-20: each alias answered a text and a tool call and resolved to the
  expected model (`served=`), and the project's four `live` tests passed 4/4 on `~z-ai/glm-flash-latest`
  (tier2) and on `~z-ai/glm-latest` (tier1). `openrouter/free` answers with a different free model
  per request, so it is a last resort, not a tuned choice.

**The trade.** An alias removes the loud failure (a retired id answers 404 and the router falls to
the next candidate) and adds a silent one: the model behind it can change without the approval
§5 requires for a swap, and the prompts are tuned for the model in use. The guard is `AliasWatch`
(`models_sync.py`): it remembers, per alias, the model last seen behind it and posts one INFO note
per change ("O modelo por trás de um apelido mudou": before, now, which tiers use it). It hears
about a move from two places, which share one memory (`alias_seen:<alias>` in `kv`) so a move is told
once: the model that answers (`response.model`, through the router's `on_call` hook) and the
catalogue's `alias_target` (`loompa models sync` checks it even when the list is unchanged, so a
weekly cron run announces a move before a call would). The first sight is a baseline, not news; the
free router rotates by design and concrete ids are not aliases, so both are ignored. It only tells:
the swap has already happened by then.

## Consequences

* The ranking depends on Artificial Analysis indices as OpenRouter republishes them. A model can
  be excellent and unrated; it will not show up until it has a score, and the count is visible.
* The new picks of a proposal are not validated live. The way to check one is the existing live
  test: `uv run pytest --live -m live --live-provider openrouter --live-model <id>`.
* `apply_preset` now copies the candidates it hands to a config; before, every factory shared the
  preset's mutable objects.
* Not done: a guard for an alias that moves (see §6), a dashboard screen for the proposal (the inbox card is enough to decide), and
  syncing providers other than OpenRouter.
