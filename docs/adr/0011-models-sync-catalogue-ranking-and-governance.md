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

The command is on demand, and `loompa schedule` prints the cron/launchd recipe that runs it every
month (`--sync-day`, default the 1st at 09:00). Monthly, not weekly: a swap disturbs tuned prompts and a
person approves each proposal anyway. It is idempotent, and posts nothing when the current list is
already the answer.

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
monthly scheduled run announces a move before a call would). The first sight is a baseline, not news; the
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

## Amendment, 2026-09-21: the founder decides, and nothing swaps itself

Three things in this ADR quietly assumed that the *factory* should keep its model list fresh. A
run through the real catalogue with a founder watching changed all three.

### The ranking never proposes a `-latest` alias

§6 made aliases a first-class mode, on the argument that an id retiring is a loud failure an alias
avoids. The cost of that is a model — and a price — changing with nobody approving it, which is
exactly what §5 says must not happen, and which the budget cannot see coming. The defence against a
retired id is already in place and is cheaper: three fixed candidates per tier, one per maker, plus
a router that falls through on the first error. So `exclusion_reason` now rejects every alias, the
`--ids alias|pinned|auto` flag and `detect_mode` are gone, and the shipped matrix names fixed ids.

Aliases stay *visible*: the full catalogue the dashboard reads lists all 446 models with `alias`,
`alias_target` and the reason each one is out of the ranking, with a filter for them. A founder who
wants that behaviour adds one by hand and `AliasWatch` (§6) still reports every move. Accepting a
suggestion replaces it — a suggestion is a list of fixed ids by construction.

### There is no monthly refresh

The scheduled `loompa models sync` is removed (`loompa schedule` now writes one job, the nightly
cycle). A swap disturbs prompts tuned for the model in use, so it happens when a person asks for
it, on the settings screen or from the CLI. What genuinely needs watching needs no clock:

* **a price that went up** is noticed when the catalogue is next read, by comparing the blended
  price against what the factory is billing at (`ModelWatch.check_prices`, one note per model, a
  rise under 10% is rounding, not news); and
* **a model that is not there any more** announces itself on the first call that fails
  (`ModelWatch.model_gone`, one note per id, cleared when it answers again).

That second one needed a correction: a 404 is the documented answer, but OpenRouter returns **400**
with `{"error":{"message":"x/y is not a valid model ID"}}` — verified against the live API on
2026-09-21 — so the status check alone never fired. `model_not_found` (`llm/providers.py`) reads
both shapes, the router skips the candidate without retrying a name that cannot improve, and the
per-model connection test on the settings screen says which model the provider does not have.

### The ceiling belongs to the budget, and the budget is a week

`tier1_ceiling` defaults to **US$ 1.25** and lives next to the cap, because it only means anything
against it: a quarter of a US$ 5 week, roughly 4M tokens. `BudgetConfig` is a period (weekly by
default) and says what to do when the cap is reached — pause, or put every role on the free tier.

### Also

* Model presets are gone (`config/presets.py`, `loompa providers preset`, `loompa init --preset`).
  They existed to arrange one key that reaches every model; a factory now ships that way.
* A cluster's score is computed for all four clusters at once (`general` is the plain mean, used
  when `models.clusters_enabled` is off) and rounded at the source — a raw mean reached the screen
  as `63.800000000000004`.
