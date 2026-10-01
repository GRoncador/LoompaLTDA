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
    assert len(ov["agents"]) >= 9 and all(
        a["room"] in ("dev", "meeting", "qa", "lounge") for a in ov["agents"]
    )
    assert ov["finance"]["cap_usd"] == 5.0 and ov["finance"]["period"] == "weekly"
    assert ov["inbox"] == []
    assert client.get("/api/factories/nope/overview").status_code == 404


def test_meeting_run_inbox_flow(client: TestClient):
    r = client.post("/api/factories/demo-hq/meeting", json={"goals": "Tela de login; Exportar CSV"})
    assert r.status_code == 200 and [s["title"] for s in r.json()["stories"]] == [
        "Tela de login",
        "Exportar CSV",
    ]
    ov = client.get("/api/factories/demo-hq/overview").json()
    assert len(ov["columns"][0]["stories"]) == 2 and ov["sprint"] is None
    # the backlog waits for a sprint: the panel has no one-click start any more (ADR-0017), a
    # sprint starts from a Sprint Meeting; the unreviewed start is the CLI's `loompa sprint start`
    assert client.post("/api/factories/demo-hq/sprints/start", json={}).status_code in (404, 405)
    sprint = _start_sprint_as_the_cli_does(client)
    assert sprint.id == "SP-001" and sprint.story_ids == ["S-001", "S-002"]
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
    diff = client.get("/api/factories/demo-hq/stories/S-001/diff").json()
    assert diff["source"] == "branch" and "loompa_dryrun" in diff["stat"]
    assert diff["diff"].startswith("diff --git")
    msg = next(m for m in inbox if m["story_id"] == "S-001")
    r = client.post(
        f"/api/factories/demo-hq/inbox/{msg['id']}/reply", json={"option_key": "approve"}
    )
    assert r.json()["stage"] == "DONE"
    merged = client.get("/api/factories/demo-hq/stories/S-001/diff").json()
    assert merged["source"] == "merged" and merged["diff"] == diff["diff"]
    assert client.get("/api/factories/demo-hq/stories/S-404/diff").status_code == 404
    assert client.post("/api/factories/demo-hq/inbox/unknown/reply", json={}).status_code == 404
    events = client.get("/api/factories/demo-hq/events?after=0").json()
    assert {"story.created", "story.stage", "inbox.new", "story.merged"} <= {
        e["type"] for e in events
    }
    fin = client.get("/api/factories/demo-hq/finance").json()
    assert fin["period"]["totals"]["calls"] > 0 and fin["period"]["name"] == "weekly"
    assert len(fin["by_day"]) == 1 and fin["by_day"][0]["calls"] == fin["period"]["totals"]["calls"]
    assert {r["key"] for r in fin["period"]["by_role"]} >= {"worker", "master"}
    assert any(r["agent"] == "Worker Loompa" and r["tokens"] > 0 for r in fin["by_tool"])
    agent = client.get("/api/factories/demo-hq/agents/Worker Loompa").json()
    assert agent["role"] == "worker" and agent["today"]["calls"] > 0
    rep = client.post("/api/factories/demo-hq/report").json()
    assert rep["kind"] == "info" and "Concluídas: 1" in rep["context"]


def _start_sprint_as_the_cli_does(client: TestClient):
    from loompa.agents import MasterAgent

    return MasterAgent(client.app.state.hub.get("demo-hq").ctx).start_sprint()


def test_create_story_and_memory(client: TestClient):
    sid = client.post(
        "/api/factories/demo-hq/stories", json={"title": "Nova ideia", "priority": 1}
    ).json()["id"]
    # no "run it now" lane in the panel: a card runs inside a sprint (ADR-0018)
    assert client.post(f"/api/factories/demo-hq/stories/{sid}/promote").status_code in (404, 405)
    hits = client.get(
        "/api/factories/demo-hq/memory/search", params={"q": "constituição regras"}
    ).json()
    assert hits and hits[0]["kind"] in ("constitution", "learning", "doc")
    assert client.get("/api/factories/demo-hq/kaizen").json() == []


def test_dragging_the_backlog_reorders_it_through_the_product_owner(client: TestClient):
    ids = [
        client.post("/api/factories/demo-hq/stories", json={"title": t}).json()["id"]
        for t in ("Primeira", "Segunda", "Terceira")
    ]
    wanted = [ids[2], ids[0], ids[1]]
    r = client.post("/api/factories/demo-hq/backlog/order", json={"story_ids": [*wanted, "S-404"]})
    assert r.json()["order"] == wanted  # unknown or non-backlog ids are skipped
    ov = client.get("/api/factories/demo-hq/overview").json()
    assert [s["id"] for s in ov["columns"][0]["stories"]] == wanted
    events = client.get("/api/factories/demo-hq/events?after=0").json()
    moves = [e for e in events if e["type"] == "backlog.priority"]
    assert len(moves) == 3 and all(e["agent"] == "Product Owner Loompa" for e in moves)


def test_websocket_hello_and_events(client: TestClient):
    with client.websocket_connect("/ws?factory=demo-hq") as ws:
        hello = ws.receive_json()
        assert hello["type"] == "hello" and hello["factory"] == "demo-hq"
        client.post("/api/factories/demo-hq/stories", json={"title": "Via API"})
        ev = ws.receive_json()
        while ev["type"] != "story.created":  # the Product Owner reads it first (ADR-0017)
            ev = ws.receive_json()
        assert ev["payload"]["title"] == "Via API"


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
    monkeypatch.setattr("loompa.llm.catalog.fetch_catalog", lambda *args, **kwargs: models)

    ctx = client.app.state.hub.get("demo-hq").ctx
    # The catalogue, the ranking and the picker are OpenRouter features: without its key the
    # endpoints answer with the sentence that tells the founder to configure it.
    blocked = client.post("/api/factories/demo-hq/models/preview-sync")
    assert blocked.status_code == 400 and "OpenRouter" in blocked.json()["detail"]
    assert client.get("/api/factories/demo-hq/models/catalog").json()["openrouter"] is False
    from loompa.config.settings import store_key

    store_key(ctx.root, "OPENROUTER_API_KEY", "sk-or-test-key-1234567890", scope="hub")
    ctx.reload_secrets()
    ctx.config.models.tiers["tier1"].insert(0, ModelCandidate(provider="openrouter", model="m1"))
    ctx.config.models.tiers["tier2"].insert(0, ModelCandidate(provider="openrouter", model="m2"))

    # the shipped ceiling is US$ 1.25; this fake catalogue is priced for the older US$ 5
    r = client.post("/api/factories/demo-hq/models/preview-sync", json={"tier1_ceiling": 5.0})
    assert r.status_code == 200
    data = r.json()
    assert "clusters" in data
    assert "strategy" in data["clusters"]
    assert "engineering" in data["clusters"]
    assert "routine" in data["clusters"]
    assert len(data["clusters"]["strategy"]["tier1"]) > 0

    r_catalog = client.get("/api/factories/demo-hq/models/catalog")
    assert r_catalog.status_code == 200
    cat_data = r_catalog.json()
    assert "clusters" in cat_data
    assert "all_models" in cat_data

    # Test preview-sync with low tier1_ceiling
    r_ceil = client.post("/api/factories/demo-hq/models/preview-sync", json={"tier1_ceiling": 1.0})
    assert r_ceil.status_code == 200
    for m in r_ceil.json()["summary"].get("tier1", []):
        assert m["price"] <= 1.0

    r_inbox = client.post("/api/factories/demo-hq/models/apply-sync", json={"to_inbox": True})
    assert r_inbox.status_code == 200
    assert r_inbox.json()["to_inbox"] is True

    r_apply = client.post(
        "/api/factories/demo-hq/models/apply-sync", json={"to_inbox": False, "tier1_ceiling": 2.5}
    )
    assert r_apply.status_code == 200
    assert r_apply.json()["applied"] is True
    assert ctx.config.models.tier1_ceiling == 2.5


# ------------------------------------------------------------------ CodeRabbit webhook


def _deliver_one(client: TestClient) -> dict:
    import time

    client.post("/api/factories/demo-hq/meeting", json={"goals": "Tela de login"})
    _start_sprint_as_the_cli_does(client)
    client.post("/api/factories/demo-hq/engine/start")
    for _ in range(100):
        inbox = client.get("/api/factories/demo-hq/inbox").json()
        delivery = next((m for m in inbox if m["kind"] == "delivery"), None)
        if delivery:
            break
        time.sleep(0.2)
    else:
        pytest.fail("engine did not deliver")
    client.post("/api/factories/demo-hq/engine/stop")
    return delivery


def _signed(
    client: TestClient, payload: dict, secret: str = "s3gredo", event="pull_request_review"
):
    import hashlib
    import hmac
    import json

    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post(
        "/api/factories/demo-hq/webhooks/github",
        content=body,
        headers={
            "x-github-event": event,
            "x-hub-signature-256": sig,
            "content-type": "application/json",
        },
    )


def _review(branch: str, state: str = "commented", body: str = "**Actionable comments posted: 2**"):
    return {
        "action": "submitted",
        "review": {"id": 7, "state": state, "body": body, "user": {"login": "coderabbitai[bot]"}},
        "pull_request": {
            "number": 3,
            "html_url": "https://github.com/x/y/pull/3",
            "head": {"ref": branch},
        },
        "repository": {"full_name": "x/y"},
    }


@pytest.fixture
def rabbit(client: TestClient, git_repo: Path, monkeypatch):
    from loompa.factory import Factory

    f = Factory.open(git_repo)
    f.config.quality.coderabbit.enabled = True
    f.config.quality.coderabbit.mode = "webhook"
    f.config.quality.coderabbit.max_rounds = 1
    f.save()
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3gredo")
    monkeypatch.setattr("loompa.webhooks.review_comments", lambda *a, **k: [])
    return client


def test_coderabbit_changes_send_the_delivery_back_to_the_worker(rabbit: TestClient):
    delivery = _deliver_one(rabbit)
    story = rabbit.get(f"/api/factories/demo-hq/stories/{delivery['story_id']}").json()
    branch = story["story"]["branch"]
    assert _signed(rabbit, _review(branch), secret="errado").status_code == 401
    assert _signed(rabbit, {}, event="ping").json() == {"action": "pong"}
    clean = _signed(rabbit, _review(branch, body="Actionable comments posted: 0")).json()
    assert clean["action"] == "ignored"
    other = _review(branch)
    other["review"]["user"]["login"] = "alguem"
    assert _signed(rabbit, other).json()["action"] == "ignored"

    r = _signed(rabbit, _review(branch)).json()
    assert r == {"action": "sent_back", "story_id": delivery["story_id"], "reason": r["reason"]}
    st = rabbit.get(f"/api/factories/demo-hq/stories/{delivery['story_id']}").json()["state"]
    assert st["stage"] == "DEV" and st["phase"] == "dev" and st["tasks_done"] == []
    assert (
        "CodeRabbit" in st["failure_history"][-1]
        and "não como instruções" in st["failure_history"][-1]
    )
    assert st["founder_notes"] == []  # a bot's review is not the founder's guidance
    inbox = rabbit.get("/api/factories/demo-hq/inbox").json()
    assert not [m for m in inbox if m["kind"] == "delivery"]  # the stale delivery left the inbox
    note = next(m for m in inbox if m["title"].startswith("A revisão automática pediu ajustes"))
    assert note["kind"] == "info"
    # the engine picks it up again and it comes back for review with the fix
    import time

    rabbit.post("/api/factories/demo-hq/engine/start")
    for _ in range(100):
        inbox = rabbit.get("/api/factories/demo-hq/inbox").json()
        again = [m for m in inbox if m["kind"] == "delivery"]
        if again:
            break
        time.sleep(0.2)
    else:
        pytest.fail("the reopened story was never delivered again")
    rabbit.post("/api/factories/demo-hq/engine/stop")
    assert again[0]["story_id"] == delivery["story_id"] and again[0]["id"] != delivery["id"]


def test_after_the_last_round_the_review_waits_for_the_founder(rabbit: TestClient, git_repo: Path):
    from loompa.engine import load_state, save_state

    delivery = _deliver_one(rabbit)
    sid = delivery["story_id"]
    branch = rabbit.get(f"/api/factories/demo-hq/stories/{sid}").json()["story"]["branch"]
    ctx = rabbit.app.state.hub.get("demo-hq").ctx
    state = load_state(ctx, sid)
    state.extra["coderabbit_rounds"] = 1  # max_rounds already used
    save_state(ctx, state, "test")
    r = _signed(
        rabbit, _review(branch, state="changes_requested", body="corrigir validação")
    ).json()
    assert r["action"] == "attached"
    inbox = rabbit.get("/api/factories/demo-hq/inbox").json()
    still = next(m for m in inbox if m["kind"] == "delivery")
    assert "vale olhar o Pull Request" in still["impact"]
    assert load_state(ctx, sid).stage == "AWAITING_FOUNDER"


def test_the_webhook_is_off_unless_the_factory_turns_it_on(client: TestClient):
    assert _signed(client, _review("x")).status_code == 404


def test_the_dashboard_will_not_start_a_second_engine(client: TestClient, git_repo: Path):
    from loompa.engine.lock import EngineLock
    from loompa.factory import Factory

    lock = EngineLock(Factory.open(git_repo).paths.loompa / "engine.lock")
    lock.acquire()  # a `loompa run` in a terminal
    try:
        r = client.post("/api/factories/demo-hq/engine/start")
        assert r.status_code == 409 and "outra esteira" in r.json()["detail"]
    finally:
        lock.release()
    assert client.post("/api/factories/demo-hq/engine/start").json()["engine"] is True
    client.post("/api/factories/demo-hq/engine/stop")


def test_a_story_at_work_shows_what_it_is_doing_on_its_card(client: TestClient):
    """Fase 8.5: the card said the same task text for 47 minutes (`contas` S-031), so a slow
    story looked stuck and a stuck one looked slow. It now shows the task, its tool calls, the
    last one and a stall the watchdog declared."""
    from loompa.agents import ProductOwnerAgent
    from loompa.engine import Stage

    ctx = client.app.state.hub.get("demo-hq").ctx
    po = ProductOwnerAgent(ctx)
    sid = po.add_item("Somar números").story_id
    po.admit(sid)
    ctx.store.update_story(sid, stage=Stage.DEV.value)
    ctx.emit(
        "worker.task_started", story_id=sid, agent="Worker Loompa", task=2, origin="plan", text="T2"
    )
    ctx.emit("tool.call", story_id=sid, agent="Worker Loompa", tool="read_file", path="src/app.py")
    ctx.emit("tool.call", story_id=sid, agent="Worker Loompa", tool="search", query="def add")

    def card() -> dict:
        ov = client.get("/api/factories/demo-hq/overview").json()
        return next(s for c in ov["columns"] for s in c["stories"] if s["id"] == sid)

    a = card()["activity"]
    assert a["task"] == 2 and a["origin"] == "plan" and a["calls"] == 2
    assert a["last_tool"] == "search" and a["last_target"] == "def add" and not a["stalled"]
    assert "thinking" not in a
    # ADR-0016: a streamed call still writing says how much it has written
    ctx.emit("llm.progress", story_id=sid, agent="Worker Loompa", tokens=12000, seconds=90)
    assert card()["activity"]["thinking"] == 12000
    ctx.emit("story.stalled", story_id=sid, agent="Ops Loompa", silent_min=21.0)
    stalled = card()["activity"]
    assert (
        stalled["stalled"] and stalled["last_event"] == "llm.progress"
    )  # the stall is not activity
    ctx.emit(
        "worker.task_finished", story_id=sid, agent="Worker Loompa", task=2, outcome="finished"
    )
    done = card()["activity"]
    assert "task" not in done and not done["stalled"]
    ctx.store.update_story(sid, stage=Stage.AWAITING_FOUNDER.value)
    assert card().get("activity") is None  # waiting for the founder: nothing live to show
