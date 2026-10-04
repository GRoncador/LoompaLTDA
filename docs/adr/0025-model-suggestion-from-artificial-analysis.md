# ADR-0025: Model suggestion from Artificial Analysis, per model and effort

**Status:** accepted · **Date:** 2026-10-04 · **Decided with:** the Founder

## Context
The suggestion of ADR-0011 ranked OpenRouter's catalogue on the three indices OpenRouter passes on
from Artificial Analysis (AA). The Founder found it weak. Plan 9.4 measured the AA free API
(690 entries, one call): exact name matching against OpenRouter covers 71 of 464 models, a
translation rule 218 but lands on the wrong variant in 9 of the 120 it can check, because AA rates
every *effort* of a model as its own entry (Claude Opus 5.5: 57.6 at Max, 42.3 at Low) and the bare
slug does not always mean the same effort. AA also exposes 17 benchmarks, speed and time to the first
token, where OpenRouter passes three indices.

## Decision
1. **The suggestion reads AA alone** (`llm/aa_rank.py`): every model AA rates, with no regard for the
   factory's keys or for OpenRouter. Finding a route and setting its key is the founder's job; the
   page shows an OpenRouter id when one is known, only as help.
2. **A pick is (model, effort).** AA's effort tags map to the Loompa's (`Xhigh` → `max`,
   `Non-reasoning` → `none`, a bare "Reasoning" → the provider's default).
3. **Composites per cluster on AA's current suite.** Since 2026-08 a new model gets HLE, LCR,
   SciCode and Terminal-Bench v4.0 (tau-banking and GPQA for about half), and no IFBench, tau2,
   LiveCodeBench or Terminal-Bench Hard; a profile on the old suite ranked April models above their
   September successors. Each benchmark is scaled against the best score (best = 100); a missing
   one redistributes its weight; below 60% of the weight the model is left out and counted.
   - Engineering (Worker, Inspector): Terminal-Bench v4.0 45, SciCode 25, LCR 15, tau-banking 15.
   - Strategy (Master, Architect, Spec, PO, Analyst): HLE 35, LCR 30, tau-banking 20, Terminal-Bench 15.
   - Routine (Deployer, support): tau-banking 35, HLE 25, LCR 20, SciCode 20.
   - The single cluster mixes the three by each cluster's share of the factory's calls in 30 days
     (contas: 84% engineering), 65/30/5 without data. Not AA's intelligence index alone.
   GPQA is left out (0.91 with LCR) and AA's coding index is not mixed with its parts (it is
   Terminal-Bench v2.1).
4. **Tiers as before:** tier 1 the best within the price ceiling; tier 2 at least the floor (75%) of
   tier 1's best, each model at its best-value effort; tier 3 the best a provider offers free (the
   OpenRouter `:free` list, the only provider data read). AA's price 0 means unknown, never free.
   Five per tier.
5. **Effort per slot, hybrid** (`ModelCandidate.effort_mode`): a slot filled from the suggestion is
   `fixed` and every call runs at its effort, because the rating is only true there; a slot set by
   hand stays `auto` (ADR-0016's per-task policy). Escalation still works by tier and a cut answer
   still gets more room. `none` turns thinking off; OpenRouter takes it where the model allows it.
6. **Where it is used, not a page apart (Founder, 2026-10-04):** a 💡 in the header of each tier of
   each cluster in "Modelos e papéis" opens that cell's suggestions in a popup (closed by a click
   outside), with the date of the data; "usar" fills the cell like any edit of the screen. Every
   cluster's ranking is computed whatever the factory uses. The list comes from a weekly cache
   refreshed at engine start; an inbox note tells when the first picks of a tier change. Nothing
   changes by itself. A "Benchmark de modelos" tab, last in the settings, shows the whole ranking
   (the four clusters, ten per tier; the popup shows five) with the tier-1 price ceiling (moved
   there from the budget tab) and the refresh. The OpenRouter suggestion buttons left the models
   screen; "Consultar os provedores" took their place and also renews the OpenRouter list the
   model picker reads.
7. The house score (plan 9.5) is shown next to each pick: on the Worker it disagreed with AA
   (GLM 5.3 Flash: Terminal-Bench 0.33, 62% of tasks done here; MiMo-V2.6-Flash: 0.23, 96%).

## Consequences
- The OpenRouter suggestion of ADR-0011 is off the screen (its endpoints stay); its catalogue remains
  for picking models by hand.
- AA's price is per token: a high-effort variant spends more reasoning tokens, which AA does not
  report, so its cost-benefit is optimistic. The page says so.
- Attribution to Artificial Analysis is shown; the free API allows 100 requests a day.
