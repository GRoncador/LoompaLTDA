from types import SimpleNamespace

from loompa.llm.catalog import _decision_tier


def m(model_id: str, score: float) -> SimpleNamespace:
    return SimpleNamespace(id=model_id, vendor=model_id.split("/")[0], score=score)


def test_tier_one_holds_only_models_rated_above_tier_two_and_keeps_a_fallback():
    """Cost review 2026-10-03: one per vendor had put tier 2's own models behind the leader."""
    ordered = [m("xiaomi/pro", 60), m("z-ai/flash", 45), m("deepseek/flash", 44)]
    value = [m("xiaomi/flash", 50), m("z-ai/flash", 45)]
    picks = _decision_tier(ordered, value, lambda x: x.score, 3)
    # the leader, then tier 2's best as the last resort: not two weaker models billed as tier 1
    assert [p.id for p in picks] == ["xiaomi/pro", "xiaomi/flash"]


def test_two_strong_models_from_different_makers_both_stay():
    ordered = [m("xiaomi/pro", 60), m("deepseek/pro", 58), m("z-ai/flash", 45)]
    value = [m("xiaomi/flash", 50)]
    picks = _decision_tier(ordered, value, lambda x: x.score, 3)
    assert [p.id for p in picks] == ["xiaomi/pro", "deepseek/pro"]
