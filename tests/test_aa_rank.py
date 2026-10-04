"""Plan 9.4 revised (04/10): model suggestion from Artificial Analysis alone, per (model, effort)."""

from __future__ import annotations

from loompa.llm.aa_rank import free_offers, parse, rank


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
