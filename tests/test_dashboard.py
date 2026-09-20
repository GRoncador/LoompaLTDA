"""Dashboard API tests (dry-run engine, no network)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import git
from loompa.dashboard.app import create_app
from loompa.factory import bootstrap_factory

PYTEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider'


@pytest.fixture
def client(git_repo: Path, hub):
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    git("add", ".", cwd=git_repo)
    git("commit", "-qm", "test: base", cwd=git_repo)
    f = bootstrap_factory(git_repo, name="Demo HQ", store=hub).factory
    f.config.quality.test_command = PYTEST_CMD
    f.config.quality.lint_command = ""
    f.save()
    app = create_app(dry_run=True, run_engine=False, store=hub)
    with TestClient(app) as c:
        yield c


def test_factories_and_overview(client: TestClient):
    r = client.get("/api/factories")
    assert r.status_code == 200 and r.json()["active"] == "demo-hq" and r.json()["dry_run"] is True
    ov = client.get("/api/factories/demo-hq/overview").json()
    assert ov["factory"]["name"] == "Demo HQ" and ov["factory"]["engine"] is False
    assert [c["key"] for c in ov["columns"]] == [
        "BACKLOG",
        "SPEC",
        "DEV",
        "TEST",
        "AWAITING_FOUNDER",
        "DONE",
    ]
    assert len(ov["agents"]) >= 10 and all(
        a["room"] in ("dev", "meeting", "qa", "lounge") for a in ov["agents"]
    )
    assert ov["finance"]["cap_usd"] == 30.0 and ov["inbox"] == []
    assert client.get("/api/factories/nope/overview").status_code == 404


def test_meeting_run_inbox_flow(client: TestClient):
    r = client.post("/api/factories/demo-hq/meeting", json={"goals": "Tela de login; Exportar CSV"})
    assert r.status_code == 200 and [s["title"] for s in r.json()["stories"]] == [
        "Tela de login",
        "Exportar CSV",
    ]
    ov = client.get("/api/factories/demo-hq/overview").json()
    assert len(ov["columns"][0]["stories"]) == 2 and ov["sprint"] is None
    # the backlog waits for a sprint: nothing runs until the founder starts one
    sprint = client.post("/api/factories/demo-hq/sprints/start", json={"run": False}).json()
    assert sprint["id"] == "SP-001" and sprint["status"] == "running"
    assert sprint["story_ids"] == ["S-001", "S-002"] and sprint["progress"]["total"] == 2
    assert (
        client.post("/api/factories/demo-hq/sprints/start", json={"run": False}).status_code == 409
    )
    ov = client.get("/api/factories/demo-hq/overview").json()
    assert ov["sprint"]["id"] == "SP-001" and ov["columns"][0]["stories"] == []
    # run the engine until the stories await the founder
    assert client.post("/api/factories/demo-hq/engine/start").json()["engine"] is True
    import time

    for _ in range(100):
        inbox = client.get("/api/factories/demo-hq/inbox").json()
        if len([m for m in inbox if m["kind"] == "delivery"]) == 2:
            break
        time.sleep(0.2)
    else:
        pytest.fail("engine did not deliver both stories")
    assert client.post("/api/factories/demo-hq/engine/stop").json()["engine"] is False
    ov = client.get("/api/factories/demo-hq/overview").json()
    waiting = next(c for c in ov["columns"] if c["key"] == "AWAITING_FOUNDER")["stories"]
    assert len(waiting) == 2 and all(s["blocked_reason"] == "delivery" for s in waiting)
    story = client.get("/api/factories/demo-hq/stories/S-001").json()
    assert "spec" in story["docs"] and story["state"]["tasks_done"] == [1] and story["commits"]
    assert story["usage"]["calls"] >= 3 and [c["node"] for c in story["checkpoints"]][:3] == [
        "admit",
        "node_intake",
        "node_spec",
    ]
    msg = next(m for m in inbox if m["story_id"] == "S-001")
    r = client.post(
        f"/api/factories/demo-hq/inbox/{msg['id']}/reply", json={"option_key": "approve"}
    )
    assert r.json()["stage"] == "DONE"
    assert client.post("/api/factories/demo-hq/inbox/unknown/reply", json={}).status_code == 404
    events = client.get("/api/factories/demo-hq/events?after=0").json()
    assert {"story.created", "story.stage", "inbox.new", "story.merged"} <= {
        e["type"] for e in events
    }
    fin = client.get("/api/factories/demo-hq/finance").json()
    assert fin["month"]["totals"]["calls"] > 0
    agent = client.get("/api/factories/demo-hq/agents/Worker Loompa").json()
    assert agent["role"] == "worker" and agent["today"]["calls"] > 0
    rep = client.post("/api/factories/demo-hq/report").json()
    assert rep["kind"] == "info" and "Concluídas: 1" in rep["context"]


def test_create_story_promote_and_memory(client: TestClient):
    sid = client.post(
        "/api/factories/demo-hq/stories", json={"title": "Nova ideia", "priority": 1}
    ).json()["id"]
    assert client.post(f"/api/factories/demo-hq/stories/{sid}/promote").json()["stage"] == "SPEC"
    hits = client.get(
        "/api/factories/demo-hq/memory/search", params={"q": "constituição regras"}
    ).json()
    assert hits and hits[0]["kind"] in ("constitution", "learning", "doc")
    assert client.get("/api/factories/demo-hq/kaizen").json() == []


def test_websocket_hello_and_events(client: TestClient):
    with client.websocket_connect("/ws?factory=demo-hq") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["factory"] == "demo-hq"
        client.post("/api/factories/demo-hq/stories", json={"title": "Via API"})
        ev = ws.receive_json()
        assert ev["type"] == "story.created" and ev["payload"]["title"] == "Via API"


def test_index_without_bundle(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200 and ("Loompa LTDA HQ" in r.text or '<div id="root"' in r.text)
    assert client.get("/api/nope").status_code == 404


def test_reply_carries_decisions_and_sprints_are_listed(client: TestClient):
    from loompa.agents import ProductOwnerAgent
    from loompa.comms import FounderMessage, MessageKind, card_decision

    ctx = client.app.state.hub.get("demo-hq").ctx
    card = ProductOwnerAgent(ctx).add_item("[Débito técnico] limpar", origin="kaizen").story_id
    msg = ctx.inbox(
        FounderMessage(
            kind=MessageKind.DELIVERY,
            title="Entrega",
            context="ok",
            decisions=[card_decision({"id": card, "title": "limpar", "priority": 500})],
        )
    )
    pending = client.get("/api/factories/demo-hq/inbox").json()
    assert [d["id"] for d in pending[0]["decisions"]] == [card]
    # only the card is decided: the message stays pending, the card lands in the sprint draft
    r = client.post(
        f"/api/factories/demo-hq/inbox/{msg.id}/reply", json={"decisions": {card: "sprint"}}
    )
    assert r.status_code == 200 and r.json()["story_id"] is None
    assert (
        client.get("/api/factories/demo-hq/inbox").json()[0]["decisions"][0]["chosen"] == "sprint"
    )
    sprints = client.get("/api/factories/demo-hq/sprints").json()
    assert sprints[0]["status"] == "open" and sprints[0]["story_ids"] == [card]
    ov = client.get("/api/factories/demo-hq/overview").json()
    assert ov["sprint"]["id"] == "SP-001" and ov["sprint"]["progress"]["total"] == 1


def test_models_sync_endpoints(client: TestClient, monkeypatch):
    from loompa.config.schema import ModelCandidate
    from loompa.llm.catalog import parse_catalog
    from test_models_sync import CATALOG

    models = parse_catalog({"data": CATALOG})
    monkeypatch.setattr("loompa.models_sync.fetch_catalog", lambda *args, **kwargs: models)

    ctx = client.app.state.hub.get("demo-hq").ctx
    ctx.config.models.tiers["tier1"].insert(0, ModelCandidate(provider="openrouter", model="m1"))
    ctx.config.models.tiers["tier2"].insert(0, ModelCandidate(provider="openrouter", model="m2"))

    r = client.post("/api/factories/demo-hq/models/preview-sync")
    assert r.status_code == 200
    data = r.json()
    assert "clusters" in data
    assert "strategy" in data["clusters"]
    assert "engineering" in data["clusters"]
    assert "routine" in data["clusters"]
    assert len(data["clusters"]["strategy"]["tier1"]) > 0

    r_inbox = client.post("/api/factories/demo-hq/models/apply-sync", json={"to_inbox": True})
    assert r_inbox.status_code == 200
    assert r_inbox.json()["to_inbox"] is True

    r_apply = client.post("/api/factories/demo-hq/models/apply-sync", json={"to_inbox": False})
    assert r_apply.status_code == 200
    assert r_apply.json()["applied"] is True
