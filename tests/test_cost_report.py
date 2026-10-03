from loompa.agents.worker import effort_arm
from loompa.config import default_config
from loompa.engine.state import StoryState
from loompa.finance import CostTracker, UsageRecord
from loompa.finance.cost_report import cost_report
from loompa.store import Store


def call(store_tracker, story, model, tier, served_by, inp, cached, role="worker"):
    store_tracker.record(
        UsageRecord(
            agent=role,
            role=role,
            provider="openrouter",
            model=model,
            tier=tier,
            input_tokens=inp,
            output_tokens=100,
            cached_tokens=cached,
            story_id=story,
            reported_cost=0.01 if tier == "tier1" else 0.001,
            served_by=served_by,
        )
    )


def test_the_report_counts_roles_switches_escalations_and_the_effort_arms():
    store = Store(":memory:")
    tracker = CostTracker(store, default_config(), "f")
    for sid, stage in (("S-1", "DONE"), ("S-2", "CANCELLED"), ("S-3", "DEV")):
        # the test writes the rows directly: it checks the report, not who may create stories
        store.upsert_story({"id": sid, "factory": "f", "title": sid, "stage": stage})  # noqa
        store.emit("f", "story.escalated", story_id=sid)
    call(tracker, "S-1", "x/pro", "tier1", "A", 1000, 800)
    call(tracker, "S-1", "x/pro", "tier1", "A", 1000, 800)
    call(tracker, "S-1", "x/pro", "tier1", "B", 1000, 100)  # switched: the cache was lost
    call(tracker, "S-2", "x/flash", "tier2", "A", 1000, 0)
    store.emit("f", "story.blocked", story_id="S-2", reason="persistent_failure")
    store.emit("f", "story.blocked", story_id="S-3", reason="persistent_failure")
    store.emit("f", "worker.task", effort_ab="low", ended_by="done", cost_usd=0.002, tool_calls=4)
    store.emit("f", "worker.task", effort_ab="low", ended_by="limit", raised="loop", cost_usd=0.004)
    store.emit("f", "worker.task", effort_ab="default", ended_by="done", cost_usd=0.006)
    store.emit("f", "worker.task", ended_by="done")  # outside the experiment

    r = cost_report(store, "f", days=1)
    assert r["calls"] == 4 and r["by_tier"][0]["key"] == "tier1"
    assert r["provider_switches"]["switched"] == {"calls": 1, "cache_hit": 0.1}
    assert r["provider_switches"]["stayed"]["calls"] == 1
    esc = r["escalations"]
    # one outcome per story: S-2 was cancelled after asking, S-3 stopped and asked
    assert (esc["done"], esc["cancelled"], esc["gave_up"]) == (1, 1, 1)
    assert esc["rescue_rate"] == round(1 / 3, 4) and esc["tier1_cost_usd"] == 0.03
    assert r["effort_ab"]["low"] == {
        "tasks": 2,
        "done": 0.5,
        "raised": 0.5,
        "cost_per_task": 0.003,
        "tool_calls": 2.0,
    }
    assert r["effort_ab"]["default"]["tasks"] == 1


def test_the_effort_arm_is_stable_for_an_attempt_and_splits_by_the_share():
    state = StoryState(story_id="S-9", title="t")
    assert effort_arm(state, 1, 0.0) == ""
    assert effort_arm(state, 1, 0.5) == effort_arm(state, 1, 0.5)  # a replay keeps its arm
    arms = [effort_arm(StoryState(story_id=f"S-{i}", title="t"), 1, 0.5) for i in range(400)]
    assert 150 < arms.count("low") < 250
    assert all(effort_arm(state, n, 1.0) == "low" for n in range(1, 5))
