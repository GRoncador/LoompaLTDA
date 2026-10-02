"""The factory's self-diagnosis (Fase 8.3 and 8.4). No network."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from loompa.agents import MasterAgent
from loompa.cli.main import app as cli
from loompa.comms import FounderAnswer, FounderMessage, MessageKind, Option
from loompa.engine import Scheduler
from loompa.factory import Factory
from loompa.factory_health import HealthBook, Window, detect, export_markdown, hub_book
from loompa.sprints import SprintBoard
from loompa.store import Store
from test_dashboard import client  # noqa: F401
from test_engine import factory, make_ctx  # noqa: F401

runner = CliRunner(env={"COLUMNS": "220"})
BASE = "/api/factories/demo-hq"
T0 = "2026-10-01T10:00:00+00:00"
T1 = "2026-10-01T20:00:00+00:00"


def ev(i: int, type_: str, story: str | None = None, **payload) -> dict:
    return {
        "id": i,
        "story_id": story,
        "agent": "",
        "type": type_,
        "payload": payload,
        "created_at": T0,
    }


def window(tmp_path: Path, events=(), usage=(), messages=(), **kw) -> Window:
    return Window(
        slug="f",
        root=tmp_path,
        since=T0,
        until=T1,
        events=list(events),
        usage=list(usage),
        messages=list(messages),
        **kw,
    )


def by_signal(findings) -> dict:
    return {f.signal: f for f in findings}


def test_signals_measure_what_sprint_one_found(tmp_path: Path):
    """The thresholds come from contas Sprint 1: finished tasks never passed 4 repeats, the judge
    took 15-21 min on the stories the founder noticed, a self-check failing 8-10 times."""
    events = [
        *(ev(i, "llm.cut", "S-001", model="m1", role="worker") for i in range(1, 4)),
        ev(4, "llm.cut", "S-001", model="m2", role="worker"),  # one cut is not a pattern
        ev(5, "story.task_unfinished", "S-030", task=3, ended_by="limit"),
        ev(6, "worker.task", "S-030", task=3, repeats=22, ended_by="limit", tool_calls=47),
        ev(7, "worker.task", "S-031", task=2, repeats=4, ended_by="done", tool_calls=30),
        ev(8, "worker.task_finished", "S-031", task=5, duration_s=35 * 60),
        *(ev(10 + i, "worker.dod_incomplete", "S-005") for i in range(3)),
        ev(20, "worker.dod_incomplete", "S-006"),
        *(ev(30 + i, "worktree.sync_failed", "S-007") for i in range(2)),
        ev(40, "story.stalled", "S-031", silent_min=50, last_event="tool.call"),
        *(ev(50 + i, "backlog.duplicate", None, title="gastos.json deixado") for i in range(4)),
        *(ev(60 + i, "tool.call", "S-001", tool="read_file", tokens=900) for i in range(3)),
        ev(70, "tool.call", "S-001", tool="search", tokens=100),
    ]
    usage = [
        {
            "story_id": "S-002",
            "role": "inspector",
            "model": "m",
            "duration_ms": 21 * 60000,
            "cost_usd": 0.1,
        },
        {
            "story_id": "S-003",
            "role": "inspector",
            "model": "m",
            "duration_ms": 5 * 60000,
            "cost_usd": 0.1,
        },
        {
            "story_id": "S-002",
            "role": "worker",
            "model": "m",
            "duration_ms": 1000,
            "cost_usd": 1.0,
            "finish_reason": "length",
        },
    ]
    question = FounderMessage(
        factory="f",
        kind=MessageKind.DECISION,
        title="Remover o worktree?",
        context="c",
        options=[Option(key="a", label="Rodar o checklist T1")],
    )
    found = by_signal(detect(window(tmp_path, events, usage, [question])))
    assert found["llm.cuts"].key == "m1/worker" and found["llm.cuts"].impact["calls"] == 3
    assert found["worker.task_limit"].stories == ["S-030"]
    assert found["worker.task_limit"].evidence[0]["command"] == "loompa trace S-030 --task 3"
    # the cut task emitted both events: counted once, with its calls
    assert len(found["worker.task_limit"].evidence) == 1
    assert found["worker.task_limit"].evidence[0]["calls"] == 47
    assert found["worker.repeats"].stories == ["S-030"]  # S-031's 4 repeats are normal
    assert found["inspector.slow"].stories == ["S-002"]
    assert found["worker.slow_task"].evidence[0]["minutes"] == 35.0
    assert found["worker.dod"].stories == ["S-005"]
    assert found["worktrees.sync"].severity == "high"
    assert found["scheduler.stalled"].impact["minutes"] == 50.0
    assert "4x" in found["kaizen.duplicates"].detail
    assert found["finance.role_share"].key == "worker"
    assert found["finance.tool_share"].key == "read_file"
    assert set(found["comms.jargon"].evidence[0]["words"]) == {"worktree", "checklist", "t1"}
    assert detect(window(tmp_path)) == []  # a quiet window finds nothing
    assert found["worker.task_limit"].signature == "worker.task_limit:limit"


def test_a_trace_shows_rereads_and_an_ignored_guard_provisionally(tmp_path: Path):
    traces = tmp_path / ".loompa" / "traces"
    traces.mkdir(parents=True)
    spans = [
        {
            "t": "span",
            "id": "t1",
            "parent": None,
            "story": "S-031",
            "kind": "task",
            "name": "T5",
            "start": T0,
            "attrs": {"task": 5},
        }
    ]
    for i in range(4):
        spans.append(
            {
                "t": "span",
                "id": f"x{i}",
                "parent": "t1",
                "story": "S-031",
                "kind": "tool",
                "name": "read_file",
                "start": T0,
                "attrs": {
                    "args": {"path": "app.py"},
                    **({"note": "you read this already"} if i == 1 else {}),
                },
            }
        )
    (traces / "S-031.jsonl").write_text("\n".join(json.dumps(s) for s in spans))
    found = by_signal(detect(window(tmp_path, [ev(1, "worker.task", "S-031", repeats=0)])))
    assert found["trace.rereads"].provisional and found["trace.rereads"].evidence[0]["times"] == 4
    assert found["trace.guard_ignored"].impact["calls"] == 2  # two more reads after the warning


def test_the_hub_keeps_the_trend_and_confirms_a_fix(tmp_path: Path):
    book = HealthBook(tmp_path)
    cut = [ev(i, "llm.cut", "S-001", model="m1", role="worker") for i in range(3)]
    w1 = window(tmp_path, cut, sprint_id="SP-001")
    first = book.record("contas", w1, detect(w1))
    assert first["new"] == ["llm.cuts:m1/worker"]
    assert book.resolve("llm.cuts:m1/worker", "abc1234")
    later = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    w2 = Window(slug="f", root=tmp_path, since=later, until=later, sprint_id="SP-002")
    assert book.record("contas", w2, detect(w2))["confirmed"] == ["llm.cuts:m1/worker"]
    w3 = Window(
        slug="f", root=tmp_path, since=later, until=later + "Z", sprint_id="SP-003", events=cut
    )
    assert book.record("contas", w3, detect(w3))["back"] == ["llm.cuts:m1/worker"]
    (row,) = book.findings(status="open")
    assert row["status"] == "open" and [t["seen"] for t in row["trend"]] == [True, False, True]
    assert row["factories"] == ["contas"] and row["evidence"]
    md = export_markdown(book.findings(status=None))
    assert "llm.cuts:m1/worker" in md and "loompa trace S-001" in md
    assert "visto em: contas SP-001, contas SP-003" in md  # the sprints, not the scan day


async def test_a_sprint_close_scans_the_factory_and_the_report_points_to_it(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    master = MasterAgent(ctx)
    await master.meeting("Página de login")
    master.start_sprint()
    await Scheduler(ctx).run()
    (msg,) = [m for m in ctx.store.list_messages(factory.slug, "pending") if m.kind == "delivery"]
    msg.title = "Revise o merge do worktree"  # jargon the founder should never have read
    ctx.store.put_message(msg)
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="approve"))
    assert SprintBoard(ctx.store, factory.slug).get("SP-001").status == "closed"
    scans = hub_book().scans(factory.slug)
    assert len(scans) == 1 and scans[0]["sprint_id"] == "SP-001"
    report = json.loads((factory.paths.reports / "SP-001.json").read_text())
    assert any(f["signature"] == "comms.jargon:jargon" for f in report["factory"])
    assert "Achados da fábrica neste sprint" in (factory.paths.reports / "SP-001.md").read_text()
    await ctx.aclose()


def test_the_cli_lists_shows_resolves_and_exports(git_repo: Path, hub, monkeypatch, tmp_path):
    monkeypatch.chdir(git_repo)
    assert runner.invoke(cli, ["init", ".", "--yes", "--name", "Saude"]).exit_code == 0
    w = window(tmp_path, [ev(i, "llm.cut", "S-001", model="m1", role="worker") for i in range(3)])
    hub_book().record("saude", w, detect(w))
    r = runner.invoke(cli, ["factory-health"])
    assert r.exit_code == 0 and "llm.cuts:m1/worker" in r.stdout
    r = runner.invoke(cli, ["factory-health", "show", "llm.cuts"])
    assert r.exit_code == 0 and "loompa trace S-001" in r.stdout
    r = runner.invoke(cli, ["factory-health", "resolve", "llm.cuts", "--commit", "abc1234"])
    assert r.exit_code == 0 and "resolvido" in r.stdout
    assert "Nenhum achado aberto" in runner.invoke(cli, ["factory-health"]).stdout
    out = tmp_path / "factory-improvements.md"
    assert runner.invoke(cli, ["factory-health", "--all", "--export", str(out)]).exit_code == 0
    assert "abc1234" in out.read_text()
    r = runner.invoke(cli, ["factory-health", "scan", "--dry-run"])
    assert r.exit_code == 0 and "achados" in r.stdout


def test_the_api_serves_the_factory_tab_and_product_findings(client: TestClient, git_repo: Path):  # noqa: F811
    w = Window(
        slug="demo-hq",
        root=Path("."),
        since=T0,
        until=T1,
        events=[ev(1, "story.stalled", "S-001", silent_min=12)],
    )
    hub_book().record("demo-hq", w, detect(w))
    r = client.get("/api/factory-health")
    (row,) = r.json()["findings"]
    assert row["signature"] == "scheduler.stalled:stalled" and r.json()["scans"]
    assert (
        client.post(
            f"/api/factory-health/{row['signature']}/resolve", json={"commit": "x1"}
        ).status_code
        == 200
    )
    assert client.get("/api/factory-health").json()["findings"] == []
    assert client.post(f"/api/factory-health/{row['signature']}/reopen").status_code == 200
    assert client.post(f"{BASE}/factory-health/scan", json={}).status_code == 200

    store = Store(git_repo / ".loompa" / "state.db")  # what the Kaizen loop caught today
    for title in ("gastos.json deixado na raiz", "Gastos.JSON deixado na raiz!", "Validar datas"):
        store.add_learning(story_id="S-001", kind="tech_debt", title=title)
    store.close()
    found = client.get(f"{BASE}/product-findings").json()
    assert sorted(f["title"] for f in found) == ["Validar datas", "gastos.json deixado na raiz"]
    assert client.get(f"{BASE}/overview").json()["kaizen_today"] == 2


def test_a_runaway_and_a_slow_model_in_a_role_are_found(tmp_path: Path):
    """contas Sprint 2: as Product Owner, deepseek-v4-flash-0731 looped or hit the ceiling on most
    calls (mean ~9 min) while ling-3.0-flash answered in about a minute."""
    events = [
        ev(1, "llm.loop", "S-044", model="ds", role="product_owner"),
        ev(2, "llm.fallthrough", "S-044", model="ds", role="product_owner", reason="cut"),
        ev(3, "llm.fallthrough", "S-045", model="ds", role="worker", reason="error"),
    ]
    usage = [
        {"story_id": "S-044", "role": "product_owner", "model": m, "duration_ms": s * 1000}
        for m, s in [
            ("ds", 600),
            ("ds", 500),
            ("ds", 640),
            ("ling", 60),
            ("ling", 80),
            ("ling", 70),
        ]
    ]
    found = by_signal(detect(window(tmp_path, events, usage)))
    assert (
        found["llm.runaway"].key == "ds/product_owner" and found["llm.runaway"].impact["calls"] == 2
    )
    slow = found["llm.slow_model"]
    assert slow.key == "ds/product_owner" and "contra 70 s de ling" in slow.detail
