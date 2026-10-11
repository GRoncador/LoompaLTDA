"""Product management: phases, epics, discovery and the PM (plan 16.2-16.8, ADR-0041)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from loompa.agents import Conversations, ProductOwnerAgent
from loompa.comms import FounderAnswer
from loompa.company import filled
from loompa.conversations import ConversationError, ConversationKind
from loompa.engine import Scheduler
from loompa.factory import Factory
from loompa.lifecycle import Lifecycle
from test_engine import factory, make_ctx  # noqa: F401


def test_a_personal_product_has_one_phase_and_a_commercial_one_five(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    lc = Lifecycle(ctx)
    assert [p["key"] for p in lc.phases()] == ["in_use"]
    assert lc.current()["name"] == "Em uso" and lc.next_phase() is None
    ctx.config.factory.kind = "commercial"
    keys = [p["key"] for p in lc.phases()]
    assert keys == ["discovery", "mvp", "validation", "growth", "maturity"]
    assert lc.current()["key"] == "discovery"  # in_use is kept in the table, out of the cycle


async def test_moving_phase_is_the_founders_decision_in_the_inbox(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    ctx.config.factory.kind = "commercial"
    lc = Lifecycle(ctx)
    lc.set_phase("discovery", goal="entender o problema", exit_criterion="5 entrevistas")
    msg = lc.propose_move("Simulação: as 5 entrevistas foram feitas.")
    assert msg is not None and "Passar para MVP" in msg.options[0].label
    assert lc.propose_move("de novo") is None  # asked once
    assert lc.current()["key"] == "discovery"  # nothing moves on its own
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="move"))
    assert lc.current()["key"] == "mvp"
    assert {p["key"]: p["status"] for p in lc.phases()}["discovery"] == "done"


async def test_epics_are_born_from_the_pm_with_the_founders_ok_and_cards_link_to_them(
    factory: Factory,
):
    from loompa.agents.product_manager import ProductManagerAgent

    ctx = make_ctx(factory, dry_run=True)
    lc = Lifecycle(ctx)
    proposals = await ProductManagerAgent(ctx).propose_epics()
    assert proposals[0]["name"] == "Primeiro uso"
    proposals.append({"name": "Relatórios", "goal": "ver o mês", "metric": "", "why": ""})
    msg = lc.ask_epics(proposals)
    assert [q.id for q in msg.questions] == ["e1", "e2"] and lc.epics() == []
    await Scheduler(ctx).aanswer(
        msg.id, FounderAnswer(option_key="answer", answers={"e1": "create", "e2": "skip"})
    )
    assert [e["name"] for e in lc.epics()] == ["Primeiro uso"]
    epic = lc.epics()[0]
    po = ProductOwnerAgent(ctx)
    sid = po.add_item("Tela de boas-vindas", epic="primeiro uso").story_id
    other = po.add_item("Exportar CSV", epic="Outro grupo").story_id
    assert ctx.store.get_story(sid)["epic_id"] == epic["id"]
    assert ctx.store.get_story(other)["epic_id"] == ""
    prog = lc.progress()
    assert (prog.done, prog.total, prog.percent) == (0, 1, 0)
    ctx.store.update_story(sid, stage="DONE")
    assert lc.progress().percent == 100
    assert "Primeiro uso" in lc.brief()


async def test_discovery_is_for_commercial_products_and_saved_only_with_the_ok(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    await chats.say(conv.id, "Um app para dividir contas de casa")
    with pytest.raises(ConversationError, match="comerciais"):
        await chats.discover(conv.id)
    ctx.config.factory.kind = "commercial"
    turn = await chats.discover(conv.id)
    msg = ctx.store.get_message(turn.extra["message_id"])
    assert "Lean Canvas" in msg.title and not msg.executive_audit()
    loompa = factory.paths.loompa
    assert not filled(loompa, "hypotheses")
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="save"))
    assert "Acreditamos que" in filled(loompa, "hypotheses")
    assert "Problema" in filled(loompa, "business")


def test_the_panel_shows_and_edits_the_product(factory: Factory, hub):
    from loompa.dashboard.app import create_app

    factory.config.factory.kind = "commercial"
    factory.save()
    with TestClient(create_app(dry_run=True, run_engine=False, store=hub)) as c:
        slug = factory.slug
        view = c.get(f"/api/factories/{slug}/product").json()
        assert view["discovery"] and view["phases"][0]["status"] == "current"
        r = c.put(
            f"/api/factories/{slug}/product/phases/discovery",
            json={"exit_criterion": "10 pessoas usando por 2 semanas"},
        )
        phase = r.json()["phases"][0]
        assert phase["exit_criterion"].startswith("10 pessoas") and "Simulação" in phase["measure"]
        r = c.post(f"/api/factories/{slug}/product/epics/propose")
        assert r.json()["proposed"] == 1
        assert c.post(f"/api/factories/{slug}/product/review").json()["exit_met"] == "unknown"
        overview = c.get(f"/api/factories/{slug}/overview").json()
        assert overview["phase"]["name"] == "Descoberta"
