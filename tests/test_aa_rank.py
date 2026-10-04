"""Plan 9.4 revised (04/10): model suggestion from Artificial Analysis alone, per (model, effort)."""

from __future__ import annotations

from loompa.llm.aa_rank import free_offers, parse, rank
from test_engine import factory  # noqa: F401, F811


def entry(slug: str, name: str, price: float, **bench: float) -> dict:
    return {
        "slug": slug,
        "name": name,
        "model_creator": {"slug": "acme"},
        "pricing": {"price_1m_blended_3_to_1": price},
        "evaluations": bench,
        "release_date": "2026-09-01",
    }


RAW = [
    entry("big-max", "Big (Max)", 1.0, terminalbench_v4_0=0.6, scicode=0.6, lcr=0.8),
    entry("big-low", "Big (Low)", 1.0, terminalbench_v4_0=0.3, scicode=0.5, lcr=0.7),
    entry("mid", "Mid", 0.2, terminalbench_v4_0=0.5, scicode=0.55, lcr=0.75),
    entry("cheap", "Cheap", 0.1, terminalbench_v4_0=0.2, scicode=0.3, lcr=0.4),
    entry("pricey", "Pricey (High)", 9.0, terminalbench_v4_0=0.65, scicode=0.65, lcr=0.85),
    entry("noprice", "No price", 0.0, terminalbench_v4_0=0.6, scicode=0.6, lcr=0.8),
    entry("old", "Old", 0.1, terminalbench_hard=0.9, tau2=0.9),  # only the retired suite
]


def test_each_pick_is_a_model_at_an_effort_within_the_ceiling_and_floor():
    entries = parse(RAW)
    assert {e.aa_id: e.effort for e in entries}["acme/big-max"] == "max"
    assert next(e for e in entries if e.aa_id == "acme/noprice").price is None  # 0 = unknown
    r = rank(entries, "engineering", ceiling=1.25, floor_share=0.75)
    assert [p.aa_id for p in r.tier1] == ["acme/big-max", "acme/mid", "acme/cheap"]  # one per base
    assert r.tier1[0].effort == "max" and "acme/pricey" not in [p.aa_id for p in r.tier1]
    assert r.floor == round(r.tier1[0].score * 0.75, 1)
    assert [p.aa_id for p in r.tier2][0] == "acme/mid"  # best score per dollar above the floor
    assert all(p.score >= r.floor for p in r.tier2) and "acme/cheap" not in [
        p.aa_id for p in r.tier2
    ]
    assert [p.aa_id for p in r.unpriced] == ["acme/noprice"]
    assert r.partial == 1  # the model measured only on the retired suite is counted, not ranked


def test_tier3_lists_only_models_a_provider_offers_free():
    entries = parse(RAW)
    free = free_offers(
        entries, [{"id": "acme/mid:free"}, {"id": "acme/unknown:free"}, {"id": "acme/cheap"}]
    )
    assert free == {"acme/mid": ["OpenRouter (acme/mid:free)"]}
    r = rank(entries, "engineering", free=free)
    assert [p.aa_id for p in r.tier3] == ["acme/mid"] and r.tier3[0].free_at


def test_the_single_cluster_mixes_the_three_by_the_factorys_work():
    from loompa.llm.aa_rank import PROFILES, general_weights

    w = general_weights({"engineering": 0.8, "strategy": 0.2})
    assert abs(sum(w.values()) - 1) < 1e-3
    assert w["terminalbench_v4_0"] == round(0.8 * 0.45 + 0.2 * 0.15, 4)
    assert "gpqa" not in w and all(
        "gpqa" not in p for p in PROFILES.values()
    )  # double count of LCR
    r = rank(parse(RAW), "general", mix={"engineering": 1.0})
    assert r.weights == PROFILES["engineering"] and r.mix == {"engineering": 1.0}


async def test_a_weekly_refresh_tells_the_founder_only_when_the_top_picks_change(factory):  # noqa: F811
    import json
    import time

    import respx

    from loompa.config.secrets import Secrets
    from loompa.llm import aa_rank
    from test_engine import make_ctx

    ctx = make_ctx(factory)
    ctx.secrets = Secrets({"ARTIFICIAL_ANALYSIS_API_KEY": "aa-key"})
    url = "https://artificialanalysis.ai/api/v2/data/llms/models"
    with respx.mock:
        respx.get(url).respond(json={"data": RAW})
        assert not await aa_rank.weekly(ctx)  # the first list sets the baseline
        assert not await aa_rank.weekly(ctx)  # fresh cache: nothing asked
        path = aa_rank._cache_path(None)
        cached = json.loads(path.read_text())
        cached["fetched_at"] = time.time() - aa_rank.STALE_S - 1
        path.write_text(json.dumps(cached))
        better = [*RAW, entry("star", "Star", 0.5, terminalbench_v4_0=0.9, scicode=0.9, lcr=0.9)]
        respx.get(url).respond(json={"data": better})
        assert await aa_rank.weekly(ctx)
    note = ctx.store.list_messages(ctx.slug, status="pending")[0]
    assert note.title == "A sugestão de modelos mudou nesta semana" and "Star" in note.context
    assert not note.executive_audit()
    await ctx.aclose()
