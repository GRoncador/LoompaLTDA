"""`loompa models sync`: rank the OpenRouter catalogue, propose through the inbox, apply on
approval and never mid-sprint (ADR-0011). The catalogue is a hand-made payload with the shape of
the real `/api/v1/models` (prices as per-token strings, benchmarks under `artificial_analysis`)."""

from __future__ import annotations

import json
from datetime import date
from typing import Any

import httpx
import pytest

from loompa.comms import FounderAnswer, MessageStatus
from loompa.config import apply_preset, default_config
from loompa.config.schema import ModelCandidate
from loompa.engine import Scheduler, Stage
from loompa.factory import Factory
from loompa.llm.catalog import (
    CatalogError,
    Policy,
    apply_proposal,
    build_proposal,
    fetch_catalog,
    parse_catalog,
    rank,
)
from loompa.models_sync import ModelSync, excluded_report, plan
from test_engine import factory, make_ctx, seed_story  # noqa: F401

TODAY = date(2026, 9, 20)
POLICY = Policy(tier1_ceiling=5.0, tier2_floor=0.8, picks=2)


def entry(
    model_id: str,
    prompt: float,
    completion: float,
    *,
    coding: float | None = 70.0,
    agentic: float | None = 50.0,
    tools: bool = True,
    mandatory: bool = False,
    efforts: list[str] | None = None,
    context: int = 200_000,
    expires: str | None = None,
    cache: float | None = None,
    alias: str | None = None,  # slug of the model an alias points to
    outputs: tuple[str, ...] = ("text",),
) -> dict[str, Any]:
    """One catalogue row; prices are USD per 1M tokens here and per token, as strings, in the payload."""
    pricing = {"prompt": str(prompt / 1e6), "completion": str(completion / 1e6)}
    if cache is not None:
        pricing["input_cache_read"] = str(cache / 1e6)
    row: dict[str, Any] = {
        "id": model_id,
        "name": f"{model_id.split('/')[0].title()}: {model_id.split('/')[1]}",
        "context_length": context,
        "architecture": {"input_modalities": ["text"], "output_modalities": list(outputs)},
        "pricing": pricing,
        "supported_parameters": ["max_tokens", *(["tools"] if tools else [])],
        "expiration_date": expires,
        "reasoning": {"mandatory": mandatory, "supported_efforts": efforts or []},
    }
    if coding is not None or agentic is not None:
        row["benchmarks"] = {
            "artificial_analysis": {"coding_index": coding, "agentic_index": agentic}
        }
    if alias:
        row["alias_target"] = {"slug": alias, "name": alias.title()}
    return row


CATALOG = [
    entry("a/best", 10, 30, coding=80, agentic=60),  # quality 70, $15: over the tier1 ceiling
    entry("b/strong", 2, 6, coding=76, agentic=56, cache=0.5),  # quality 66, $3
    entry("c/mid", 1, 3, coding=70, agentic=54),  # quality 62, $1.5
    entry("d/cheap", 0.1, 0.3, coding=64, agentic=52),  # quality 58, $0.15
    entry("d/cheaper", 0.05, 0.1, coding=62, agentic=52),  # quality 57, same vendor as d/cheap
    entry("e/weak", 0.01, 0.01, coding=40, agentic=40),  # below the tier2 floor
    entry("f/unrated", 0.01, 0.01, coding=None, agentic=None),  # no benchmark
    entry("b/strong:batch", 1, 3),  # a variant
    entry("g/notools", 1, 3, tools=False),
    entry("h/forced", 0.1, 0.3, coding=76, agentic=60, mandatory=True),  # cannot be limited
    entry("i/limited", 0.1, 0.3, coding=60, agentic=54, mandatory=True, efforts=["high", "low"]),
    entry("j/leaving", 0.1, 0.3, expires="2026-10-20"),
    entry("~k/alias", 1, 3, alias="x/y"),
    entry("l/short", 1, 3, context=32_000),
    entry("m/router", -1, -1),
]


def catalog(rows: list[dict[str, Any]] = CATALOG) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"data": rows}))
    )


def pin_openrouter(config):
    """The OpenRouter preset as it was before the aliases: concrete ids, one `:free` fallback."""
    ids = {
        "tier1": ["z-ai/glm-5.3", "qwen/qwen3.8-max-0902"],
        "tier2": [
            "z-ai/glm-5.3-flash",
            "deepseek/deepseek-v4-flash-0731",
            "deepseek/deepseek-v4-flash-0731:free",
        ],
    }
    config.models.tiers = {
        tier: [ModelCandidate(provider="openrouter", model=m) for m in models]
        for tier, models in ids.items()
    }
    return config


def openrouter_config():
    return pin_openrouter(default_config())


# ------------------------------------------------------------------------------ the catalogue


def test_parse_reads_per_token_prices_as_dollars_per_million_and_survives_bad_rows():
    models = parse_catalog(
        {
            "data": [
                entry("b/strong", 2, 6, coding=76, agentic=56, cache=0.5),
                "junk",
                {"id": ""},
                {"id": "x/y", "pricing": []},
            ]
        }
    )
    # a row that is not a row costs only itself
    assert [m.id for m in models] == ["b/strong", "x/y"]
    strong = models[0]
    assert (strong.input_usd, strong.output_usd, strong.cached_usd) == pytest.approx((2, 6, 0.5))
    assert strong.blended == pytest.approx(3.0) and strong.quality == pytest.approx(66)
    assert models[1].blended is None and models[1].quality is None
    with pytest.raises(CatalogError):
        parse_catalog({"data": []})


def test_fetch_reads_the_models_url_of_the_configured_provider_and_wraps_failures():
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        assert "authorization" not in request.headers  # the catalogue is public: no key leaves
        return httpx.Response(200, json={"data": CATALOG})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert len(fetch_catalog(default_config(), client=client)) == len(CATALOG)
    assert seen == ["https://openrouter.ai/api/v1/models"]
    for response in (httpx.Response(503), httpx.Response(200, text="<html>")):
        broken = httpx.Client(transport=httpx.MockTransport(lambda request, r=response: r))
        with pytest.raises(CatalogError) as err:
            fetch_catalog(default_config(), client=broken)
        assert "Error" not in str(err.value)  # readable, no exception class name


def test_every_model_left_out_is_counted_with_its_reason():
    ranking = rank(parse_catalog({"data": CATALOG}), POLICY, TODAY)
    assert dict(ranking.excluded) == {
        "variant": 1,
        "no_tools": 1,
        "unlimited_reasoning": 1,
        "expiring": 1,
        "alias": 1,
        "short_context": 1,
        "bad_price": 1,
        "unrated": 1,  # counted, not ranked as if it scored 0
    }
    assert ranking.total == len(CATALOG) and len(ranking.eligible) == 7


def test_tier1_is_best_under_the_ceiling_and_tier2_cheapest_above_the_floor():
    ranking = rank(parse_catalog({"data": CATALOG}), POLICY, TODAY)
    assert ranking.best_quality == pytest.approx(70)
    assert [m.id for m in ranking.tier1] == ["b/strong", "c/mid"]  # a/best costs too much
    # floor = 80% of 70 = 56; the cheapest above it, one per vendor: d/cheap is d/cheaper's twin
    assert [m.id for m in ranking.tier2] == ["d/cheaper", "i/limited"]
    assert "e/weak" not in {m.id for m in ranking.tier2}


def test_a_mandatory_reasoning_model_stays_when_its_effort_can_be_lowered():
    ranking = rank(parse_catalog({"data": CATALOG}), POLICY, TODAY)
    assert "i/limited" in {m.id for m in ranking.eligible}
    assert "h/forced" not in {m.id for m in ranking.eligible}


# ------------------------------------------------------------------------------- the proposal


def test_proposal_takes_the_openrouter_slot_keeps_other_providers_and_prices_the_new_models():
    config = default_config()
    apply_preset(config, "gratuito")  # gemini, groq and openrouter :free, in that order
    config.models.tiers["tier2"][2].model = "d/free:free"
    proposal = build_proposal(
        config, parse_catalog({"data": [*CATALOG, entry("d/free:free", 0, 0)]}), POLICY, TODAY
    )
    tier2 = [(c["provider"], c["model"]) for c in proposal.tiers["tier2"]]
    assert tier2 == [
        ("gemini", "gemini-3.5-flash-lite"),  # the founder's order is untouched...
        ("groq", "llama-3.1-8b-instant"),
        ("openrouter", "d/cheaper"),  # ...and OpenRouter takes the slot of its first candidate
        ("openrouter", "i/limited"),
        ("openrouter", "d/free:free"),  # a free model still on offer stays as the last fallback
    ]
    assert proposal.pricing["b/strong"] == {"input": 2.0, "output": 6.0, "cached_input": 0.5}
    assert "d/free:free" not in proposal.pricing  # free needs no price
    assert proposal.gone == ["deepseek/deepseek-r1:free"]  # in the preset, gone from the catalogue


def test_a_free_model_that_left_the_catalogue_is_dropped_and_reported():
    config = openrouter_config()
    proposal = build_proposal(config, parse_catalog({"data": CATALOG}), POLICY, TODAY)
    assert "deepseek/deepseek-v4-flash-0731:free" in proposal.gone
    assert all(not c["model"].endswith(":free") for c in proposal.tiers["tier2"])


def test_settings_of_a_model_that_stays_are_kept():
    config = openrouter_config()
    config.models.tiers["tier1"] = [
        config.models.tiers["tier1"][0].model_copy(
            update={"model": "b/strong", "reasoning_effort": "low", "max_output_tokens": 999}
        )
    ]
    proposal = build_proposal(config, parse_catalog({"data": CATALOG}), POLICY, TODAY)
    kept = next(c for c in proposal.tiers["tier1"] if c["model"] == "b/strong")
    assert kept["reasoning_effort"] == "low" and kept["max_output_tokens"] == 999


def test_an_expiring_model_in_use_is_flagged_and_a_settled_list_proposes_nothing():
    config = openrouter_config()
    config.models.tiers["tier2"][0].model = "j/leaving"
    models = parse_catalog({"data": CATALOG})
    assert build_proposal(config, models, POLICY, TODAY).expiring == ["j/leaving"]
    assert apply_proposal(config, build_proposal(config, models, POLICY, TODAY))
    assert not build_proposal(config, models, POLICY, TODAY).changed


def test_a_factory_that_does_not_use_openrouter_gets_no_proposal():
    config = default_config()
    config.models.tiers = {"tier1": [], "tier2": []}
    with pytest.raises(CatalogError):
        build_proposal(config, parse_catalog({"data": CATALOG}), POLICY, TODAY)


def test_apply_refuses_a_configuration_edited_since_the_proposal():
    config = openrouter_config()
    proposal = build_proposal(config, parse_catalog({"data": CATALOG}), POLICY, TODAY)
    config.models.tiers["tier2"][0].max_output_tokens = 1234  # the founder tuned it meanwhile
    assert apply_proposal(config, proposal) is False
    assert config.models.tiers["tier2"][0].max_output_tokens == 1234


# --------------------------------------------------------------------------------- governance


def swap_ready(factory: Factory):
    """A factory on the OpenRouter preset with a context, and the proposal a sync would make."""
    pin_openrouter(factory.config)
    factory.save()
    ctx = make_ctx(factory, dry_run=True)
    return ctx, plan(ctx.config, POLICY, client=catalog(), today=TODAY)


def tier_ids(config, tier: str) -> list[str]:
    return [c.model for c in config.models.tiers[tier]]


async def test_the_proposal_reaches_the_inbox_in_plain_words_and_changes_nothing(factory: Factory):
    ctx, proposal = swap_ready(factory)
    before = tier_ids(ctx.config, "tier2")
    msg = ModelSync(ctx).propose(proposal)
    assert msg is not None and msg.status == MessageStatus.PENDING and msg.requires_action
    assert [o.key for o in msg.options] == ["approve", "reject"]
    assert msg.executive_audit() == []
    assert "B: strong" in msg.context  # models by name, with score and price
    assert tier_ids(ctx.config, "tier2") == before


async def test_a_new_proposal_withdraws_the_one_still_waiting(factory: Factory):
    ctx, proposal = swap_ready(factory)
    sync = ModelSync(ctx)
    first, second = sync.propose(proposal), sync.propose(proposal)
    assert first is not None and second is not None
    assert ctx.store.get_message(first.id).status == MessageStatus.ARCHIVED
    assert [m.id for m in ctx.store.list_messages(factory.slug, status="pending")] == [second.id]


async def test_nothing_is_proposed_when_the_list_is_already_the_best(factory: Factory):
    ctx, proposal = swap_ready(factory)
    assert apply_proposal(ctx.config, proposal)
    assert ModelSync(ctx).propose(plan(ctx.config, POLICY, client=catalog(), today=TODAY)) is None
    assert ctx.store.list_messages(factory.slug) == []


async def test_approving_on_an_idle_factory_swaps_models_and_prices_and_saves(factory: Factory):
    ctx, proposal = swap_ready(factory)
    msg = ModelSync(ctx).propose(proposal)
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="approve"))
    assert tier_ids(ctx.config, "tier1") == ["b/strong", "c/mid"]
    assert ctx.config.price_for("b/strong").output == 6.0  # billed at its own price, not $3 default
    saved = Factory.open(factory.root).config
    assert tier_ids(saved, "tier2") == ["d/cheaper", "i/limited"]
    notes = [
        m for m in ctx.store.list_messages(factory.slug) if m.title.startswith("Modelos de IA")
    ]
    assert notes and notes[0].executive_audit() == []


async def test_rejecting_keeps_the_models(factory: Factory):
    ctx, proposal = swap_ready(factory)
    before = tier_ids(ctx.config, "tier1")
    msg = ModelSync(ctx).propose(proposal)
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="reject"))
    assert tier_ids(ctx.config, "tier1") == before


async def test_an_approval_mid_sprint_waits_for_the_work_to_end(factory: Factory):
    ctx, proposal = swap_ready(factory)
    before = tier_ids(ctx.config, "tier1")
    story = seed_story(ctx, "Página de login")  # admitted: work is in flight
    msg = ModelSync(ctx).propose(proposal)
    sched = Scheduler(ctx)
    await sched.aanswer(msg.id, FounderAnswer(option_key="approve"))
    assert tier_ids(ctx.config, "tier1") == before  # not swapped under the running story
    assert any("terminar" in m.context for m in ctx.store.list_messages(factory.slug))
    sched.close_sprints()
    assert tier_ids(ctx.config, "tier1") == before  # still running
    ctx.store.update_story(story, stage=Stage.DONE.value)
    sched.close_sprints()
    assert tier_ids(ctx.config, "tier1") == ["b/strong", "c/mid"]
    sched.close_sprints()  # and it is applied once
    assert len([m for m in ctx.store.list_messages(factory.slug) if "atualizados" in m.title]) == 1


async def test_a_configuration_edited_after_the_proposal_is_not_overwritten(factory: Factory):
    ctx, proposal = swap_ready(factory)
    msg = ModelSync(ctx).propose(proposal)
    ctx.config.models.tiers["tier1"][0].max_output_tokens = 777  # edited while the inbox waited
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="approve"))
    assert ctx.config.models.tiers["tier1"][0].max_output_tokens == 777
    assert tier_ids(ctx.config, "tier1")[0] == "z-ai/glm-5.3"
    assert any("não foi trocada" in m.title for m in ctx.store.list_messages(factory.slug))


async def test_with_nothing_deferred_nothing_happens(factory: Factory):
    ctx, _ = swap_ready(factory)
    assert ctx.store.get("models_sync:deferred") is None
    ModelSync(ctx).apply_deferred()  # nothing deferred: nothing happens, nothing is written
    assert ctx.store.list_messages(factory.slug) == []


def test_excluded_report_lists_the_biggest_reasons_first_in_words():
    proposal = build_proposal(openrouter_config(), parse_catalog({"data": CATALOG}), POLICY, TODAY)
    report = excluded_report(proposal)
    assert all(n >= 1 for _, n in report) and len(report) == 8
    assert ("sem nota nos benchmarks", 1) in report
    assert json.dumps(report)  # plain, serialisable pairs


# ------------------------------------------------------------------------------------------ cli


def test_cli_previews_then_proposes_through_the_inbox(hub, brownfield_repo, monkeypatch):
    from typer.testing import CliRunner

    from loompa.cli.main import app
    from loompa.store import Store

    runner = CliRunner()
    args = ["init", str(brownfield_repo), "--yes", "--name", "Demo", "--preset", "openrouter"]
    assert runner.invoke(app, args).exit_code == 0
    monkeypatch.setattr(
        "loompa.cli.models.plan",
        lambda config, policy: plan(config, policy, client=catalog(), today=TODAY),
    )
    shown = runner.invoke(
        app, ["models", "sync", "--factory", "demo", "--preview", "--picks", "2", "--ids", "pinned"]
    )
    assert shown.exit_code == 0 and "b/strong" in shown.stdout and "sem nota" in shown.stdout
    assert "nada foi enviado" in shown.stdout
    f = Factory.open(brownfield_repo)
    assert Store(f.paths.state_db).list_messages(f.slug) == []

    sent = runner.invoke(
        app, ["models", "sync", "--factory", "demo", "--picks", "2", "--ids", "pinned"]
    )
    assert sent.exit_code == 0 and "loompa inbox reply" in sent.stdout
    (msg,) = Store(f.paths.state_db).list_messages(f.slug, status="pending")
    assert msg.title.startswith("Nova lista de modelos")
    assert (
        tier_ids(Factory.open(brownfield_repo).config, "tier1")[0] == "~z-ai/glm-latest"
    )  # unchanged


# ------------------------------------------------------------------------- `-latest` aliases

ALIASED = [
    entry("b/strong", 2, 6, coding=76, agentic=56),  # quality 66
    entry("c/mid", 1, 3, coding=70, agentic=54),  # quality 62
    entry("d/cheap", 0.1, 0.3, coding=64, agentic=52),  # quality 58
    entry("~b/strong-latest", 1.9, 5.5, coding=None, agentic=None, alias="b/strong"),
    entry("~c/mid-latest", 1, 3, coding=None, agentic=None, alias="c/mid"),
    entry("~d/cheap-latest", 0.1, 0.3, coding=None, agentic=None, alias="d/cheap"),
    entry("~e/orphan-latest", 1, 3, coding=None, agentic=None, alias="e/gone"),  # target unknown
    entry("openrouter/free", 0, 0, coding=None, agentic=None),
]
for _row in ALIASED[3:7]:
    _row["benchmarks"] = (
        None  # what the catalogue really does: an alias has no benchmark of its own
    )


def aliased_config():
    config = default_config()
    config.models.tiers = {
        "tier1": [ModelCandidate(provider="openrouter", model="~c/mid-latest")],
        "tier2": [
            ModelCandidate(provider="openrouter", model="~d/cheap-latest"),
            ModelCandidate(provider="openrouter", model="openrouter/free"),
        ],
    }
    return config


def test_an_alias_is_rated_by_the_model_it_points_to():
    models = {m.id: m for m in parse_catalog({"data": ALIASED})}
    alias = models["~b/strong-latest"]
    assert alias.alias and alias.quality == pytest.approx(66)  # inherited from b/strong
    assert alias.label == "~B: strong-latest (hoje: B/Strong)"  # says what it points to today
    assert models["~e/orphan-latest"].quality is None  # a target that is not in the catalogue


def test_a_factory_on_aliases_is_ranked_on_aliases_only_and_a_pinned_one_on_ids():
    models = parse_catalog({"data": ALIASED})
    ranking = rank(models, POLICY, TODAY, "alias")
    assert {m.id for m in ranking.eligible} == {
        "~b/strong-latest",
        "~c/mid-latest",
        "~d/cheap-latest",
    }
    assert ranking.excluded["unrated"] == 1  # the orphan: counted, not scored 0
    assert ranking.excluded["pinned"] >= 3
    pinned = rank(models, POLICY, TODAY, "pinned")
    assert not any(m.alias for m in pinned.eligible) and pinned.excluded["alias"] == 4


def test_the_mode_follows_what_the_factory_uses_and_can_be_forced():
    models = parse_catalog({"data": ALIASED})
    assert build_proposal(aliased_config(), models, POLICY, TODAY).mode == "alias"
    assert build_proposal(openrouter_config(), models, POLICY, TODAY).mode == "pinned"
    forced = Policy(picks=2, ids="pinned")
    assert build_proposal(aliased_config(), models, forced, TODAY).mode == "pinned"


def test_an_alias_list_keeps_the_free_router_last_and_prices_every_alias_it_uses():
    config = aliased_config()
    proposal = build_proposal(config, parse_catalog({"data": ALIASED}), POLICY, TODAY)
    assert [c["model"] for c in proposal.tiers["tier2"]][-1] == "openrouter/free"
    assert "openrouter/free" not in proposal.pricing  # free needs no price
    assert proposal.pricing["~b/strong-latest"]["output"] == 5.5  # the alias's own price
    assert set(proposal.repriced) == set(proposal.pricing)  # none of them priced in this config
    assert apply_proposal(config, proposal)
    assert config.price_for("~b/strong-latest").output == 5.5  # billed by the id that was asked for
    assert build_proposal(config, parse_catalog({"data": ALIASED}), POLICY, TODAY).repriced == []


def test_a_price_that_moved_is_proposed_even_when_the_list_is_the_same():
    config = aliased_config()
    models = parse_catalog({"data": ALIASED})
    assert apply_proposal(config, build_proposal(config, models, POLICY, TODAY))
    moved = [dict(r) for r in ALIASED]
    for row in moved:
        if row["id"] == "~b/strong-latest":
            row["pricing"] = {"prompt": str(3 / 1e6), "completion": str(8 / 1e6)}
    again = build_proposal(config, parse_catalog({"data": moved}), POLICY, TODAY)
    assert again.repriced == ["~b/strong-latest"] and again.changed


def test_the_same_models_in_another_order_are_left_as_the_founder_ordered_them():
    config = default_config()
    config.models.tiers = {
        "tier1": [
            ModelCandidate(provider="openrouter", model="~c/mid-latest"),
            ModelCandidate(provider="openrouter", model="~b/strong-latest"),
        ],
        "tier2": [ModelCandidate(provider="openrouter", model="~d/cheap-latest")],
    }
    models = parse_catalog({"data": ALIASED})
    assert apply_proposal(config, build_proposal(config, models, POLICY, TODAY))
    settled = build_proposal(config, models, POLICY, TODAY)
    assert not settled.changed and [c["model"] for c in settled.tiers["tier1"]] == [
        "~c/mid-latest",
        "~b/strong-latest",  # the ranking would put b/strong first; the founder's order stays
    ]


def test_the_openrouter_preset_uses_priced_aliases_that_survive_the_config_file(tmp_path):
    from loompa.config import MODEL_PRESETS, load_config, save_config

    config = default_config()
    apply_preset(config, "openrouter")
    ids = [c.model for cands in config.models.tiers.values() for c in cands]
    assert all(m.startswith("~") or m == "openrouter/free" for m in ids)
    for model_id in ids:  # an unpriced id is billed at the generic $1/$3
        assert model_id in config.pricing, f"{model_id} has no price in defaults.yaml"
    assert MODEL_PRESETS["openrouter"].tiers["tier2"][-1].model == "openrouter/free"
    (tmp_path / ".loompa").mkdir()
    save_config(tmp_path, config)
    reloaded = load_config(tmp_path)  # `~` opens a YAML null; the ids must come back as text
    assert [c.model for c in reloaded.models.tiers["tier1"]] == [
        c.model for c in config.models.tiers["tier1"]
    ]
    assert reloaded.price_for("~z-ai/glm-latest") == config.price_for("~z-ai/glm-latest")
