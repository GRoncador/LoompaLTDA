"""Sprints scope what the factory works on (ADR-0006 §5, ADR-0008)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from loompa.agents import MasterAgent, ProductOwnerAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.comms import FounderAnswer
from loompa.engine import Scheduler, Stage, load_state
from loompa.factory import Factory
from loompa.llm import Message
from loompa.sprints import SprintBoard, SprintError, SprintStatus
from test_engine import factory, make_ctx  # noqa: F401


def events(ctx, type_: str) -> list[dict]:
    return [e for e in ctx.store.events_since(0, limit=5000) if e["type"] == type_]


async def test_backlog_waits_for_a_sprint_and_the_sprint_closes_when_all_finish(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    master = MasterAgent(ctx)
    await master.meeting("Página de login; Exportar CSV")
    sched = Scheduler(ctx)
    assert sched.runnable() == [] and await sched.run() == []  # cards do not run on their own
    assert {s["stage"] for s in ctx.store.list_stories(factory.slug)} == {Stage.BACKLOG}

    sprint = master.start_sprint(goal="Primeira entrega")
    assert sprint.id == "SP-001" and sprint.status == SprintStatus.RUNNING
    assert sprint.story_ids == ["S-001", "S-002"] and events(ctx, "sprint.started")
    assert sorted(await Scheduler(ctx).run()) == ["S-001", "S-002"]

    board = SprintBoard(ctx.store, factory.slug)
    # both deliveries wait for the founder, so the sprint is still running
    assert board.get("SP-001").status == SprintStatus.RUNNING
    assert board.progress(board.get("SP-001")) == {
        "total": 2,
        "done": 0,
        "cancelled": 0,
        "waiting": 2,
    }
    deliveries = [
        m for m in ctx.store.list_messages(factory.slug, "pending") if m.kind == "delivery"
    ]
    await Scheduler(ctx).aanswer(deliveries[0].id, FounderAnswer(option_key="approve"))
    assert board.get("SP-001").status == SprintStatus.RUNNING and not events(ctx, "sprint.done")
    await Scheduler(ctx).aanswer(deliveries[1].id, FounderAnswer(option_key="drop"))
    closed = board.get("SP-001")
    assert closed.status == SprintStatus.CLOSED and closed.closed_at
    done = events(ctx, "sprint.done")
    assert (
        len(done) == 1 and done[0]["payload"]["done"] == 1 and done[0]["payload"]["cancelled"] == 1
    )
    info = [
        m for m in ctx.store.list_messages(factory.slug) if m.title == "Sprint SP-001 concluído"
    ]
    assert (
        len(info) == 1 and info[0].executive_audit() == [] and "Primeira entrega" in info[0].context
    )
    await ctx.aclose()


async def test_a_blocked_story_waits_alone_and_does_not_hold_the_batch(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if (
            role_of(messages) == "product"
            and "Cobrança" in messages[1].content
            and "Guidance from the founder" not in messages[1].content
        ):
            return json.dumps(
                {
                    "needs_decision": True,
                    "question": "Por assento ou por uso?",
                    "options": ["a", "b"],
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    po = ProductOwnerAgent(ctx)
    for title in ("Cobrança", "Relatório"):
        po.add_item(title)
    master = MasterAgent(ctx)
    master.start_sprint()
    await Scheduler(ctx).run()
    blocked, other = load_state(ctx, "S-001"), load_state(ctx, "S-002")
    assert blocked.blocked_reason == "question" and other.blocked_reason == "delivery"
    board = SprintBoard(ctx.store, factory.slug)
    assert board.get("SP-001").status == SprintStatus.RUNNING
    assert board.progress(board.get("SP-001"))["waiting"] == 2
    # one sprint at a time (ADR-0018): the next one is assembled and waits for this one
    nxt = po.add_item("Perfil do usuário").story_id
    with pytest.raises(SprintError, match="só um sprint roda por vez"):
        master.start_sprint()
    assert ctx.store.get_story(nxt)["stage"] == Stage.BACKLOG  # checked before anything moved
    board.add(nxt)
    assert board.open_sprint().story_ids == [nxt] and board.running().id == "SP-001"
    await ctx.aclose()


async def test_draft_sprint_is_built_then_started(factory: Factory):
    ctx = make_ctx(factory)
    po, master = ProductOwnerAgent(ctx), MasterAgent(ctx)
    a, b, c = (po.add_item(t).story_id for t in ("Alfa", "Beta", "Gama"))
    kaizen = po.add_item("[Débito técnico] limpar", origin="kaizen").story_id
    board = SprintBoard(ctx.store, factory.slug)

    board.add(b)  # opens the draft
    assert board.open_sprint().story_ids == [b] and board.sprint_of(b).status == SprintStatus.OPEN
    with pytest.raises(SprintError, match="não existe"):
        board.add("S-404")
    running = po.add_item("Já andando").story_id
    po.admit(running)  # already in the pipeline: it cannot join a sprint any more
    with pytest.raises(SprintError, match="não está esperando"):
        board.add(running)
    sprint = master.start_sprint()  # uses the draft as it is
    assert sprint.story_ids == [b]
    assert ctx.store.get_story(b)["stage"] == Stage.SPEC
    assert ctx.store.get_story(a)["stage"] == Stage.BACKLOG

    def finish(sprint_id: str) -> None:  # the sprint's stories end; the next one may start
        for sid in board.get(sprint_id).story_ids:
            ctx.store.update_story(sid, stage=Stage.DONE.value)
        master.close_finished_sprints()

    finish(sprint.id)
    # default pick: the founder's cards in priority order, never the Kaizen findings
    second = master.start_sprint(limit=1)
    assert second.story_ids == [a]
    # explicit ids may include a finding; a card belongs to a single sprint
    board.add(kaizen)
    with pytest.raises(SprintError, match="já está no"):
        board.add(kaizen, sprint_id=second.id)
    finish(second.id)
    third = master.start_sprint([c])
    assert third.story_ids == [kaizen, c]
    finish(third.id)
    with pytest.raises(SprintError, match="não há histórias"):
        master.start_sprint()
    with pytest.raises(SprintError, match="não está esperando"):
        master.start_sprint([a])
    await ctx.aclose()


async def test_epic_children_join_the_parents_sprint_and_run(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "master" and "Classify the story" in messages[0].content:
            big = "Plataforma" in messages[-1].content
            return json.dumps(
                {
                    "kind": "feature",
                    "complexity": "STANDARD",
                    "children": [
                        {"title": "Cadastro", "description": "parte 1"},
                        {"title": "Cobrança", "description": "parte 2"},
                    ]
                    if big
                    else [],
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script, dry_run=True)
    ProductOwnerAgent(ctx).add_item("Plataforma completa")
    sprint = MasterAgent(ctx).start_sprint()
    assert sprint.story_ids == ["S-001"]
    await Scheduler(ctx).run()
    board = SprintBoard(ctx.store, factory.slug)
    assert board.get(sprint.id).story_ids == ["S-001", "S-002", "S-003"]
    assert load_state(ctx, "S-001").stage == Stage.DONE
    assert {load_state(ctx, s).stage for s in ("S-002", "S-003")} == {Stage.AWAITING_FOUNDER}
    assert (
        board.get(sprint.id).status == SprintStatus.RUNNING
    )  # children still wait for the founder
    await ctx.aclose()


async def test_a_fix_runs_only_inside_a_sprint(factory: Factory):
    """No "run it now" lane (ADR-0018): a Kaizen fix waits in the backlog, prioritized by the
    Product Owner, until a sprint takes it."""
    ctx = make_ctx(factory, dry_run=True)
    sid = ProductOwnerAgent(ctx).add_item("[Bug colateral] total errado", origin="kaizen").story_id
    assert not hasattr(Scheduler(ctx), "promote")
    assert await Scheduler(ctx).run() == []
    MasterAgent(ctx).start_sprint([sid])
    assert await Scheduler(ctx).run() == [sid]
    await ctx.aclose()
