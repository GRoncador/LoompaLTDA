# ADR-0024: Cost controls: tiers by task, a cap per story, the makers' own keys

Date: 2026-10-03 · Status: accepted (the founder's cost review of 2026-10-03; builds on ADR-0016's
reasoning policy and the model matrix of Fase 2b)

## Context

The founder found the factory more expensive than a top-tier coding subscription and asked where the
money went. OpenRouter billed US$ 11.67 in the last week. The calls with a reported cost in October
(US$ 7.78 in `contas` and `tamagotchi-retro`, ~95% of the bill) showed:

- Tier 1 was 59% of the spend from 33% of the calls (mimo-v2.6-pro alone 40%). The Worker escalated
  to tier 1 was ~33%, and that step pays for itself: in `contas`, escalating delivered 8 of 9 stories.
  The rest was the Architect planning STANDARD stories (10%), the preflight, plan amendments and the
  Master, all on tier 1 by role. Checking or wording what is already decided does not need that tier.
- One cancelled story (Tamagotchi S-006) cost 17%. It planned Python persistence in a JavaScript
  product, then switched language on the re-plan.
- Reasoning tokens were ~38% of the cost and 76% of the Worker's output.
- When OpenRouter switched the upstream provider mid-task (10% of the calls), the prompt-cache hit fell
  from 73% to 27%. A cache hit on MiMo costs ~1% of a miss.
- Tier 1's one-per-vendor picks put tier-2 models behind the leader: 210 of 338 escalated Worker calls
  ran on glm-5.3-flash.
- The small roles (Master's wording, Deployer, Ops) are under 2% of the spend. Free tiers save cents
  there, and the Worker's volume (~1,000 calls/day) would eat a free quota whole.

The Xiaomi "Token Plan" (US$ 16 a month) was considered and rejected. Its terms limit the quota to
coding tools and forbid "custom application backends", which is what the factory is. This is the same
line drawn on 2026-09-30 for subscription OAuth: official pay-as-you-go keys only.

## Decisions

1. **Tiers by task** (`config/schema.py` `ROLE_TASKS`). A declared task's tier wins over the story's
   complexity lift. `RoleTask.complex_tier` lets one task climb only on a COMPLEX story, and SIMPLE
   never runs above tier 2.
   - `architect.plan`: tier 2, tier 1 when COMPLEX. The re-plan of an escalated story stays on
     tier 1, as part of that attempt.
   - Tier 2: `architect.preflight`, `.amend`, `.lesson`, `product_owner.spec_review` and
     `worker.self_check`. The self-check was on tier 3 for a day: a free model's false "missing"
     costs a whole Worker round (~17 tool calls), and it is the one task that reads product code.
   - Tier 3: `master.wording`, `master.exec_options` and `deployer.summary`.
   - The Master's role default is tier 2.
   The founder's per-task choice in settings wins over all of this.
2. **Free tier by choice falls back to paid.** A tier-3 call chosen by role or task makes one pass over
   the free models without waiting on their rate limits; then tier 2 answers. A spent budget with
   `on_exceed: tier3` stays free-only, because that is the founder choosing free.
3. **Config revision.** `LoompaConfig.revision` and `config/store.py:_upgrade`: a config.yaml written
   before revision 1 drops the defaults this ADR changed when they appear word for word. A founder's own
   value stays, and a choice made after the upgrade is never undone.
4. **A cap per story** (`budget.story_cap_usd`, US$ 1.00, 0 = off; `finance/story_cap.py`). Past it
   without finishing, the story stops, either on entering `dev` or between the Worker's tasks with the
   last one committed. The founder then continues (another cap from what it has spent), sends it back to
   the backlog, or cancels it.
5. **The plan's language is checked** (`stack_check.py`). After the plan, its source files are compared
   with `stack.languages`. JS and TS count as one family; SQL and shell are neutral. A mismatch re-plans
   once with the mismatch named. A second one asks the founder (blocked reason `stack`).
6. **The makers' own keys, in front of OpenRouter (BYOK).** These providers each map the OpenRouter
   ids they serve to their own (`ProviderConfig.byok_models`): `xiaomi`, `deepseek`, `zai`, `alibaba`
   (Model Studio intl), `moonshot` and `minimax`. With a maker's key set, `ModelRouter.direct_first`
   puts its API before each matching OpenRouter candidate, and OpenRouter answers when it fails. With
   no key, nothing changes. A call with an explicit effort skips a maker whose API was not confirmed to
   take one (`takes_effort: false`), so the effort policy of ADR-0016 holds. Prices come from the
   makers' pages; DeepSeek's peak price is kept so the meter never under-counts.
7. **Tier 1 holds only models rated above tier 2's best** (`catalog._decision_tier`). If fewer than two
   remain, tier 2's best follows as the last resort.
8. **Measured, not argued.** `loompa costs` counts from `usage` and the events:
   - spend by role, tier, model and story;
   - the cache lost to provider switches;
   - the escalation rescue rate;
   - the cost cap, the language check and the free tier at work.
   An experiment runs alongside: `schedule.effort_ab_low_share` (0.5) of the Worker's main tasks that
   think at the default today (a later attempt, or tier 1) run at `low`, going back to the default on
   the first sign of trouble. The arm is a hash of story, attempt and task, and the report compares the
   arms. Whoever decides whether `low` stays reads it there.

## Consequences

- Tamagotchi S-006 type of waste pauses at US$ 1 or is re-planned before the first commit.
- Nothing calls a maker directly until the founder pastes its key, and a key from a coding or token
  plan must not be used here.
- Existing factories pick up decisions 1–4 on their next load (revision 1). Their tier-1 lists were
  edited by hand on 2026-10-03 to mimo-v2.6-pro with mimo-v2.6-flash behind it. A later recommendation
  applies decision 7.
- The experiment changes behaviour for half of the later attempts. Set the share to 0 to stop it.
