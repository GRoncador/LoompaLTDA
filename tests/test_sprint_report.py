"""The sprint report (Plano 8.2) and the Sprints tab's data (10.8): measured in code from the
events and the usage table, saved when a sprint ends, with the Master's summary on top. No
network."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest import mock

from typer.testing import CliRunner

from loompa import sprint_report
from loompa.agents import MasterAgent, ProductOwnerAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.engine import Stage
from loompa.factory import Factory
from loompa.llm import Message
from loompa.sprints import SprintBoard
from test_dashboard import client  # noqa: F401
from test_engine import factory, make_ctx  # noqa: F401

BASE = "/api/factories/demo-hq"


def sprint_with_history(ctx, *, traced: bool = True) -> tuple[str, str, str]:
    """SP-001 with two planned stories and one that joined mid-sprint; a, the slow one, was
    retried, escalated, blocked once and the founder asked for changes on its delivery."""
    po = ProductOwnerAgent(ctx)
    a, b = (po.add_item(t).story_id for t in ("Login", "Relatório"))
    MasterAgent(ctx).start_sprint([a, b], goal="Entrar e ver o mês")
    emit = ctx.emit
    for sid in (a, b):
        emit("story.stage", story_id=sid, stage="SPEC")
        emit("story.stage", story_id=sid, stage="DEV")
    emit("story.retry", story_id=a, tier="tier2", attempt=1)
    emit("story.escalated", story_id=a, to_tier="tier1", after_attempts=2)
    emit("story.retry", story_id=a, node="dev", cause="rede", recoveries=1)  # an Ops recovery
    emit("story.blocked", story_id=a, reason="question", message_id="m1")
    emit("inbox.answered", story_id=a, message_id="m1", option="opt1")
    emit("inbox.answered", story_id=a, message_id="m2", option="changes")
    emit("inspector.verdict", story_id=a, verdict="FAIL", findings=2)
    emit("inspector.verdict", story_id=a, verdict="PASS", findings=0)
    if traced:
        for n, origin in ((1, "plan"), (2, "plan"), (3, "inspector")):
            emit(
                "worker.task_finished",
                story_id=a,
                task=n,
                origin=origin,
                outcome="finished",
                duration_s=30,
            )
        emit("llm.cut", story_id=a, model="m")
    else:
        emit("worker.task", story_id=a, task=1, ended_by="done", tool_calls=4)
    for sid, cost in ((a, 0.3), (b, 0.1), (None, 0.05)):
        ctx.store.record_usage(
            factory=ctx.slug,
            story_id=sid,
            agent="Worker Loompa",
            role="worker",
            provider="p",
            model="m",
            tier="tier2",
            input_tokens=1000,
            output_tokens=100,
            cost_usd=cost,
            duration_ms=2000,
            span_id="abc" if traced else "",
            cost_source="reported" if traced else "",
        )
    # a card joins the running sprint in a meeting; Kaizen files a finding for later
    c = po.add_item("Exportar").story_id
    board = SprintBoard(ctx.store, ctx.slug)
    board.add(c, "SP-001")
    emit("sprint.adjusted", sprint_id="SP-001", joined=[c], withdrawn=[], restarted=[])
    later = po.add_item("[Débito técnico] limpar", origin="kaizen").story_id
    emit("kaizen.learning", story_id=a, kind="tech_debt", title="limpar", created_story_id=later)
    for sid in (a, b, c):
        emit("story.stage", story_id=sid, stage="DONE")
        ctx.store.update_story(sid, stage=Stage.DONE.value)
    return a, b, c


async def test_the_report_measures_each_story_and_what_was_not_planned(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    a, b, c = sprint_with_history(ctx)
    await MasterAgent(ctx).close_finished_sprints()

    report = sprint_report.load(factory.paths.reports, "SP-001")
    assert report is not None and report["summary"]
    t = report["totals"]
    assert (t["stories"], t["planned"], t["joined"], t["delivered"]) == (3, 2, 1, 3)
    # the factory's call outside any story counts for the period, not for the stories
    assert t["cost_usd"] == 0.4 and t["factory_cost_usd"] == 0.45
    assert t["retries"] == 1 and t["escalations"] == 1 and t["recoveries"] == 1
    assert t["changes_asked"] == 1 and t["rework"] == 2 and t["tasks_done"] == 3 and t["cuts"] == 1
    story_a = next(s for s in report["stories"] if s["id"] == a)
    assert story_a["blocks"] == {"question": 1} and story_a["founder_answers"] == 2
    assert story_a["review_rounds"] == 2 and story_a["tasks"]["by_origin"] == {
        "plan": 2,
        "inspector": 1,
    }
    assert report["unplanned"]["joined"] == [{"id": c, "title": "Exportar", "how": "pedido seu"}]
    assert report["unplanned"]["tasks_added"] == {"inspector": 1}
    assert report["unplanned"]["extra_review_rounds"] == 1
    assert [x["origin"] for x in report["later"]["cards"]] == ["kaizen"]
    # the burn-up ends with everything done
    assert report["timeline"][-1]["DONE"] == 3

    md = (factory.paths.reports / "SP-001.md").read_text()
    assert "# Relatório do SP-001" in md and "Entrar e ver o mês" in md and "não medido" not in md
    assert (factory.paths.reports / ".gitignore").read_text().strip().endswith("*")

    # the inbox note carries the summary and leads to the report
    note = next(
        m for m in ctx.store.list_messages(ctx.slug) if m.title == "Sprint SP-001 concluído"
    )
    assert note.sprint_id == "SP-001" and note.context == report["summary"]
    assert "aba Sprints" in note.impact and note.executive_audit() == []
    await ctx.aclose()


async def test_an_epic_is_not_counted_as_a_delivery(factory: Factory):
    """tamagotchi SP-001: "4 de 5 entregues" counted S-002, split into an epic with no code of
    its own; its parts are the deliveries."""
    ctx = make_ctx(factory, dry_run=True)
    a, b, c = sprint_with_history(ctx)
    ctx.emit("story.split", story_id=a, children=[b])
    await MasterAgent(ctx).close_finished_sprints()
    report = sprint_report.load(factory.paths.reports, "SP-001")
    t = report["totals"]
    assert (t["stories"], t["delivered"], t["split"]) == (3, 2, 1)
    assert next(s for s in report["stories"] if s["id"] == a)["result"] == "split"
    assert "split into an epic" in sprint_report.summary_facts(report)
    assert "dividida em épico" in (factory.paths.reports / "SP-001.md").read_text()
    await ctx.aclose()


async def test_a_sprint_before_the_trace_says_not_measured(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    sprint_with_history(ctx, traced=False)
    sprint = SprintBoard(ctx.store, ctx.slug).get("SP-001")
    report = sprint_report.measure(ctx.store, ctx.slug, sprint)
    assert report["measured"]["traced"] is False
    assert report["unplanned"]["tasks_added"] is None and report["totals"]["cuts"] is None
    assert report["totals"]["tasks_done"] == 1  # the old task events still count runs
    md = sprint_report.render_markdown(report)
    assert "não medido" in md and "rodou antes do rastro" in md
    await ctx.aclose()


async def test_the_next_sprint_is_compared_with_the_one_before(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    sprint_with_history(ctx)
    master = MasterAgent(ctx)
    await master.close_finished_sprints()
    d = ProductOwnerAgent(ctx).add_item("Gráfico").story_id
    master.start_sprint([d])
    ctx.store.update_story(d, stage=Stage.DONE.value)
    await master.close_finished_sprints()
    report = sprint_report.load(factory.paths.reports, "SP-002")
    assert report["previous"]["id"] == "SP-001" and report["previous"]["totals"]["delivered"] == 3
    assert "Comparação com o SP-001" in sprint_report.render_markdown(report)
    await ctx.aclose()


def summary_says(text: str):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "master" and "Sprint report" in messages[0].content:
            return json.dumps({"summary": text})
        return dry_run_script(model, messages, tools)

    return script


async def test_the_masters_summary_is_audited(factory: Factory):
    ctx = make_ctx(factory, summary_says("Entregamos tudo; o atraso veio de /Users/x/app.py."))
    sprint_with_history(ctx)
    sprint = SprintBoard(ctx.store, ctx.slug).get("SP-001")
    _, summary = await MasterAgent(ctx).write_sprint_report(sprint)
    assert "/Users" not in summary and summary.startswith("O SP-001 entregou")  # code wrote it
    good = make_ctx(factory, summary_says("O SP-001 entregou as três histórias."))
    _, summary = await MasterAgent(good).write_sprint_report(sprint)
    assert summary == "O SP-001 entregou as três histórias."
    await ctx.aclose()
    await good.aclose()


async def test_a_cancelled_sprint_is_reported_too(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    sprint_with_history(ctx)
    sprint = SprintBoard(ctx.store, ctx.slug).close("SP-001", cancelled=True)
    report, _ = await MasterAgent(ctx).write_sprint_report(sprint)
    assert report["sprint"]["status"] == "cancelled"
    assert (factory.paths.reports / "SP-001.md").is_file()
    await ctx.aclose()


def test_the_sprints_tab_and_the_card_marker(client):
    ctx = client.app.state.hub.get("demo-hq").ctx
    a, _, _ = sprint_with_history(ctx)
    listed = client.get(f"{BASE}/sprints").json()
    assert listed[0]["id"] == "SP-001" and listed[0]["totals"]["stories"] == 3
    live = client.get(f"{BASE}/sprints/SP-001/report").json()  # still running: measured now
    assert (
        live["saved"] is False
        and live["summary"] == ""
        and "# Relatório do SP-001" in live["markdown"]
    )
    cards = [c for col in client.get(f"{BASE}/overview").json()["columns"] for c in col["stories"]]
    assert next(c for c in cards if c["id"] == a)["sprint_id"] == "SP-001"
    assert client.get(f"{BASE}/stories/{a}").json()["sprints"] == ["SP-001"]
    assert client.get(f"{BASE}/sprints/SP-404/report").status_code == 404


def test_the_cli_prints_and_saves_the_report(factory: Factory):
    import asyncio

    from loompa.cli import ops
    from loompa.cli.main import app

    ctx = make_ctx(factory, dry_run=True)
    sprint_with_history(ctx)
    SprintBoard(ctx.store, ctx.slug).close("SP-001")
    asyncio.run(ctx.aclose())
    reopen = lambda *_a, **_k: Factory.open(Path(factory.paths.root))  # noqa: E731
    with mock.patch.object(ops, "resolve_factory", reopen):
        r = CliRunner().invoke(app, ["sprint", "report", "--dry-run"], env={"COLUMNS": "160"})
    assert r.exit_code == 0, r.output
    assert "Relatório do SP-001" in r.output and "Resumo executivo" in r.output
    assert (factory.paths.reports / "SP-001.json").is_file()
