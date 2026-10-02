"""Dependencies between stories and the gate before `plan` (ADR-0021). No network."""

from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from loompa.agents import Conversations, MasterAgent, ProductOwnerAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.backlog import BacklogError
from loompa.comms import FounderAnswer
from loompa.conversations import (
    ConversationError,
    ConversationKind,
    Draft,
    DraftItem,
    OpenCard,
    apply_ops,
)
from loompa.dependencies import WAIT_KEY, Gate, find_cycle, plan_gate, rows_by_id
from loompa.engine import Scheduler, Stage, load_state
from loompa.factory import Factory
from loompa.llm import Message
from loompa.sprints import SprintError
from loompa.store import Store
from test_engine import factory, make_ctx  # noqa: F401


def events(ctx, type_: str) -> list[dict]:
    return [e for e in ctx.store.events_since(0, limit=5000) if e["type"] == type_]


def first(ctx, pred) -> int:
    rows = ctx.store.events_since(0, limit=5000)
    return next(i for i, e in enumerate(rows) if pred(e))


# ------------------------------------------------------------------------------ model


def test_cycles_are_found_with_the_path_that_closes_them():
    assert find_cycle({"A": ["B"], "B": ["C"], "C": []}) is None
    assert find_cycle({"A": ["B"], "B": ["C"], "C": ["A"]}) == ["A", "B", "C", "A"]
    assert find_cycle({"A": ["A"]}) == ["A", "A"]


def test_an_old_state_db_gets_the_column_on_open(tmp_path):
    path = tmp_path / "state.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE stories (id TEXT PRIMARY KEY, factory TEXT NOT NULL, title TEXT NOT NULL, "
        "description TEXT NOT NULL DEFAULT '', epic TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL, "
        "priority INTEGER NOT NULL DEFAULT 100, origin TEXT NOT NULL DEFAULT 'founder', "
        "state_json TEXT NOT NULL DEFAULT '{}', attempts_tier2 INTEGER NOT NULL DEFAULT 0, "
        "attempts_tier1 INTEGER NOT NULL DEFAULT 0, branch TEXT NOT NULL DEFAULT '', "
        "worktree TEXT NOT NULL DEFAULT '', blocked_message_id TEXT, "
        "cost_usd REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO stories (id, factory, title, stage, created_at, updated_at) "
        "VALUES ('S-001', 'f', 'Velha', 'BACKLOG', 'x', 'x')"
    )
    conn.commit()
    conn.close()
    store = Store(path)
    assert store.get_story("S-001")["depends_on"] == []
    store.close()


async def test_only_the_product_owner_writes_relations_and_never_a_cycle(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b, c = (po.add_item(t).story_id for t in ("Centavos", "Orçamento", "Relatório"))
    assert po.set_dependencies(b, [a, a.lower()]) == [a]  # normalised, no repeats
    po.set_dependencies(c, [b])
    with pytest.raises(BacklogError, match="ciclo"):
        po.set_dependencies(a, [c])  # a → c → b → a
    with pytest.raises(BacklogError, match="si mesma"):
        po.set_dependencies(a, [a])
    with pytest.raises(BacklogError, match="não existe"):
        po.set_dependencies(a, ["S-999"])
    assert ctx.store.get_story(b)["depends_on"] == [a] and events(ctx, "backlog.depends")
    await ctx.aclose()


# ------------------------------------------------------------------------------ draft


def card(sid: str, title: str, deps: tuple[str, ...] = ()) -> OpenCard:
    return OpenCard(id=sid, title=title, stage=Stage.BACKLOG, priority=3, depends_on=deps)


def test_a_draft_resolves_relations_and_refuses_a_cycle():
    draft = Draft(
        items=[DraftItem(key="D1", title="Centavos"), DraftItem(key="D2", title="Orçamento")],
        next_key=3,
    )
    cards = {"S-004": card("S-004", "Exportar"), "S-005": card("S-005", "Painel", ("S-004",))}
    report = apply_ops(
        draft,
        [
            {"op": "update", "ref": "D2", "depends_on": ["d1", "S-004", "S-999", "D2"]},
            {"op": "update", "ref": "D1", "depends_on": ["D2"]},  # D1 → D2 → D1
        ],
        cards,
    )
    assert draft.find("D2").depends_on == ["D1", "S-004"]
    assert draft.find("D1").depends_on == []  # D1 → D2 → D1 was refused whole
    assert report.cycles and any("ciclo" in n for n in report.ignored)
    assert any("S-999" in n for n in report.ignored) and any(
        "si mesmo" in n for n in report.ignored
    )
    cyc = apply_ops(draft, [{"op": "update", "ref": "S-004", "depends_on": ["S-005"]}], cards)
    assert cyc.cycles == ["S-004 → S-005 → S-004"]  # the backlog's own relations count too
    apply_ops(draft, [{"op": "drop", "ref": "D1"}], cards)
    assert draft.find("D2").depends_on == ["S-004"]  # nobody depends on a card that left


# ------------------------------------------------------------------- the sprint proposal


def meeting_script(proposals: list[dict], seen: list[str] | None = None):
    queue = list(proposals)

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "product_owner" and "Propose the sprint" in messages[0].content:
            if seen is not None:
                seen.append(messages[-1].content)
            return json.dumps(queue.pop(0))
        return dry_run_script(model, messages, tools)

    return script


async def test_the_proposal_brings_a_dependency_into_the_sprint_and_the_commit_saves_it(
    factory: Factory,
):
    proposal = {
        "reply": "O orçamento depende dos centavos.",
        "picks": [{"ref": "D2", "in_sprint": True, "priority": 2, "depends_on": ["D1"]}],
    }
    ctx = make_ctx(factory, meeting_script([proposal]))
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "Guardar valores em centavos\nOrçamento por categoria")
    chats.edit(conv.id, [{"op": "update", "ref": "D1", "in_sprint": False}])
    turn = await chats.propose(conv.id)
    conv = chats.board.require(conv.id)
    assert conv.draft.find("D2").depends_on == ["D1"]
    assert conv.draft.find("D1").in_sprint  # it came along with the card that needs it
    assert any("D1 entrou junto" in c for c in turn.changes)

    chats.edit(conv.id, [{"op": "update", "ref": "D1", "in_sprint": False}])
    with pytest.raises(ConversationError, match="D2 depende de D1"):
        await chats.commit(conv.id, start_sprint=True)
    chats.edit(conv.id, [{"op": "update", "ref": "D1", "in_sprint": True}])
    result = await chats.commit(conv.id, start_sprint=True)
    assert result.sprint_id == "SP-001"
    assert ctx.store.get_story("S-002")["depends_on"] == ["S-001"]
    await ctx.aclose()


async def test_a_proposal_that_closes_a_cycle_is_asked_again(factory: Factory):
    seen: list[str] = []
    cyclic = {
        "reply": "x",
        "picks": [
            {"ref": "D1", "in_sprint": True, "depends_on": ["D2"]},
            {"ref": "D2", "in_sprint": True, "depends_on": ["D1"]},
        ],
    }
    fixed = {
        "reply": "Corrigido.",
        "picks": [{"ref": "D2", "in_sprint": True, "depends_on": ["D1"]}],
    }
    ctx = make_ctx(factory, meeting_script([cyclic, fixed], seen))
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "Centavos\nOrçamento")
    await chats.propose(conv.id)
    conv = chats.board.require(conv.id)
    assert len(seen) == 2 and "Relations refused" in seen[1] and "Relations refused" not in seen[0]
    assert conv.draft.find("D2").depends_on == ["D1"] and conv.draft.find("D1").depends_on == []
    await ctx.aclose()


async def test_a_sprint_never_starts_without_a_dependency(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b = (po.add_item(t).story_id for t in ("Centavos", "Orçamento"))
    po.set_dependencies(b, [a])
    with pytest.raises(SprintError, match=f"{b} depende de {a}"):
        MasterAgent(ctx).start_sprint([b])
    assert ctx.store.get_story(b)["stage"] == Stage.BACKLOG  # checked before anything moved
    await ctx.aclose()


# --------------------------------------------------------------------------- the engine


async def test_the_chain_specs_first_and_the_dependent_plans_on_the_delivered_code(
    factory: Factory,
):
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b = (po.add_item(t).story_id for t in ("Guardar centavos", "Orçamento por categoria"))
    po.set_dependencies(b, [a])
    MasterAgent(ctx).start_sprint([a, b])
    await Scheduler(ctx).run()

    sa, sb = load_state(ctx, a), load_state(ctx, b)
    assert sa.stage == Stage.AWAITING_FOUNDER and sa.blocked_reason == "delivery"
    assert sb.stage != Stage.AWAITING_FOUNDER and WAIT_KEY in sb.extra  # parked, not blocked
    assert sb.spec_ready and not sb.plan_ready
    # the chain's specs came first: A planned only after B's spec was approved
    b_spec = first(ctx, lambda e: e["type"] == "spec.approved" and e["story_id"] == b)
    a_plan = first(
        ctx,
        lambda e: (
            e["type"] == "story.stage"
            and e["story_id"] == a
            and e["payload"].get("node") == "node_plan"
        ),
    )
    assert b_spec < a_plan
    gate = plan_gate(rows_by_id(ctx.store, factory.slug), b)
    assert gate.gate == Gate.WAIT and gate.label() == f"aguardando {a}, que espera você"
    delivery = next(m for m in ctx.store.list_messages(factory.slug, "pending") if m.story_id == a)
    assert f"A {b} depende desta" in delivery.impact  # one answer unblocks both
    assert not [m for m in ctx.store.list_messages(factory.slug) if m.story_id == b]

    await Scheduler(ctx).aanswer(delivery.id, FounderAnswer(option_key="approve"))
    assert load_state(ctx, a).stage == Stage.DONE
    await Scheduler(ctx).run()
    sb = load_state(ctx, b)
    assert sb.stage == Stage.AWAITING_FOUNDER and sb.blocked_reason == "delivery"
    assert WAIT_KEY not in sb.extra
    assert len([e for e in events(ctx, "spec.approved") if e["story_id"] == b]) == 1  # not redone
    # planned on the code A delivered: its merged file is in B's worktree
    assert (factory.root / "loompa_dryrun").is_dir()
    assert any((ctx.worktrees.get(b).path / "loompa_dryrun").iterdir())
    await ctx.aclose()


async def test_a_cancelled_dependency_is_the_founders_call(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b, c = (po.add_item(t).story_id for t in ("Centavos", "Orçamento", "Relatório"))
    po.set_dependencies(b, [a])
    po.set_dependencies(c, [a])
    MasterAgent(ctx).start_sprint([a, b, c])
    po.set_status(a, Stage.CANCELLED)
    await Scheduler(ctx).run()
    for sid in (b, c):
        st = load_state(ctx, sid)
        assert st.stage == Stage.AWAITING_FOUNDER and st.blocked_reason == "dependency"
    msgs = {m.story_id: m for m in ctx.store.list_messages(factory.slug, "pending")}
    assert {o.key for o in msgs[b].options} == {"skip", "detach", "drop"}
    assert msgs[b].executive_audit() == []

    st = await Scheduler(ctx).aanswer(msgs[b].id, FounderAnswer(option_key="detach"))
    assert st.phase == "spec" and any("saiu do sprint" in n for n in st.founder_notes)
    assert ctx.store.get_story(b)["depends_on"] == []
    st = await Scheduler(ctx).aanswer(msgs[c].id, FounderAnswer(option_key="skip"))
    assert st.stage == Stage.BACKLOG and ctx.store.get_story(c)["depends_on"] == []
    await Scheduler(ctx).run()
    assert load_state(ctx, b).blocked_reason == "delivery"  # went on without it
    await ctx.aclose()


async def test_a_meeting_cannot_take_out_a_dependency_and_leave_its_dependent(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b = (po.add_item(t).story_id for t in ("Centavos", "Orçamento"))
    po.set_dependencies(b, [a])
    MasterAgent(ctx).start_sprint([a, b])
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    chats.choose(conv.id, "current")
    chats.edit(conv.id, [{"op": "update", "ref": a, "in_sprint": False}])
    with pytest.raises(ConversationError, match=f"{b} depende de {a}"):
        await chats.commit(conv.id)
    chats.edit(conv.id, [{"op": "update", "ref": b, "in_sprint": False}])
    result = await chats.commit(conv.id)
    assert sorted(result.withdrawn) == [a, b]
    await ctx.aclose()


async def test_a_dependent_spec_reads_what_its_dependency_decided(factory: Factory):
    """contas Sprint 2: the spec of the budget-in-summary story asked the founder where the
    limits are stored, a decision already in the spec of the story it builds on."""
    from loompa.agents.product import ProductAgent
    from loompa.speckit import story_dir

    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b = (po.add_item(t).story_id for t in ("Definir limite", "Mostrar orçamento"))
    po.set_dependencies(b, [a])
    spec = story_dir(ctx.root, a).spec
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("# Spec\nOs limites ficam em limites.json, um por categoria.\n", "utf-8")
    text = ProductAgent(ctx)._builds_on(load_state(ctx, b))
    assert f"### {a}: Definir limite" in text and "limites.json" in text
    assert ProductAgent(ctx)._builds_on(load_state(ctx, a)) == ""  # no dependency, no section
    await ctx.aclose()


async def test_an_epic_split_hands_its_dependents_to_the_children_and_never_splits_again(
    factory: Factory,
):
    """contas Sprint 2: restarting the cents story turned it into an epic, the parent was DONE at
    once and the import story that waited on it was planned on the old code; one child was then
    split again into three that duplicated its siblings."""

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if "Classify the story" in messages[0].content:  # always wants to split, children too
            return json.dumps(
                {
                    "kind": "feature",
                    "complexity": "COMPLEX",
                    "children": [
                        {"title": "Modelo em centavos", "description": "parte 1"},
                        {"title": "Comandos em centavos", "description": "parte 2"},
                    ],
                    "reason": "grande",
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    po = ProductOwnerAgent(ctx)
    base, cents, imports = (
        po.add_item(t).story_id for t in ("Base", "Migrar para centavos", "Importar fatura")
    )
    po.set_dependencies(cents, [base])
    po.set_dependencies(imports, [cents])
    MasterAgent(ctx).start_sprint([base, cents, imports])
    from loompa.engine.graph import node_intake

    await node_intake(ctx, load_state(ctx, cents))
    kids = [s for s in ctx.store.list_stories(factory.slug) if s["origin"] == "epic"]
    ids = [k["id"] for k in kids]
    assert len(ids) == 2
    assert ctx.store.get_story(imports)["depends_on"] == ids  # waits for every part now
    assert all(k["depends_on"] == [base] for k in kids)  # the parts still wait for the base
    child = await node_intake(ctx, load_state(ctx, ids[0]))
    assert child.stage != Stage.DONE and "children" not in child.extra  # not split again
    assert len([s for s in ctx.store.list_stories(factory.slug) if s["origin"] == "epic"]) == 2
    await ctx.aclose()
