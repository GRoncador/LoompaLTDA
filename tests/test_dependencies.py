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
from loompa.dependencies import (
    BUILT_ON_KEY,
    WAIT_KEY,
    Gate,
    awaiting_merge,
    base_branch_for,
    find_cycle,
    merge_gate,
    merge_parked_note,
    parked_before_review,
    plan_gate,
    rows_by_id,
    still_waiting,
)
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


async def test_the_chain_specs_first_and_the_dependent_builds_on_the_approved_delivery(
    factory: Factory,
):
    """11.5: while A waits for the founder's acceptance, B goes on building on A's branch — the
    Inspector already passed it. What the acceptance still holds is B's own merge."""
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b = (po.add_item(t).story_id for t in ("Guardar centavos", "Orçamento por categoria"))
    po.set_dependencies(b, [a])
    MasterAgent(ctx).start_sprint([a, b])
    await Scheduler(ctx).run()
    # `run()` stops as soon as nothing is running: B parks at the plan gate while A is still in
    # dev, and the engine (which polls) picks it up again. Here that is the second run.
    await Scheduler(ctx).run()

    sa, sb = load_state(ctx, a), load_state(ctx, b)
    assert sa.stage == Stage.AWAITING_FOUNDER and sa.blocked_reason == "delivery"
    # B was not held at the plan gate: it specified, planned and built on A's code
    assert sb.plan_ready and WAIT_KEY in sb.extra  # parked before `review`, not blocked
    assert sb.stage != Stage.AWAITING_FOUNDER
    assert any((ctx.worktrees.get(b).path / "loompa_dryrun").iterdir())  # A's code is in it
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
    rows = rows_by_id(ctx.store, factory.slug)
    gate = plan_gate(rows, b)
    assert gate.gate == Gate.OPEN and gate.unmerged == [a]
    assert merge_gate(rows, b) == [a]  # it may build on A, never merge over it
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
    assert (
        len(
            [
                e
                for e in events(ctx, "story.stage")
                if e["story_id"] == b and e["payload"].get("node") == "node_plan"
            ]
        )
        == 1
    )  # planned once
    assert (factory.root / "loompa_dryrun").is_dir()
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


async def test_a_meeting_re_points_a_dependency_before_taking_it_out(factory: Factory):
    """tamagotchi SP-005: S-035 became covered by S-033, but S-036 depended on both, and the
    review of the running sprint could neither see nor change that."""
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b, c = (po.add_item(t).story_id for t in ("Necessidades", "Tela", "Evolução"))
    po.set_dependencies(c, [a, b])
    MasterAgent(ctx).start_sprint([a, b, c])
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    conv = chats.choose(conv.id, "current")
    assert next(i for i in conv.draft.items if i.key == c).depends_on == [a, b]
    chats.edit(
        conv.id,
        [
            {"op": "update", "ref": b, "in_sprint": False},
            {"op": "update", "ref": c, "depends_on": [a]},
        ],
    )
    result = await chats.commit(conv.id)
    assert result.withdrawn == [b] and ctx.store.get_story(c)["depends_on"] == [a]
    assert ctx.store.get_story(a)["depends_on"] == []
    await ctx.aclose()


async def test_a_review_opened_before_members_had_relations_keeps_them(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b = (po.add_item(t).story_id for t in ("Centavos", "Orçamento"))
    po.set_dependencies(b, [a])
    MasterAgent(ctx).start_sprint([a, b])
    chats = Conversations(ctx)
    conv = chats.choose(chats.open(ConversationKind.MEETING).id, "current")
    for item in conv.draft.items:  # as a meeting saved by an older build
        item.depends_on = []
    conv.draft.members_deps = False
    chats.board.save(conv)
    chats.edit(conv.id, [{"op": "update", "ref": a, "in_sprint": False}])
    with pytest.raises(ConversationError, match=f"{b} depende de {a}"):
        await chats.commit(conv.id)
    assert ctx.store.get_story(b)["depends_on"] == [a]
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


async def test_the_plan_sees_what_its_dependencies_delivered_and_can_send_the_spec_back(
    factory: Factory,
):
    """contas Sprint 2, S-049: its spec was written before S-047 and S-051 were delivered and
    undid what the founder approved there; the Architect now sees those deliveries and sends a
    spec that contradicts them back to the Spec Loompa."""
    from loompa.agents import ArchitectAgent
    from loompa.agents.architect import BOUNCED_KEY
    from loompa.dependencies import delivered_brief

    seen: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "architect":
            seen.append(messages[-1].content)
            return json.dumps(
                {"blocker": "a spec deixa de aceitar ponto decimal, que S-001 entrega"}
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    po = ProductOwnerAgent(ctx)
    a, b = (po.add_item(t).story_id for t in ("Centavos", "Testes legados"))
    po.set_dependencies(b, [a])
    assert delivered_brief(ctx.store, b) == ""  # nothing delivered yet: nothing to show
    row = ctx.store.get_story(a)
    state = {
        **row["state"],
        "acceptance": ["aceita '12.50' e '12,50'"],
        "founder_notes": ["arquivo antigo continua sendo lido"],
        "worker_summary": "T1: centavos",
    }
    ctx.store.update_story(a, stage=Stage.DONE.value, state=state)
    brief = delivered_brief(ctx.store, b)
    assert "S-001: Centavos" in brief and "aceita '12.50' e '12,50'" in brief
    assert "arquivo antigo continua sendo lido" in brief and "T1: centavos" in brief

    st = load_state(ctx, b)
    res = await ArchitectAgent(ctx).run(st)
    assert not res.ok and st.extra[BOUNCED_KEY] and "ponto decimal" in st.handoff["plan"]
    assert "## What the stories this one builds on delivered" in seen[0]
    await ctx.aclose()


async def test_what_the_founder_writes_on_approving_reaches_the_stories_built_on_it(
    factory: Factory,
):
    """tamagotchi S-017 was approved with "reabrir_app goes to the persistence port"; S-018,
    built on it, never saw that and deleted the function without porting it."""
    from loompa.dependencies import delivered_brief

    ctx = make_ctx(factory, dry_run=True)
    MasterAgent(ctx).start_sprint([ProductOwnerAgent(ctx).add_item("Recálculo").story_id])
    await Scheduler(ctx).run()
    sid = "S-001"
    delivery = ctx.store.get_message(load_state(ctx, sid).blocked_message_id)
    await Scheduler(ctx).aanswer(
        delivery.id, FounderAnswer(option_key="approve", text="o reabrir_app fica para a S-002")
    )
    po = ProductOwnerAgent(ctx)
    later = po.add_item("Persistência").story_id
    po.set_dependencies(later, [sid])
    assert "Ao aprovar: o reabrir_app fica para a S-002" in delivered_brief(ctx.store, later)
    await ctx.aclose()


async def test_a_rejected_delivery_holds_the_story_built_on_it(factory: Factory):
    """11.5: B builds on A's branch while A waits for the founder. If the founder asks for
    changes, A goes back to `dev` and stops being "awaiting" — but B still carries A's commits,
    so it must not merge until A is really in the base."""
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b = (po.add_item(t).story_id for t in ("Guardar centavos", "Orçamento por categoria"))
    po.set_dependencies(b, [a])
    MasterAgent(ctx).start_sprint([a, b])
    await Scheduler(ctx).run()
    await Scheduler(ctx).run()  # the engine polls; `run()` stops as soon as nothing runs

    sb = load_state(ctx, b)
    assert sb.extra[BUILT_ON_KEY] == a and sb.stage == Stage.REVIEW  # parked on A's branch

    delivery = next(m for m in ctx.store.list_messages(factory.slug, "pending") if m.story_id == a)
    await Scheduler(ctx).aanswer(
        delivery.id, FounderAnswer(option_key="changes", text="faltou arredondar")
    )
    rows = rows_by_id(ctx.store, factory.slug)
    assert rows[a]["stage"] != Stage.DONE and not awaiting_merge(rows[a])  # back to work
    assert merge_gate(rows, b) == [a]  # B keeps waiting: it carries A's commits
    assert still_waiting(rows, rows[b])
    await ctx.aclose()


async def test_a_chain_of_three_keeps_moving_while_the_founder_decides_the_first(
    factory: Factory,
):
    """11.5: A delivered and waiting for the founder, B built on A's branch and C on B's. The
    founder's answer is asked once, for A; the other two are finished, waiting only to merge."""
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b, c = (
        po.add_item(t).story_id
        for t in ("Guardar centavos", "Orçamento por categoria", "Relatório do mês")
    )
    po.set_dependencies(b, [a])
    po.set_dependencies(c, [b])
    MasterAgent(ctx).start_sprint([a, b, c])
    for _ in range(4):  # the engine polls; each `run()` stops as soon as nothing runs
        await Scheduler(ctx).run()

    rows = rows_by_id(ctx.store, factory.slug)
    assert awaiting_merge(rows[a])  # the only one asking the founder anything
    assert [m.story_id for m in ctx.store.list_messages(factory.slug, "pending")] == [a]
    for sid, dep in ((b, a), (c, b)):
        state = load_state(ctx, sid)
        assert state.plan_ready and parked_before_review(rows[sid])
        assert state.extra[BUILT_ON_KEY] == dep  # each one written on the branch before it
        assert merge_gate(rows, sid) == [dep]  # and none of them may merge first

    # the panel puts the three where the founder looks for what their answer unblocks
    from loompa.dashboard.app import board_column

    assert [board_column(rows[sid]) for sid in (a, b, c)] == ["AWAITING_FOUNDER"] * 3
    await ctx.aclose()


def test_two_pending_deliveries_in_a_chain_let_the_next_story_start():
    """11.5 (tamagotchi S-056, 04/10): S-056 depended on S-054 and S-055, both built and green.
    "More than one pending delivery" held it — but S-055 had been written on top of S-054, so its
    branch carries both. The tip of the chain is a base that leaves nothing out."""
    ROUTE = ["spec", "spec_review", "plan", "dev", "test", "review"]
    rows = {
        "S-054": {
            "id": "S-054",
            "stage": Stage.AWAITING_FOUNDER,
            "depends_on": [],
            "branch": "b54",
            "state": {
                "blocked_reason": "delivery",
                "qa_verdict": "PASS",
                "route": ROUTE,
                "resume_phase": "review",
            },
        },
        "S-055": {
            "id": "S-055",
            "stage": Stage.REVIEW,
            "depends_on": ["S-054"],
            "branch": "b55",
            "state": {
                "qa_verdict": "PASS",
                "route": ROUTE,
                "phase": "review",
                "extra": {BUILT_ON_KEY: "S-054", WAIT_KEY: {"unmerged": ["S-054"]}},
            },
        },
        "S-056": {
            "id": "S-056",
            "stage": Stage.PLAN,
            "depends_on": ["S-054", "S-055"],
            "branch": "",
            "state": {"route": ROUTE, "phase": "plan"},
        },
    }
    gate = plan_gate(rows, "S-056")
    assert gate.gate == Gate.OPEN and gate.unmerged == ["S-054", "S-055"]
    assert base_branch_for(rows, "S-056") == "b55"  # the tip, which carries S-054 too
    assert merge_gate(rows, "S-056") == ["S-054", "S-055"]  # still merges after both

    # the same two, but separate lines of work (S-055 built on a third story): no branch carries
    # both, so the gate asks for an integration branch instead of waiting (ADR-0027)
    rows["S-055"]["depends_on"] = ["S-050"]
    rows["S-055"]["state"]["extra"][BUILT_ON_KEY] = "S-050"
    apart = plan_gate(rows, "S-056")
    assert apart.gate == Gate.OPEN and apart.integrate == ["S-054", "S-055"]
    assert apart.pending == [] and apart.founder == []
    assert base_branch_for(rows, "S-056") == ""


def test_the_parked_card_says_nothing_is_missing():
    """11.5 (tamagotchi S-055, 04/10): a story finished and green, parked before `review` until
    the delivery it was built on is accepted, was shown with a closed padlock over S-054 — the
    card's "a dependency is missing" — because its note repeated the dependency in `pending` and
    `founder`. Nothing is missing there: the code is written and the Inspector passed it."""
    note = merge_parked_note(["S-054"], "pronta · aguardando o seu aceite de S-054")
    assert note["unmerged"] == ["S-054"]  # what it waits for, and what names it on the card
    assert not note["pending"] and not note["founder"] and not note["chain"] and not note["gone"]
    assert note["gate"] == Gate.WAIT.value  # it is waiting: no Loompa is working on it

    ROUTE = ["spec", "spec_review", "plan", "dev", "test", "review"]
    rows = {
        "S-054": {
            "id": "S-054",
            "stage": Stage.AWAITING_FOUNDER,
            "depends_on": [],
            "branch": "b54",
            "state": {
                "blocked_reason": "delivery",
                "qa_verdict": "PASS",
                "route": ROUTE,
                "resume_phase": "review",
            },
        },
        "S-055": {
            "id": "S-055",
            "stage": Stage.REVIEW,
            "depends_on": ["S-054"],
            "branch": "b55",
            "state": {
                "qa_verdict": "PASS",
                "route": ROUTE,
                "phase": "review",
                "extra": {BUILT_ON_KEY: "S-054", WAIT_KEY: note},
            },
        },
    }
    assert parked_before_review(rows["S-055"]) and still_waiting(rows, rows["S-055"])

    # a row parked by an older version carried the dependency in every field; the panel reads it
    # as it is written today, so the padlock opens without a migration
    legacy = note | {"pending": ["S-054"], "founder": ["S-054"]}
    rows["S-055"]["state"]["extra"][WAIT_KEY] = legacy
    assert still_waiting(rows, rows["S-055"])  # the engine only ever read `unmerged`
    shown = merge_parked_note(legacy["unmerged"], legacy["label"]) | {"ready": True}
    assert not shown["pending"] and not shown["founder"]
    assert shown["unmerged"] == ["S-054"] and shown["ready"]


def test_a_story_waits_while_the_delivery_it_stands_on_is_reworked():
    """12.1, tamagotchi S-061: it kept fixing tests on top of S-060 while S-060, failed by the
    judge, was replanned from scratch — 102 minutes, three conflicts and an escalation."""
    from loompa.dependencies import parent_in_rework

    ROUTE = ["spec", "plan", "dev", "test", "review"]
    rows = {
        "S-060": {"id": "S-060", "stage": Stage.DEV, "depends_on": [], "branch": "b60",
                  "state": {"route": ROUTE, "phase": "dev"}},
        "S-061": {"id": "S-061", "stage": Stage.DEV, "depends_on": ["S-060"], "branch": "b61",
                  "state": {"route": ROUTE, "phase": "dev", "extra": {BUILT_ON_KEY: "S-060"}}},
    }  # fmt: skip
    assert parent_in_rework(rows, "S-061") == "S-060"
    rows["S-061"]["state"]["extra"][WAIT_KEY] = {"gate": "wait", "pending": ["S-060"]}
    assert still_waiting(rows, rows["S-061"])  # parked in `dev` while S-060 is reworked
    rows["S-060"]["stage"] = Stage.AWAITING_FOUNDER  # delivered again, green
    rows["S-060"]["state"].update(blocked_reason="delivery", qa_verdict="PASS")
    assert not parent_in_rework(rows, "S-061") and not still_waiting(rows, rows["S-061"])
    rows["S-060"]["stage"] = Stage.REVIEW  # accepted and merging: not rework
    assert not parent_in_rework(rows, "S-061")
