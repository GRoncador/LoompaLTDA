from loompa.comms import FounderAnswer, FounderMessage, MessageKind, MessageStatus
from loompa.store import Store


def test_story_roundtrip_and_updates():
    s = Store(":memory:")
    assert s.next_story_id("f") == "S-001"
    s.upsert_story(
        {"id": "S-001", "factory": "f", "title": "Login", "stage": "BACKLOG", "state": {"a": 1}}
    )
    story = s.get_story("S-001")
    assert story["state"] == {"a": 1} and story["priority"] == 100 and story["origin"] == "founder"
    s.update_story("S-001", stage="DEV", state={"a": 2}, attempts_tier2=1)
    story = s.get_story("S-001")
    assert story["stage"] == "DEV" and story["state"]["a"] == 2 and story["attempts_tier2"] == 1
    s.upsert_story({"id": "S-001", "factory": "f", "title": "Login 2", "stage": "DEV"})
    assert (
        s.get_story("S-001")["title"] == "Login 2" and s.get_story("S-001")["attempts_tier2"] == 1
    )
    assert [x["id"] for x in s.list_stories("f", stage="DEV")] == ["S-001"]
    assert s.next_story_id("f") == "S-002"


def test_checkpoints_and_events():
    s = Store(":memory:")
    s.upsert_story({"id": "S-1", "factory": "f", "title": "t", "stage": "SPEC"})
    s.checkpoint("S-1", "spec", "SPEC", {"x": 1})
    s.checkpoint("S-1", "plan", "PLAN", {"x": 2})
    assert s.last_checkpoint("S-1")["state"] == {"x": 2}
    assert [c["node"] for c in s.checkpoints("S-1")] == ["spec", "plan"]
    s.emit("f", "story.stage", story_id="S-1", agent="master", stage="PLAN")
    s.emit("g", "other")
    evs = s.events_since(0)
    assert len(evs) == 2 and evs[0]["payload"] == {"stage": "PLAN"}
    assert len(s.events_since(0, factory="f")) == 1
    assert s.events_since(evs[-1]["id"]) == []


def test_inbox_answer_flow():
    s = Store(":memory:")
    msg = FounderMessage(
        factory="f", story_id="S-1", kind=MessageKind.DECISION, title="Q?", context="c"
    )
    s.put_message(msg)
    assert s.list_messages("f", status="pending")[0].id == msg.id
    answered = s.answer_message(msg.id, FounderAnswer(option_key="a"))
    assert (
        answered.status == MessageStatus.ANSWERED and s.get_message(msg.id).answer.option_key == "a"
    )
    assert s.list_messages("f", status="pending") == []
    s.archive_message(msg.id)
    assert s.get_message(msg.id).status == MessageStatus.ARCHIVED


def test_usage_agents_learnings_kv():
    s = Store(":memory:")
    s.record_usage(
        factory="f",
        story_id="S-1",
        agent="w1",
        role="worker",
        provider="p",
        model="m",
        tier="tier2",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.01,
    )
    s.record_usage(
        factory="f",
        story_id="S-2",
        agent="a1",
        role="architect",
        provider="p",
        model="m2",
        tier="tier1",
        input_tokens=10,
        output_tokens=5,
        cost_usd=0.02,
    )
    tot = s.usage_totals("f")
    assert tot["calls"] == 2 and abs(tot["cost_usd"] - 0.03) < 1e-9 and tot["input_tokens"] == 110
    assert s.usage_by("role", "f")[0]["key"] == "architect"
    s.set_agent("worker-1", "worker", "WORKING", story_id="S-1", model="m")
    s.set_agent("worker-1", "worker", "IDLE")
    assert s.list_agents()[0]["state"] == "IDLE"
    s.add_learning(story_id="S-1", kind="bug", title="x")
    assert s.list_learnings()[0]["title"] == "x"
    s.set("k", "v")
    assert s.get("k") == "v" and s.get("missing", "d") == "d"
