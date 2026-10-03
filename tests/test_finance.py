from loompa.config import default_config
from loompa.finance import CostTracker, UsageRecord
from loompa.store import Store


def make() -> tuple[Store, CostTracker]:
    cfg = default_config()
    cfg.budget.cap_usd = 1.0
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
    assert st.period_cost_usd == 0.685 and st.today_cost_usd == 0.685 and not st.warn
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
        and "orçamento de IA desta semana" in msg.title
        and msg.executive_audit() == []
    )
    assert t.maybe_alert() is None  # only once per period
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


def test_finance_points_at_the_tool_that_fills_the_context():
    """STATUS token suggestion 3: input tokens were metered per call, never per tool step, so
    nothing said *what* made an agent read so much. Each tool result now records its size."""
    store, t = make()
    for _ in range(30):
        store.emit(
            "f", "tool.call", story_id="S-1", agent="Worker Loompa", tool="read_file", tokens=1000
        )
    store.emit("f", "tool.call", story_id="S-1", agent="Worker Loompa", tool="search", tokens=200)
    store.emit(
        "g", "tool.call", story_id="S-9", agent="Worker Loompa", tool="read_file", tokens=10**6
    )
    rows = store.tool_output_by("f")
    assert (rows[0]["agent"], rows[0]["tool"], rows[0]["calls"], rows[0]["tokens"]) == (
        "Worker Loompa",
        "read_file",
        30,
        30_000,
    )
    (tip,) = [s for s in t.suggestions() if "trouxe" in s]
    assert "'Worker Loompa'" in tip and "'read_file'" in tip and "~30 mil" in tip and "99%" in tip


def test_no_tool_note_while_tools_read_little():
    store, t = make()
    store.emit(
        "f", "tool.call", story_id="S-1", agent="Worker Loompa", tool="read_file", tokens=500
    )
    assert not [s for s in t.suggestions() if "trouxe" in s]


def usage(t: CostTracker, tier: str, cost_tokens: int, story_id: str, role: str = "worker"):
    t.record(
        UsageRecord(
            agent=role,
            role=role,
            provider="p",
            model="deepseek-chat",
            tier=tier,
            input_tokens=0,
            output_tokens=cost_tokens,
            story_id=story_id,
        )
    )


def test_tier1_by_role_is_not_an_escalation_and_a_recorded_lesson_ends_the_tip():
    """tamagotchi 03/10: the tip said escalations were over 40% of the cost, counting the
    Architect and the Master (tier 1 by role) after two sprints with no escalation at all."""
    store, t = make()
    usage(t, "tier1", 900_000, "S-1", role="architect")
    usage(t, "tier2", 100_000, "S-1")
    assert t.escalation_tip() == "" and not [s for s in t.suggestions() if "Constituição" in s]
    store.emit("f", "story.escalated", story_id="S-2", to_tier="tier1")
    usage(t, "tier1", 2_000_000, "S-2")  # after the escalation: this one counts
    tip = t.escalation_tip()
    assert "S-2" in tip and "S-1" not in tip and "Constituição" in tip
    store.emit("f", "constitution.lesson", story_id="S-2", rule="x")
    assert t.escalation_tip() == ""  # the lesson is already there


def test_the_budget_alert_claims_no_action_it_did_not_take():
    store, t = make()
    usage(t, "tier2", 900_000, "S-1")
    msg = t.maybe_alert()
    assert msg is not None and "prompts mais curtos" not in msg.impact
