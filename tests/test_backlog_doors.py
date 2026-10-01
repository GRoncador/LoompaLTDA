"""The backlog's doors (ADR-0017): nothing enters unread by the Product Owner, nothing is
dispatched outside a Sprint Meeting, and the founder's order wins. No network."""

from __future__ import annotations

import json
from typing import Any

import pytest

from loompa.agents import Conversations, KaizenAgent, MasterAgent, ProductOwnerAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.backlog import TOP, TRIAGE_KEY
from loompa.cli.main import app
from loompa.conversations import ConversationError, ConversationKind, ConversationStatus
from loompa.engine import Stage, StoryState
from loompa.engine.graph import node_intake
from loompa.factory import Factory
from loompa.llm import Message
from loompa.sprints import SprintBoard
from test_chat import init_factory, runner
from test_dashboard import client  # noqa: F401
from test_engine import factory, make_ctx  # noqa: F401

BASE = "/api/factories/demo-hq"


def order(ctx) -> list[str]:
    return [r["id"] for r in ctx.store.list_stories(ctx.slug, stage=Stage.BACKLOG)]


def events(ctx, type_: str) -> list[dict]:
    return [e for e in ctx.store.events_since(0, limit=5000) if e["type"] == type_]


def po_answers(**by_marker: Any):
    """A provider script: the Product Owner's calls whose system prompt holds a marker get the
    JSON (or the callable's result, or raise when it is an exception); the rest as in dry-run."""
    seen: dict[str, list[list[Message]]] = {k: [] for k in by_marker}

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        for marker, answer in by_marker.items():
            if role_of(messages) == "product_owner" and marker in messages[0].content:
                seen[marker].append(messages)
                if isinstance(answer, Exception):
                    raise answer
                return json.dumps(answer(messages) if callable(answer) else answer)
        return dry_run_script(model, messages, tools)

    script.seen = seen  # type: ignore[attr-defined]
    return script


# ------------------------------------------------------------------------------ order


def test_a_new_card_is_slotted_without_moving_anyone_else(factory: Factory):
    ctx = make_ctx(factory)
    po = ProductOwnerAgent(ctx)
    a, b, c = (po.add_item(t, priority=300).story_id for t in ("A", "B", "C"))  # a tie
    new = po.add_item("Novo", priority=300, after=a).story_id
    assert order(ctx) == [a, new, b, c]  # ties broke where they had to, nobody else moved
    top = po.add_item("Urgente", priority=500, after=TOP).story_id
    assert order(ctx) == [top, a, new, b, c]
    last = po.add_item("Por último", priority=1, after=c).story_id
    assert order(ctx)[-1] == last
    po.add_item("Sem lugar", priority=999, after="S-404")  # an unknown reference: number only
    assert order(ctx)[-1] == "S-007"
    assert events(ctx, "backlog.placed")
    ctx.close()


def test_a_crowded_bottom_is_spread_instead_of_overflowing(factory: Factory):
    ctx = make_ctx(factory)
    po = ProductOwnerAgent(ctx)
    a = po.add_item("A", priority=998).story_id
    b = po.add_item("B", priority=999).story_id
    new = po.add_item("Novo", priority=999, after=a).story_id
    assert order(ctx) == [a, new, b]
    assert all(r["priority"] <= 999 for r in ctx.store.list_stories(ctx.slug))
    ctx.close()


def test_dragging_pins_the_card_and_ranking_never_moves_or_passes_it(factory: Factory):
    ctx = make_ctx(factory)
    po = ProductOwnerAgent(ctx)
    ids = [po.add_item(t).story_id for t in "ABCDE"]
    a, b, c, d, e = ids
    po.reorder([a, c, b, d, e], dragged=c)  # the founder put C second
    assert ctx.store.get_story(c)["priority_pinned"] == 1
    assert ctx.store.get_story(a)["priority_pinned"] == 0
    # the Product Owner wants E, D first: they cannot pass C, so they lead their own segment
    moved = po.backlog.rerank([e, d, c, b, a])
    assert order(ctx) == [a, c, e, d, b] and set(moved) == {e, b}  # D kept its slot
    # a card the ranking left out keeps its place
    po.backlog.rerank([b, e])
    assert order(ctx) == [a, c, b, d, e]
    po.unpin(c)
    po.backlog.rerank([e, d, c, b, a])
    assert order(ctx) == [e, d, c, b, a]
    assert events(ctx, "backlog.pinned") and events(ctx, "backlog.reranked")
    ctx.close()


async def test_a_meeting_can_release_a_pinned_card_and_closes_with_a_ranking(factory: Factory):
    def rank(messages: list[Message]) -> dict:
        assert "pinned" not in messages[-1].content  # released before the ranking ran
        return {"order": ["S-003", "S-002", "S-001"], "notes": "ok"}

    def turn(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "master" and "Sprint Meeting" in messages[0].content:
            return json.dumps(
                {"reply": "Soltei.", "ops": [{"op": "update", "ref": "S-002", "pinned": False}]}
            )
        return script(model, messages, tools)

    script = po_answers(**{"rank the backlog": rank})
    ctx = make_ctx(factory, turn)
    po = ProductOwnerAgent(ctx)
    ids = [po.add_item(t).story_id for t in ("Um", "Dois", "Três")]
    po.reorder(ids, dragged="S-002")
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "pode soltar o S-002")
    assert chats.board.require(conv.id).draft.find("S-002").unpin
    await chats.commit(conv.id)
    assert ctx.store.get_story("S-002")["priority_pinned"] == 0
    assert order(ctx) == ["S-003", "S-002", "S-001"]  # the closing ranking (plan 10.5)
    assert len(script.seen["rank the backlog"]) == 1
    await ctx.aclose()


# ------------------------------------------------------------------------- Kaizen (10.1)


async def test_kaizen_findings_are_read_by_the_product_owner_before_they_become_cards(
    factory: Factory,
):
    def triage(messages: list[Message]) -> dict:
        user = messages[-1].content
        assert "- F1 · Bug colateral · " in user and "S-001" in user  # the backlog is in view
        return {
            "findings": [
                {
                    "key": "F1",
                    "duplicate_of": "",
                    "title": "Total do mês ignora estornos em cli.py",
                    "description": "O comando `total` soma estornos como gastos.",
                    "kind": "bugfix",
                    "after": "top",
                },
                {"key": "F2", "duplicate_of": "S-001", "title": "", "description": ""},
                {"key": "F3", "duplicate_of": "", "after": "F1"},
            ]
        }

    script = po_answers(**{"Triage the findings": triage})
    ctx = make_ctx(factory, script)
    po = ProductOwnerAgent(ctx)
    po.add_item("Limpar arquivos temporários do relatório", priority=300)  # S-001
    po.add_item("Exportar CSV", priority=200)  # S-002
    state = StoryState(story_id="S-009", title="Relatório mensal")
    created = await KaizenAgent(ctx).capture(
        state,
        items=[
            {"kind": "bug", "title": "estorno somado", "detail": "cli.py linha 40"},
            {"kind": "tech_debt", "title": "tmp_relatorio.csv fica na raiz", "detail": "..."},
            {"kind": "opportunity", "title": "Cache do relatório", "detail": "lento"},
        ],
    )
    assert len(script.seen["Triage the findings"]) == 1  # one call for the whole capture
    f1, f2, f3 = created
    assert f2 == "S-001" and f1 != f3  # covered by meaning: no second card
    row = ctx.store.get_story(f1)
    assert row["title"] == "[Bug colateral] Total do mês ignora estornos em cli.py"
    assert "Descoberto durante S-009" in row["description"]
    st = StoryState.from_row(row)
    assert st.kind.value == "bugfix" and st.extra[TRIAGE_KEY]["rewritten"] is True
    assert st.extra[TRIAGE_KEY]["original"].startswith("estorno somado")
    assert order(ctx)[:2] == [f1, f3]  # slotted first, the related finding right after it
    assert {e["payload"]["created_story_id"] for e in events(ctx, "kaizen.learning")} == {
        f1,
        f2,
        f3,
    }
    assert state.finding_cards == [f1, "S-001", f3]
    await ctx.aclose()


async def test_findings_go_in_as_written_when_the_product_owner_is_unavailable(factory: Factory):
    script = po_answers(**{"Triage the findings": RuntimeError("fora do ar")})
    ctx = make_ctx(factory, script)
    state = StoryState(story_id="S-009", title="Relatório")
    [sid] = await KaizenAgent(ctx).capture(
        state, items=[{"kind": "bug", "title": "estorno somado", "detail": "x"}]
    )
    row = ctx.store.get_story(sid)
    assert row["title"] == "[Bug colateral] estorno somado" and row["priority"] == 250
    assert StoryState.from_row(row).extra[TRIAGE_KEY]["reviewed"] is False
    assert events(ctx, "backlog.triage_unavailable")
    await ctx.aclose()


# ---------------------------------------------------------------------- quick story (10.6)


async def test_an_approved_quick_story_is_filed_as_the_product_owner_wrote_it(factory: Factory):
    def triage(messages: list[Message]) -> dict:
        assert "## Founder's request\ntotal errado\no mês de março soma duas vezes" in (
            messages[-1].content
        )
        return {
            "admit": True,
            "reason": "Assumi que é o comando de total mensal.",
            "title": "Corrigir o total mensal que soma março duas vezes",
            "description": "O total do mês de março conta cada gasto duas vezes.",
            "kind": "bugfix",
            "epic": "",
            "after": "S-001",
        }

    ctx = make_ctx(factory, po_answers(**{"Triage the founder's request": triage}))
    po = ProductOwnerAgent(ctx)
    po.add_item("Primeiro", priority=100)
    po.add_item("Segundo", priority=200)
    out = await Conversations(ctx).quick_story("total errado", "o mês de março soma duas vezes")
    assert out.story_id == "S-003" and out.conversation is None
    row = ctx.store.get_story("S-003")
    st = StoryState.from_row(row)
    assert row["title"] == "Corrigir o total mensal que soma março duas vezes"
    assert st.kind.value == "bugfix"
    # the founder's own words travel with the card: the spec review traces criteria to them
    assert st.founder_notes == ["total errado\no mês de março soma duas vezes"]
    assert st.extra[TRIAGE_KEY]["rewritten"] is True
    assert st.extra[TRIAGE_KEY]["original"] == "total errado\no mês de março soma duas vezes"
    assert order(ctx) == ["S-001", "S-003", "S-002"]  # slotted; the others kept their order
    # intake keeps the kind the Product Owner gave the card
    po.admit("S-003")
    state = await node_intake(ctx, StoryState.from_row(ctx.store.get_story("S-003")))
    assert state.kind.value == "bugfix"
    await ctx.aclose()


async def test_a_refused_quick_story_opens_a_review_and_the_founder_can_clarify(factory: Factory):
    answers = iter(
        [
            {
                "admit": False,
                "reason_code": "vague",
                "reason": "Não sei qual relatório.",
                "title": "Melhorar o relatório",
                "description": "melhorar o relatório",
                "kind": "feature",
                "after": "",
            },
            {
                "admit": True,
                "title": "Mostrar a média diária no relatório mensal",
                "description": "O relatório mensal mostra a média de gasto por dia.",
                "kind": "feature",
                "after": "top",
            },
        ]
    )
    script = po_answers(**{"Triage the founder's request": lambda _m: next(answers)})
    ctx = make_ctx(factory, script)
    chats = Conversations(ctx)
    out = await chats.quick_story("melhorar o relatório")
    assert out.story_id is None and ctx.store.list_stories(ctx.slug) == []  # nothing written
    conv = out.conversation
    assert conv.kind == ConversationKind.REVIEW and conv.open
    assert [t.who for t in conv.turns] == ["founder", "agent"]
    assert "vago demais" in conv.turns[1].text and "Não sei qual relatório." in conv.turns[1].text
    with pytest.raises(ConversationError, match="ainda não aprovou"):
        await chats.commit(conv.id)
    turn = await chats.say(conv.id, "o mensal: quero ver a média por dia")
    assert not turn.failed
    second = script.seen["Triage the founder's request"][1][-1].content
    assert "## Conversation since" in second and "média por dia" in second
    conv = chats.board.require(conv.id, open_only=False)
    assert conv.status == ConversationStatus.COMMITTED and conv.result["created"] == ["S-001"]
    st = StoryState.from_row(ctx.store.get_story("S-001"))
    assert st.title == "Mostrar a média diária no relatório mensal"
    assert st.founder_notes == ["melhorar o relatório", "o mensal: quero ver a média por dia"]
    assert "forced" not in st.extra[TRIAGE_KEY]
    await ctx.aclose()


async def test_the_founder_has_the_last_word_and_the_objection_stays_on_the_card(
    factory: Factory,
):
    refusal = {
        "admit": False,
        "reason_code": "contradicts",
        "reason": "A constituição pede só CLI, sem interface web.",
        "title": "Criar uma interface web",
        "description": "Uma página web para lançar gastos.",
        "kind": "feature",
        "after": "",
    }
    ctx = make_ctx(factory, po_answers(**{"Triage the founder's request": refusal}))
    chats = Conversations(ctx)
    conv = (await chats.quick_story("interface web para lançar gastos")).conversation
    with pytest.raises(ConversationError, match="diga ao Product Owner por que"):
        await chats.commit(conv.id, force=True)  # insisting means saying why
    await chats.say(conv.id, "Mudei de ideia sobre a constituição; quero a web.")
    assert chats.board.require(conv.id).open  # the Product Owner kept its refusal
    result = await chats.commit(conv.id, force=True)
    assert result.created == ["S-001"]
    st = StoryState.from_row(ctx.store.get_story("S-001"))
    record = st.extra[TRIAGE_KEY]
    assert record["forced"] is True and record["objection"] == refusal["reason"]
    assert st.title == "Criar uma interface web"  # classified by the Product Owner all the same
    conv = chats.board.require(conv.id, open_only=False)
    assert "objeção ficou registrada" in conv.turns[-1].text
    await ctx.aclose()


async def test_a_quick_story_with_an_open_title_is_a_duplicate_to_talk_about(factory: Factory):
    ctx = make_ctx(factory)
    ProductOwnerAgent(ctx).add_item("Exportar CSV")
    out = await Conversations(ctx).quick_story("exportar csv")
    assert out.story_id is None and out.triage.reason_code == "duplicate"
    assert out.triage.duplicate_of == "S-001" and "S-001" in out.conversation.turns[-1].text
    assert len(ctx.store.list_stories(ctx.slug)) == 1
    with pytest.raises(ConversationError):
        Conversations(ctx).open(ConversationKind.REVIEW)  # only born from a quick story
    await ctx.aclose()


async def test_a_quick_story_is_filed_as_written_when_the_product_owner_is_down(factory: Factory):
    ctx = make_ctx(factory, po_answers(**{"Triage the founder's request": RuntimeError("x")}))
    out = await Conversations(ctx).quick_story("Exportar PDF", "do mês")
    row = ctx.store.get_story(out.story_id)
    assert row["title"] == "Exportar PDF" and row["description"] == "do mês"
    assert StoryState.from_row(row).extra[TRIAGE_KEY]["reviewed"] is False
    await ctx.aclose()


def test_quick_story_over_the_api(client):
    r = client.post(f"{BASE}/stories", json={"title": "Exportar CSV"})
    body = r.json()
    assert body["status"] == "created" and body["id"] == "S-001"
    assert body["story"]["title"] == "Exportar CSV" and body["triage"]["admit"] is True
    r = client.post(f"{BASE}/stories", json={"title": "exportar csv!"})
    body = r.json()
    assert body["status"] == "refused" and body["id"] is None
    cid = body["conversation"]["id"]
    assert body["conversation"]["kind"] == "review" and body["triage"]["duplicate_of"] == "S-001"
    ov = client.get(f"{BASE}/overview").json()
    assert [c["id"] for c in ov["conversations"]] == [cid]
    r = client.post(f"{BASE}/conversations/{cid}/draft", json={"ops": []})
    assert r.status_code == 409  # a review changes through the conversation only
    r = client.post(f"{BASE}/conversations/{cid}/commit", json={"force": True})
    assert r.status_code == 409 and "por que" in r.json()["detail"]
    assert client.post(f"{BASE}/conversations/{cid}/discard").status_code == 200
    assert client.post(f"{BASE}/conversations", json={"kind": "review"}).status_code == 409
    assert client.post(f"{BASE}/stories", json={"title": "  "}).status_code == 409


def test_unpin_and_drag_over_the_api(client):
    ids = [client.post(f"{BASE}/stories", json={"title": t}).json()["id"] for t in "ABC"]
    client.post(f"{BASE}/backlog/order", json={"story_ids": ids[::-1], "dragged": ids[2]})
    cards = client.get(f"{BASE}/overview").json()["columns"][0]["stories"]
    assert [(c["id"], c["priority_pinned"]) for c in cards] == [
        (ids[2], True),
        (ids[1], False),
        (ids[0], False),
    ]
    assert client.post(f"{BASE}/stories/{ids[2]}/unpin").json()["priority_pinned"] is False
    assert client.post(f"{BASE}/stories/S-404/unpin").status_code == 404


def test_story_add_from_the_terminal(git_repo, hub, monkeypatch):
    init_factory(git_repo, monkeypatch)
    r = runner.invoke(app, ["story", "add", "Exportar", "CSV", "--dry-run"])
    assert r.exit_code == 0 and "S-001 gravada pelo Product Owner" in r.stdout, r.stdout
    r = runner.invoke(app, ["story", "add", "Exportar CSV", "--dry-run"])
    assert r.exit_code == 0 and "não gravou" in r.stdout and "chat resume C-001" in r.stdout
    r = runner.invoke(app, ["chat", "resume", "C-001", "--dry-run"], input="/gravar\n")
    assert "por que o card deve entrar" in r.stdout


# ------------------------------------------------------------------- sprint meeting (10.2)


async def test_a_meeting_opens_with_the_state_of_the_project(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    po.admit(po.add_item("Em andamento").story_id)
    po.add_item("[Débito técnico] x", origin="kaizen")
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    text = await chats.brief(conv.id)
    assert "1 história em andamento" in text and "1 card, 1 achados pela própria fábrica" in text
    conv = chats.board.require(conv.id)
    assert [t.who for t in conv.turns] == ["agent"] and conv.turns[0].name == "Master Loompa"
    assert events(ctx, "meeting.briefed")
    await ctx.aclose()


async def test_the_briefing_is_worded_by_the_model_from_counted_facts(factory: Factory):
    seen: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "master" and "Brief the founder" in messages[0].content:
            seen.append(messages[-1].content)
            return json.dumps({"reply": "Tudo calmo; sugiro planejar o relatório."})
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    ProductOwnerAgent(ctx).add_item("Relatório")
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    assert await chats.brief(conv.id) == "Tudo calmo; sugiro planejar o relatório."
    assert "## Backlog\n1 cards waiting" in seen[0] and 'S-001 "Relatório"' in seen[0]
    await ctx.aclose()


async def test_the_master_hands_the_sprint_to_the_product_owner(factory: Factory):
    def master(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "master" and "Sprint Meeting" in messages[0].content:
            return json.dumps(
                {
                    "reply": "Vou pedir a proposta ao Product Owner.",
                    "ops": [
                        {"op": "add", "title": "Login", "in_sprint": True},
                        {"op": "add", "title": "Tema escuro", "in_sprint": True},
                    ],
                    "consult_po": True,
                }
            )
        return script(model, messages, tools)

    script = po_answers(
        **{
            "Propose the sprint": {
                "reply": "Proponho o login e o relatório antigo; o tema escuro fica para depois.",
                "picks": [
                    {"ref": "D1", "in_sprint": True, "priority": 1, "note": "Vem primeiro."},
                    {"ref": "D2", "in_sprint": False, "note": "Não serve à meta."},
                    {"ref": "S-001", "in_sprint": True, "priority": 2, "note": "Usa o login."},
                    {"ref": "S-404", "in_sprint": True},
                ],
            }
        }
    )
    ctx = make_ctx(factory, master)
    ProductOwnerAgent(ctx).add_item("Relatório antigo")
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "Quero login e tema escuro; pode montar o sprint")
    conv = chats.board.require(conv.id)
    assert [t.name for t in conv.turns[-2:]] == ["Master Loompa", "Product Owner Loompa"]
    assert [(i.key, i.in_sprint, i.priority) for i in conv.draft.items] == [
        ("D1", True, 1),
        ("D2", False, 3),
        ("S-001", True, 2),
    ]
    assert conv.draft.items[2].note == "Usa o login."
    assert conv.draft.proposal.keys == ["D1", "D2", "S-001"] and conv.draft.proposal.reviewed
    proposal_messages = script.seen["Propose the sprint"][0][-1].content
    assert "## Current draft" in proposal_messages and "Quero login" in proposal_messages
    # the founder adjusts: fine. A card the proposal never saw: a new proposal first
    chats.edit(conv.id, [{"op": "update", "ref": "D2", "in_sprint": True}])
    chats.edit(conv.id, [{"op": "add", "title": "Card de última hora", "in_sprint": True}])
    with pytest.raises(ConversationError, match="D3 entrou no rascunho depois da proposta"):
        await chats.commit(conv.id, start_sprint=True)
    chats.edit(conv.id, [{"op": "drop", "ref": "D3"}])
    result = await chats.commit(conv.id, start_sprint=True)
    sprint = SprintBoard(ctx.store, ctx.slug).get(result.sprint_id)
    assert sorted(sprint.story_ids) == ["S-001", "S-002", "S-003"]
    await ctx.aclose()


async def test_a_proposal_that_fails_leaves_the_draft_and_says_so(factory: Factory):
    ctx = make_ctx(factory, po_answers(**{"Propose the sprint": RuntimeError("fora do ar")}))
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    with pytest.raises(ConversationError, match="vazio"):
        await chats.propose(conv.id)
    await chats.say(conv.id, "Login")
    turn = await chats.propose(conv.id)
    assert turn.failed and "Não consegui revisar" in turn.reply
    conv = chats.board.require(conv.id)
    assert conv.draft.proposal is not None and conv.draft.proposal.reviewed is False
    await ctx.aclose()


async def test_a_meeting_sees_what_the_inbox_planned_and_can_leave_it_out(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    finding = po.add_item("[Débito técnico] limpar", origin="kaizen").story_id
    po.resolve_finding(finding, "sprint")  # the founder said "sprint" on a delivery
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    assert [(i.key, i.in_sprint) for i in conv.draft.items] == [(finding, True)]
    await chats.say(conv.id, "Login")
    chats.edit(conv.id, [{"op": "update", "ref": finding, "in_sprint": False}])
    await chats.propose(conv.id)
    result = await chats.commit(conv.id, start_sprint=True)
    sprint = SprintBoard(ctx.store, ctx.slug).get(result.sprint_id)
    assert finding not in sprint.story_ids  # left out in the meeting, it was not dispatched
    assert ctx.store.get_story(finding)["stage"] == Stage.BACKLOG
    await ctx.aclose()


def test_the_cli_keeps_an_unreviewed_start(factory: Factory):
    """`loompa sprint start` (and automation, and `live` tests) still start without a meeting."""
    ctx = make_ctx(factory)
    ProductOwnerAgent(ctx).add_item("Login")
    assert MasterAgent(ctx).start_sprint().story_ids == ["S-001"]
    ctx.close()
