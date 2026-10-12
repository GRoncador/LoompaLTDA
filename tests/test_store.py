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


def test_ids_follow_the_highest_number_used_not_the_count():
    """A deleted story must never hand its id out again, and S-999 is followed by S-1000."""
    s = Store(":memory:")
    for sid in ("S-001", "S-002", "S-003"):
        s.upsert_story({"id": sid, "factory": "f", "title": sid, "stage": "BACKLOG"})
    s._x("DELETE FROM stories WHERE id = ?", ("S-002",))
    assert s.next_story_id("f") == "S-004"  # the count (2) would repeat S-003
    s.upsert_story({"id": "S-999", "factory": "f", "title": "x", "stage": "BACKLOG"})
    assert s.next_story_id("f") == "S-1000"  # as text, "S-1000" < "S-999"
    s.upsert_story({"id": "S-1000", "factory": "f", "title": "y", "stage": "BACKLOG"})
    assert s.next_story_id("f") == "S-1001"
    assert s.next_story_id("other") == "S-001"  # numbered per factory
    for n in (1, 2, 3):
        s.put_sprint(
            {
                "id": f"SP-00{n}",
                "factory": "f",
                "goal": "",
                "status": "closed",
                "story_ids": [],
                "created_at": "t",
            }
        )
    s._x("DELETE FROM sprints WHERE id = ?", ("SP-001",))
    assert s.next_sprint_id("f") == "SP-004"
    s.put_sprint(
        {
            "id": "SP-999",
            "factory": "f",
            "goal": "",
            "status": "open",
            "story_ids": [],
            "created_at": "t",
        }
    )
    assert s.next_sprint_id("f") == "SP-1000"


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


def test_store_retries_when_sqlite_reports_lock(monkeypatch):
    import sqlite3

    from loompa import store as store_mod

    s = Store(":memory:")
    monkeypatch.setattr(store_mod, "LOCK_BACKOFF_S", 0.001)
    real = s._conn
    failures = {"left": 2}

    class Flaky:
        def execute(self, sql, params=()):
            if failures["left"] and sql.startswith("INSERT"):
                failures["left"] -= 1
                raise sqlite3.OperationalError("database is locked")
            return real.execute(sql, params)

        def __getattr__(self, name):
            return getattr(real, name)

    s._conn = Flaky()
    s.set("k", "v")
    assert s.get("k") == "v" and failures["left"] == 0


def test_store_gives_up_on_non_lock_errors():
    import sqlite3

    import pytest

    s = Store(":memory:")
    with pytest.raises(sqlite3.OperationalError):
        s._q("SELECT * FROM nope")


def test_patch_story_state_merges_and_keeps_the_rest():
    """The dev node only checkpoints when it ends: the progress bar reads these patches."""
    s = Store(":memory:")
    s.upsert_story(
        {
            "id": "S-001",
            "factory": "f",
            "title": "Login",
            "stage": "DEV",
            "state": {"kind": "feature", "tasks_total": 4, "tasks_done": []},
        }
    )
    s.patch_story_state("S-001", tasks_done=[1, 2])
    state = s.get_story("S-001")["state"]
    assert state["tasks_done"] == [1, 2] and state["tasks_total"] == 4
    assert state["kind"] == "feature"
    s.patch_story_state("S-404", tasks_done=[1])  # an unknown story is a no-op, not a crash


def test_story_activity_carries_what_the_loompa_says_it_is_doing():
    """The card prefers the Loompa's own sentence to the event type's generic word (11.2)."""
    s = Store(":memory:")
    s.upsert_story({"id": "S-001", "factory": "f", "title": "x", "stage": "TEST"})
    s.emit(
        "f",
        "agent.state",
        story_id="S-001",
        agent="Inspector Loompa",
        state="TESTING",
        detail="abrindo o produto no navegador",
    )
    s.emit("f", "llm.call", story_id="S-001", agent="Inspector Loompa")
    assert s.story_activity("S-001")["detail"] == "abrindo o produto no navegador"
    # the Loompa stopped: the sentence goes with it
    s.emit("f", "agent.state", story_id="S-001", agent="Inspector Loompa", state="IDLE", detail="")
    assert "detail" not in s.story_activity("S-001")


def test_story_activity_names_the_model_the_last_call_used():
    """The card says the tier it chose and the model that answered (11.2)."""
    s = Store(":memory:")
    s.upsert_story({"id": "S-001", "factory": "f", "title": "x", "stage": "DEV"})
    s.emit("f", "llm.call", story_id="S-001", agent="Worker Loompa", model="xiaomi/mimo-2.6-pro")
    s.emit("f", "tool.call", story_id="S-001", agent="Worker Loompa", tool="edit_file")
    assert s.story_activity("S-001")["model"] == "xiaomi/mimo-2.6-pro"


def test_withdrawn_message_stays_listed_but_not_pending():
    store = Store(":memory:")
    msg = FounderMessage(factory="f", title="Aprovar?", context="ctx")
    store.put_message(msg)
    store.withdraw_message(msg.id)
    assert store.get_message(msg.id).status == MessageStatus.WITHDRAWN
    assert store.list_messages("f", status="pending") == []
    assert [m.id for m in store.list_messages("f", exclude="archived")] == [msg.id]
    store.archive_message(msg.id)
    assert store.list_messages("f", exclude="archived") == []


def test_the_spec_writer_is_the_analyst_in_an_older_database(tmp_path):
    """ADR-0045: a factory from before kept a "Spec Loompa" row, a second figure in the office
    beside the Analyst; on open it is gone and its spending is the Analyst's, the role apart."""
    path = tmp_path / "state.db"
    old = Store(path)
    old.set_agent("Spec Loompa", "product", "IDLE")
    old.set_agent("Analyst Loompa", "analyst", "IDLE")
    for agent, role in (("Spec Loompa", "product"), ("Analyst Loompa", "analyst")):
        old.record_usage(
            factory="f",
            agent=agent,
            role=role,
            provider="p",
            model="m",
            tier="tier2",
            cost_usd=0.01,
        )
    old.close()

    s = Store(path)
    assert [a["name"] for a in s.list_agents()] == ["Analyst Loompa"]
    assert [r["key"] for r in s.usage_by("agent", "f")] == ["Analyst Loompa"]
    assert sorted(r["key"] for r in s.usage_by("role", "f")) == ["analyst", "product"]
