from loompa.config import default_config
from loompa.finance import CostTracker, UsageRecord
from loompa.store import Store


def make() -> tuple[Store, CostTracker]:
    cfg = default_config()
    cfg.budget.monthly_cap_usd = 1.0
    store = Store(":memory:")
    return store, CostTracker(store, cfg, "f")


def test_cost_computation_uses_pricing_table():
    _, t = make()
    # deepseek-chat: 0.27 in / 1.10 out / 0.07 cached per 1M
    assert t.cost_of("deepseek-chat", 1_000_000, 0) == 0.27
    assert t.cost_of("deepseek-chat", 0, 1_000_000) == 1.10
    assert t.cost_of("deepseek-chat", 1_000_000, 0, cached_tokens=1_000_000) == 0.07
    assert t.cost_of("unknown", 1_000_000, 1_000_000) == 4.0  # default row


def test_record_updates_story_and_budget_alerts():
    store, t = make()
    store.upsert_story({"id": "S-1", "factory": "f", "title": "x", "stage": "DEV"})
    cost = t.record(
        UsageRecord(
            agent="w",
            role="worker",
            provider="deepseek",
            model="deepseek-chat",
            tier="tier2",
            input_tokens=500_000,
            output_tokens=500_000,
            story_id="S-1",
        )
    )
    assert cost == 0.685 and store.get_story("S-1")["cost_usd"] == 0.685
    st = t.status()
    assert st.month_cost_usd == 0.685 and st.today_cost_usd == 0.685 and not st.warn
    t.record(
        UsageRecord(
            agent="a",
            role="architect",
            provider="deepseek",
            model="deepseek-reasoner",
            tier="tier1",
            input_tokens=200_000,
            output_tokens=50_000,
            story_id="S-1",
        )
    )
    st = t.status()
    assert st.warn and not st.exhausted and st.remaining_usd > 0
    msg = t.maybe_alert()
    assert (
        msg is not None
        and msg.kind == "finance"
        and "orçamento mensal" in msg.title
        and msg.executive_audit() == []
    )
    assert t.maybe_alert() is None  # only once per month
    t.record(
        UsageRecord(
            agent="a",
            role="architect",
            provider="deepseek",
            model="deepseek-reasoner",
            tier="tier1",
            input_tokens=0,
            output_tokens=200_000,
        )
    )
    assert t.status().exhausted


def test_reports_and_suggestions():
    store, t = make()
    t.record(
        UsageRecord(
            agent="w",
            role="worker",
            provider="p",
            model="deepseek-chat",
            tier="tier2",
            input_tokens=900_000,
            output_tokens=10_000,
            story_id="S-1",
        )
    )
    rep = t.daily_report()
    assert (
        rep["totals"]["calls"] == 1
        and rep["by_role"][0]["key"] == "worker"
        and rep["by_story"][0]["key"] == "S-1"
    )
    summary = t.executive_daily_summary()
    assert "Gasto de hoje" in summary and "S-1" in summary
    sug = t.suggestions()
    assert any("worker" in s for s in sug) and any("paginar" in s for s in sug)
