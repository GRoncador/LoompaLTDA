"""Conversations: chat sessions with a backlog/sprint draft (ADR-0010). No network."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
import respx

from loompa.agents import AnalystAgent, Conversations, MasterAgent, ProductOwnerAgent
from loompa.agents.conversation import parse_turn
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.comms import MessageKind
from loompa.config.schema import ModelCandidate
from loompa.conversations import (
    MAX_ITEMS,
    ConversationBoard,
    ConversationError,
    ConversationKind,
    ConversationStatus,
    Draft,
    OpenCard,
    apply_ops,
)
from loompa.engine import EngineContext, Scheduler, Stage
from loompa.factory import Factory
from loompa.llm import Message, MockProvider, ModelRouter, OpenAICompatibleProvider
from loompa.sprints import SprintBoard, SprintStatus
from test_engine import factory, make_ctx  # noqa: F401


def events(ctx, type_: str) -> list[dict]:
    return [e for e in ctx.store.events_since(0, limit=5000) if e["type"] == type_]


def turn_script(*answers: dict[str, Any], seen: list[list[Message]] | None = None):
    """A provider script: answers the conversation calls in order, the rest as in dry-run."""
    queue = list(answers)

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        system = messages[0].content
        if role == "master" and "Sprint Meeting" in system or "Brainstorming session" in system:
            if seen is not None:
                seen.append(messages)
            return json.dumps(queue.pop(0))
        return dry_run_script(model, messages, tools)

    return script


def add(title: str, **fields: Any) -> dict[str, Any]:
    return {"op": "add", "title": title, "description": f"{title} (detalhes)", **fields}


# ------------------------------------------------------------------------- draft ops


def card(story_id: str, title: str, stage: str = Stage.BACKLOG, priority: int = 3) -> OpenCard:
    return OpenCard(id=story_id, title=title, stage=stage, priority=priority)


def test_ops_add_update_drop_and_goal():
    draft = Draft()
    report = apply_ops(
        draft,
        [
            add("Login com Google", priority=2, in_sprint=True, epic="acesso"),
            add("Exportar CSV"),
            {"op": "update", "ref": "d2", "priority": "1", "in_sprint": "sim"},
            {"op": "drop", "ref": "D1"},
            {"op": "goal", "text": "Primeira entrega"},
        ],
        {},
    )
    assert [(i.key, i.priority, i.in_sprint) for i in draft.items] == [("D2", 1, True)]
    assert draft.goal == "Primeira entrega" and draft.next_key == 3  # keys are never reused
    assert len(report.changes) == 5 and report.ignored == []


def test_ops_never_raise_on_garbage_and_report_what_they_skipped():
    draft = Draft()
    report = apply_ops(
        draft,
        ["texto", 7, {"op": "explode"}, {"op": "add"}, {"op": "drop", "ref": "D9"}, {}],
        {},
    )
    assert draft.items == []
    assert len(report.ignored) == 4  # unknown op, no title, unknown ref, empty op


def test_a_repeated_title_refines_the_card_instead_of_doubling_it():
    draft = Draft()
    apply_ops(draft, [add("Exportar CSV", priority=4)], {})
    apply_ops(draft, [add("exportar  csv!", priority=1, in_sprint=True)], {})
    assert len(draft.items) == 1 and draft.items[0].priority == 1 and draft.items[0].in_sprint


def test_a_title_that_matches_the_backlog_becomes_a_reference_to_that_card():
    cards = {
        "S-004": card("S-004", "Relatório mensal", priority=2),
        "S-009": card("S-009", "Cobrança", Stage.DEV),
    }
    draft = Draft()
    report = apply_ops(draft, [add("Relatório mensal", in_sprint=True), add("Cobrança")], cards)
    assert [(i.key, i.story_id, i.in_sprint, i.priority) for i in draft.items] == [
        ("S-004", "S-004", True, 2)
    ]
    assert "já existe e está em andamento" in report.ignored[0]  # work in progress can't join


def test_an_existing_card_can_be_pulled_in_but_only_its_priority_and_sprint_change():
    cards = {"S-004": card("S-004", "Relatório mensal"), "S-009": card("S-009", "Obra", Stage.DEV)}
    draft = Draft()
    report = apply_ops(
        draft,
        [
            {"op": "update", "ref": "S-004", "in_sprint": True, "priority": 1, "title": "Outro"},
            {"op": "update", "ref": "S-009", "in_sprint": True},
        ],
        cards,
    )
    item = draft.find("S-004")
    assert item and item.title == "Relatório mensal" and item.in_sprint and item.priority == 1
    assert draft.find("S-009") is None
    assert any("pertence ao backlog" in n for n in report.ignored)
    assert any("já está em andamento" in n for n in report.ignored)
    apply_ops(draft, [{"op": "drop", "ref": "S-004"}], cards)
    assert draft.items == []  # dropping a pulled card leaves it in the backlog


def test_a_draft_holds_a_bounded_number_of_cards():
    draft = Draft()
    report = apply_ops(draft, [add(f"História {n}") for n in range(MAX_ITEMS + 3)], {})
    assert len(draft.items) == MAX_ITEMS and len(report.ignored) == 3


def test_parse_turn_accepts_the_older_meeting_shape():
    reply, ops, questions = parse_turn(
        {"stories": [{"title": "A"}], "clarifications": "Qual prazo?", "message": "ok"}
    )
    assert reply == "ok" and ops == [{"op": "add", "title": "A"}] and questions == ["Qual prazo?"]


# ------------------------------------------------------------------------ persistence


def test_sessions_are_persisted_and_listed(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    board = ConversationBoard(ctx.store, factory.slug)
    first = board.create(ConversationKind.MEETING)
    second = board.create(ConversationKind.BRAINSTORM, "Ideias de onboarding")
    assert (first.id, second.id) == ("C-001", "C-002")
    apply_ops(first.draft, [add("Login")], {})
    board.save(first)
    again = board.get("C-001")
    assert again.draft.items[0].title == "Login" and again.updated_at
    # most recent activity first: C-001 was saved after C-002 was created, unless both landed
    # in the same millisecond (then the id breaks the tie) — the clock decides, so compare sets
    assert {c.id for c in board.list(ConversationStatus.OPEN)} == {"C-001", "C-002"}
    board.discard("C-002")
    assert [c.id for c in board.list(ConversationStatus.OPEN)] == ["C-001"]
    with pytest.raises(ConversationError, match="já foi encerrada"):
        board.require("C-002")
    with pytest.raises(ConversationError, match="não existe"):
        board.require("C-404")


# --------------------------------------------------------------------- sprint meeting


async def test_a_meeting_builds_a_draft_and_leaves_the_backlog_untouched(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    turn = await chats.say(conv.id, "Página de login; Exportar CSV")
    assert not turn.failed and len(turn.changes) == 2
    conv = chats.board.require(conv.id)
    assert [i.title for i in conv.draft.items] == ["Página de login", "Exportar CSV"]
    assert [t.who for t in conv.turns] == ["founder", "agent"] and conv.turns[1].changes
    assert conv.title == "Página de login; Exportar CSV"
    assert ctx.store.list_stories(factory.slug) == []  # nothing in the backlog yet
    assert SprintBoard(ctx.store, factory.slug).sprints() == []
    assert events(ctx, "conversation.turn") and events(ctx, "conversation.opened")
    await ctx.aclose()


async def test_the_draft_evolves_over_turns_and_committing_starts_the_sprint(factory: Factory):
    seen: list[list[Message]] = []
    script = turn_script(
        {
            "reply": "Montei dois cards.",
            "ops": [add("Login", in_sprint=True), add("Exportar CSV", in_sprint=True, priority=4)],
        },
        {
            "reply": "Tirei o CSV e defini a meta.",
            "ops": [{"op": "drop", "ref": "D2"}, {"op": "goal", "text": "Entrar no sistema"}],
            "questions": [],
        },
        seen=seen,
    )
    ctx = make_ctx(factory, script)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "Quero login e CSV hoje")
    await chats.say(conv.id, "O CSV fica para depois")
    # the second call saw the draft and the earlier turns
    second = seen[1][-1].content
    assert 'D2 · P4 · IN SPRINT · "Exportar CSV"' in second
    assert "Founder: Quero login e CSV hoje" in second and "Montei dois cards." in second
    assert ctx.store.list_stories(factory.slug) == []

    with pytest.raises(ConversationError, match="proposta do Product Owner"):
        await chats.commit(conv.id, start_sprint=True)  # never the first act of a meeting
    await chats.propose(conv.id)
    result = await chats.commit(conv.id, start_sprint=True)
    assert result.created == ["S-001"] and result.sprint_id == "SP-001"
    story = ctx.store.get_story("S-001")
    assert story["stage"] == Stage.SPEC and story["origin"] == "founder"  # admitted by the PO
    sprint = SprintBoard(ctx.store, factory.slug).get("SP-001")
    assert sprint.status == SprintStatus.RUNNING and sprint.goal == "Entrar no sistema"
    assert sprint.story_ids == ["S-001"]
    conv = chats.board.require(conv.id, open_only=False)
    assert conv.status == ConversationStatus.COMMITTED and conv.result["sprint_id"] == "SP-001"
    assert "sprint SP-001 começou" in conv.turns[-1].text
    assert events(ctx, "conversation.committed")
    with pytest.raises(ConversationError, match="já foi encerrada"):
        await chats.say(conv.id, "mais uma coisa")
    await ctx.aclose()


async def test_saving_without_a_sprint_only_fills_the_backlog(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "Login; CSV")
    result = await chats.commit(conv.id)
    assert result.created == ["S-001", "S-002"] and result.sprint_id is None
    assert {s["stage"] for s in ctx.store.list_stories(factory.slug)} == {Stage.BACKLOG}
    assert Scheduler(ctx).runnable() == []
    assert SprintBoard(ctx.store, factory.slug).sprints() == []
    await ctx.aclose()


async def test_existing_cards_join_the_sprint_and_change_priority_through_the_po(
    factory: Factory,
):
    script = turn_script(
        {
            "reply": "Puxei o card e criei outro.",
            "ops": [
                {"op": "update", "ref": "S-001", "in_sprint": True, "priority": 1},
                add("Login", in_sprint=True),
            ],
        }
    )
    ctx = make_ctx(factory, script)
    po = ProductOwnerAgent(ctx)
    po.add_item("Relatório mensal", priority=300)
    po.add_item("Nota antiga", priority=500)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "Quero o relatório e um login")
    await chats.propose(conv.id)
    result = await chats.commit(conv.id, start_sprint=True, goal="Meta")
    assert result.existing == ["S-001"] and result.created == ["S-003"]
    assert ctx.store.get_story("S-001")["priority"] == 100  # re-ranked by the Product Owner
    board = SprintBoard(ctx.store, factory.slug)
    assert board.get("SP-001").story_ids == ["S-001", "S-003"]
    assert ctx.store.get_story("S-002")["stage"] == Stage.BACKLOG  # untouched card stays put
    await ctx.aclose()


async def test_commit_checks_everything_before_it_writes(factory: Factory):
    script = turn_script(
        {"reply": "ok", "ops": [{"op": "update", "ref": "S-001", "in_sprint": True}]},
        {"reply": "ok", "ops": [add("Nova", in_sprint=True)]},
    )
    ctx = make_ctx(factory, script)
    ProductOwnerAgent(ctx).add_item("Card antigo")
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    with pytest.raises(ConversationError, match="rascunho está vazio"):
        await chats.commit(conv.id)
    await chats.say(conv.id, "puxe o S-001")
    await chats.say(conv.id, "e crie uma nova")
    await chats.propose(conv.id)
    ProductOwnerAgent(ctx).admit("S-001")  # someone else started it in the meantime
    with pytest.raises(ConversationError, match="S-001 não está mais esperando"):
        await chats.commit(conv.id, start_sprint=True)
    assert [s["id"] for s in ctx.store.list_stories(factory.slug)] == ["S-001"]  # no half-commit
    assert chats.board.require(conv.id).open
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "Login; CSV")  # dry-run marks them for the sprint
    chats.edit(conv.id, [{"op": "update", "ref": "D1", "in_sprint": False}])
    chats.edit(conv.id, [{"op": "update", "ref": "D2", "in_sprint": False}])
    with pytest.raises(ConversationError, match="marque ao menos uma"):
        await chats.commit(conv.id, start_sprint=True)
    await ctx.aclose()


async def test_a_sprint_never_sweeps_in_the_backlog_when_every_pick_was_taken(factory: Factory):
    ctx = make_ctx(factory, turn_script({"reply": "ok", "ops": [add("Login", in_sprint=True)]}))
    po = ProductOwnerAgent(ctx)
    po.add_item("Outro card")
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "quero o login")
    await chats.propose(conv.id)
    po.admit(po.add_item("Login").story_id)  # the same work started elsewhere meanwhile
    with pytest.raises(ConversationError, match="nenhuma das histórias do sprint"):
        await chats.commit(conv.id, start_sprint=True)
    assert SprintBoard(ctx.store, factory.slug).sprints() == []
    assert ctx.store.get_story("S-001")["stage"] == Stage.BACKLOG  # "Outro card" stayed put
    assert chats.board.require(conv.id).open
    await ctx.aclose()


async def test_a_message_that_sanitizes_to_nothing_still_gets_a_turn(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "Traceback (most recent call last):")
    assert chats.board.require(conv.id).title == ""
    await ctx.aclose()


async def test_the_founder_edits_the_same_draft_the_model_does(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    await chats.say(conv.id, "Login; CSV")
    report = chats.edit(
        conv.id,
        [
            {"op": "drop", "ref": "D1"},
            {"op": "update", "ref": "D2", "priority": 5},
            {"op": "goal", "text": "M"},
        ],
    )
    assert len(report.changes) == 3
    conv = chats.board.require(conv.id)
    assert [(i.key, i.priority) for i in conv.draft.items] == [("D2", 5)] and conv.draft.goal == "M"
    await ctx.aclose()


# ------------------------------------------------------------------------- failures


async def test_a_model_failure_keeps_the_message_and_talks_plainly(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        raise RuntimeError("Traceback (most recent call last): boom")

    ctx = make_ctx(factory, script)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    turn = await chats.say(conv.id, "E se tivéssemos um app?")
    assert turn.failed and "tente de novo" in turn.reply
    conv = chats.board.require(conv.id)
    assert [t.who for t in conv.turns] == ["founder", "agent"]
    assert conv.turns[0].text == "E se tivéssemos um app?" and conv.draft.items == []
    err = events(ctx, "conversation.error")
    assert err and "boom" in err[0]["payload"]["error"]  # the detail goes to the event log
    await ctx.aclose()


async def test_the_one_turn_meeting_still_splits_goals_when_the_model_is_down(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        raise RuntimeError("sem rede")

    ctx = make_ctx(factory, script)
    result = await MasterAgent(ctx).meeting("Página de login; Exportar CSV")
    assert [s["title"] for s in result["stories"]] == ["Página de login", "Exportar CSV"]
    await ctx.aclose()


async def test_a_reply_never_shows_paths_or_stack_traces(factory: Factory):
    script = turn_script(
        {
            "reply": "Falhou em /Users/ana/proj/app/main.py:42 com ValueError: boom",
            "ops": [],
        }
    )
    ctx = make_ctx(factory, script)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    turn = await chats.say(conv.id, "oi")
    assert "/Users/ana" not in turn.reply and "ValueError" not in turn.reply
    await ctx.aclose()


# ---------------------------------------------------------- the morning meeting shortcut


async def test_the_morning_meeting_is_a_one_turn_session(factory: Factory):
    script = turn_script(
        {
            "reply": "Anotei.",
            "ops": [add("Cobrança", priority=2, epic="financeiro")],
            "questions": ["Cobrar por assento ou por uso?"],
        }
    )
    ctx = make_ctx(factory, script)
    result = await MasterAgent(ctx).meeting("Quero cobrar os clientes")
    assert result["stories"] == [
        {"id": "S-001", "title": "Cobrança", "epic": "financeiro", "priority": 2}
    ]
    assert result["clarifications"] == ["Cobrar por assento ou por uso?"]
    asked = [m for m in ctx.store.list_messages(factory.slug) if m.kind == MessageKind.DECISION]
    assert len(asked) == 1 and asked[0].executive_audit() == []
    conv = ConversationBoard(ctx.store, factory.slug).get("C-001")
    assert conv.status == ConversationStatus.COMMITTED and conv.result["created"] == ["S-001"]
    assert ctx.store.get_story("S-001")["stage"] == Stage.BACKLOG
    assert events(ctx, "meeting.done")
    await ctx.aclose()


async def test_the_one_turn_meeting_recovers_when_the_model_drafts_nothing(factory: Factory):
    """Seen live with Gemini flash-lite: the reply said the story was prepared and the only op was
    the sprint goal. `loompa meeting` is a batch command with nobody at the keyboard, so the
    founder's goals must never evaporate."""
    script = turn_script(
        {
            "reply": "Preparei a história para adicionar a subtração, já com testes.",
            "ops": [{"op": "goal", "text": "Calculadora completa"}],
            "questions": [],
        }
    )
    ctx = make_ctx(factory, script)
    result = await MasterAgent(ctx).meeting("Página de login; Exportar CSV")
    assert [s["title"] for s in result["stories"]] == ["Página de login", "Exportar CSV"]
    assert {s["id"] for s in result["stories"]} == {"S-001", "S-002"}
    recovered = events(ctx, "meeting.recovered")
    assert len(recovered) == 1 and recovered[0]["payload"]["cards"] == 2
    await ctx.aclose()


async def test_a_question_is_not_a_dropped_ball(factory: Factory):
    """A meeting that asks instead of drafting is doing its job: no deterministic salvage."""
    script = turn_script(
        {"reply": "Para quem é o relatório?", "ops": [], "questions": ["Para quem é o relatório?"]}
    )
    ctx = make_ctx(factory, script)
    result = await MasterAgent(ctx).meeting("Um relatório")
    assert result["stories"] == [] and result["clarifications"] == ["Para quem é o relatório?"]
    assert events(ctx, "meeting.recovered") == []
    assert ctx.store.list_stories(factory.slug) == []
    await ctx.aclose()


async def test_a_chat_turn_records_what_it_refused(factory: Factory):
    """The reason an edit was dropped survives in the session, not only in the reply of the
    moment: a silent drop is what made the live failure hard to diagnose."""
    script = turn_script(
        {
            "reply": "Anotei.",
            "ops": [add("Login"), {"op": "add", "description": "sem título"}, {"op": "explodir"}],
        }
    )
    ctx = make_ctx(factory, script)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    turn = await chats.say(conv.id, "quero um login")
    assert len(turn.ignored) == 2
    saved = chats.board.require(conv.id).turns[-1]
    assert saved.ignored == turn.ignored and saved.changes == turn.changes
    assert events(ctx, "conversation.turn")[0]["payload"]["ignored"] == 2
    await ctx.aclose()


async def test_a_meeting_that_drafts_nothing_leaves_no_open_session(factory: Factory):
    script = turn_script(
        {"reply": "Preciso de mais detalhes.", "ops": [], "questions": ["Sobre o quê?"]}
    )
    ctx = make_ctx(factory, script)
    result = await MasterAgent(ctx).meeting("hm")
    assert result == {"stories": [], "clarifications": ["Sobre o quê?"]}
    board = ConversationBoard(ctx.store, factory.slug)
    assert board.list(ConversationStatus.OPEN) == []
    assert board.get("C-001").status == ConversationStatus.DISCARDED
    await ctx.aclose()


# ---------------------------------------------------------------------------- brainstorm


def brainstorm_script(master: list[dict], *, consult=None, split=None, seen=None):
    """The Master's brainstorm turns in order; consulted roles and the Product Owner's split
    answer through `consult(role, messages)` and `split(messages)`; the rest as in dry-run."""
    queue = list(master)

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role, system = role_of(messages), messages[0].content
        if seen is not None:
            seen.append((role, messages))
        if role == "master" and "Brainstorming session" in system:
            return json.dumps(queue.pop(0))
        if "preliminary opinion" in system and consult is not None:
            return json.dumps(consult(role, messages))
        if "Split the direction" in system and split is not None:
            return split(messages)
        return dry_run_script(model, messages, tools)

    return script


def idea(title: str, **fields: Any) -> dict[str, Any]:
    return {"op": "add", "title": title, "description": f"{title} (detalhes)", **fields}


def no_web(factory: Factory) -> None:
    factory.config.tools.tavily.enabled = False  # no MCP server: the web is declared missing


async def test_a_brainstorm_is_led_by_the_master_and_files_only_after_two_oks(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    turn = await chats.say(conv.id, "Como melhorar o onboarding?")
    conv = chats.board.require(conv.id)
    assert conv.turns[1].name == "Master Loompa" and turn.changes
    assert [(i.origin, i.in_sprint) for i in conv.draft.items] == [("brainstorm", False)]
    assert conv.draft.direction and conv.draft.ready
    with pytest.raises(ConversationError, match="aprove o rumo"):
        await chats.commit(conv.id)  # no second OK without the first
    with pytest.raises(ConversationError, match="só uma reunião"):
        await chats.commit(conv.id, start_sprint=True)

    await chats.approve(conv.id)  # first OK: the Product Owner proposes the cards
    conv = chats.board.require(conv.id)
    assert conv.draft.approved is not None and conv.turns[-1].name == "Product Owner Loompa"
    assert [(c.key, c.ideas, c.story_id) for c in conv.draft.split] == [("C1", ["D1"], None)]
    assert ctx.store.list_stories(factory.slug) == []  # nothing written yet

    result = await chats.commit(conv.id)  # second OK
    assert result.created == ["S-001"]
    row = ctx.store.get_story("S-001")
    assert row["origin"] == "brainstorm"
    assert row["state"]["extra"]["brainstorm"]["conversation"] == conv.id
    conv = chats.board.require(conv.id, open_only=False)
    assert conv.status == ConversationStatus.COMMITTED and conv.draft.split is None
    assert events(ctx, "brainstorm.approved") and events(ctx, "brainstorm.filed")
    await ctx.aclose()


async def test_the_master_calls_in_only_the_roles_the_idea_needs(factory: Factory):
    no_web(factory)
    seen: list = []
    answer = {
        "reply": "Vou ouvir o Analyst sobre os concorrentes.",
        "direction": "Convite por e-mail no primeiro acesso",
        "ops": [idea("Convite por e-mail")],
        "consult": [
            {"role": "analyst", "question": "Como os concorrentes convidam?"},
            {"role": "legal", "question": "Pode?"},  # no such role in this factory
            {"role": "Analyst Loompa", "question": "de novo"},  # one question per role
        ],
        "ready": False,
    }
    opinion = {
        "summary": "Os concorrentes convidam por e-mail com link mágico.",
        "attention": ["Entregabilidade do e-mail"],
        "cost": "baixo",
        "benefit": "menos atrito no cadastro",
        "counterpoints": ["Contraria a decisão de não guardar e-mails sem consentimento"],
        "sources": [],
    }
    ctx = make_ctx(factory, brainstorm_script([answer], consult=lambda role, m: opinion, seen=seen))
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    await chats.say(conv.id, "Quero um convite por e-mail")
    conv = chats.board.require(conv.id)
    assert [o.role for o in conv.draft.consults] == ["analyst"]
    assert [r for r, _ in seen].count("architect") == 0
    (op,) = conv.draft.consults
    assert op.key == "O1" and op.asked_by == "master" and op.counterpoints
    assert [t.name for t in conv.turns] == ["", "Master Loompa", "Analyst Loompa"]
    assert conv.turns[-1].text.startswith("Parecer preliminar (O1)")
    assert "Contrapontos" in conv.turns[-1].text
    assert conv.limits and "busca na web" in conv.limits[0]  # declared by code, not the model
    assert conv.draft.direction == "Convite por e-mail no primeiro acesso"
    assert events(ctx, "brainstorm.consulted")
    await ctx.aclose()


async def test_an_opinion_keeps_only_sources_that_were_checked(factory: Factory):
    no_web(factory)
    (factory.root / "README.md").write_text("# produto\n", encoding="utf-8")
    opinion = {
        "summary": "Já existe uma tela de cadastro.",
        "sources": ["README.md", "https://inventado.com/x", "nao/existe.py"],
    }
    ctx = make_ctx(factory, brainstorm_script([], consult=lambda role, m: opinion))
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    turn = await chats.consult(conv.id, "architect", "Isso mexe no cadastro?")
    conv = chats.board.require(conv.id)
    (op,) = conv.draft.consults
    assert op.sources == ["README.md"] and op.asked_by == "founder" and not turn.failed
    assert any("2 fonte(s)" in a for a in op.attention)
    assert "README.md" not in conv.turns[-1].text  # paths are for the models, not the founder
    assert conv.turns[0].who == "founder" and "Architect" in conv.turns[0].text
    await ctx.aclose()


async def test_the_founder_sets_an_opinion_aside_and_unknown_roles_are_refused(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    await chats.consult(conv.id, "analyst", "Quanto custa um serviço de e-mail?")
    report = chats.edit(conv.id, [{"op": "dismiss", "ref": "o1"}])
    assert report.changes and chats.board.require(conv.id).draft.consults[0].dismissed
    from loompa.conversations import render_brainstorm

    assert "set this opinion aside" in render_brainstorm(chats.board.require(conv.id).draft)
    with pytest.raises(ConversationError, match="papel"):
        await chats.consult(conv.id, "legal", "Pode?")
    with pytest.raises(ConversationError, match="só vale num brainstorm"):
        await chats.consult(chats.open(ConversationKind.MEETING).id, "analyst", "x")
    await ctx.aclose()


async def test_a_role_that_cannot_answer_leaves_a_failed_opinion(factory: Factory):
    def boom(role, messages):
        raise RuntimeError("fora do ar")

    ctx = make_ctx(factory, brainstorm_script([], consult=boom))
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    turn = await chats.consult(conv.id, "architect", "Isso mexe no banco?")
    conv = chats.board.require(conv.id)
    assert turn.failed and conv.draft.consults[0].failed and conv.draft.opinions() == []
    assert "Não consegui ouvir" in conv.turns[-1].text
    await ctx.aclose()


async def test_the_product_owner_splits_into_new_cards_additions_and_held_ideas(factory: Factory):
    po = ProductOwnerAgent(factory_ctx := make_ctx(factory, dry_run=True))
    po.add_item("Convite por e-mail")  # S-001, waiting
    po.add_item("Tela de boas-vindas")  # S-002, about to be in progress
    factory_ctx.store.update_story("S-002", stage=Stage.DEV.value)
    await factory_ctx.aclose()

    answer = {
        "reply": "Anotei.",
        "direction": "Primeiro acesso mais simples",
        "ops": [idea("Link mágico"), idea("Lembrete do convite"), idea("Gamificar"), idea("Tour")],
        "ready": True,
    }
    split = {
        "reply": "Dois cards novos e um acréscimo ao convite.",
        "cards": [
            {
                "title": "Entrar por link mágico",
                "description": "Login sem senha",
                "kind": "feature",
                "epic": "Primeiro acesso",
                "priority": 1,
                "ideas": ["D1"],
                "into": "",
                "note": "",
            },
            {
                "title": "",
                "description": "Reenviar o convite após 3 dias",
                "priority": 2,
                "ideas": ["D2"],
                "into": "S-001",
                "note": "complementa o convite",
            },
            {
                "title": "Boas-vindas com nome",
                "description": "Saudar pelo nome",
                "priority": 3,
                "ideas": [],
                "into": "S-002",
                "depends_on": ["C1"],
                "note": "",
            },
        ],
        "held": [{"ref": "D3", "reason": "Vago demais para construir."}, {"ref": "D4"}],
    }
    ctx = make_ctx(factory, brainstorm_script([answer], split=lambda m: json.dumps(split)))
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    await chats.say(conv.id, "Quero um primeiro acesso mais simples")
    await chats.approve(conv.id)
    conv = chats.board.require(conv.id)
    cards = {c.key: c for c in conv.draft.split}
    assert cards["C1"].title == "Entrar por link mágico" and cards["C1"].kind == "feature"
    assert (cards["C2"].story_id, cards["C2"].amend) == ("S-001", "Reenviar o convite após 3 dias")
    assert cards["C3"].story_id is None and "em andamento" in cards["C3"].note  # S-002 is busy
    assert cards["C4"].ideas == ["D4"] and "não citou" in cards["C4"].note  # a hold needs a reason
    assert [i.note for i in conv.draft.items if i.key == "D3"] == ["Vago demais para construir."]

    report = chats.edit(
        conv.id, [{"op": "drop", "ref": "C4"}, {"op": "add", "title": "x"}], split=True
    )
    assert report.ignored and chats.board.require(conv.id).draft.approved is not None
    result = await chats.commit(conv.id)
    assert result.created == ["S-003", "S-004"] and result.amended == ["S-001"]
    assert (
        "Acrescentado (brainstorm C-001): Reenviar o convite"
        in ctx.store.get_story("S-001")["description"]
    )
    assert ctx.store.get_story("S-003")["state"]["kind"] == "feature"
    assert ctx.store.get_story("S-004")["depends_on"] == ["S-003"]  # C3 needs C1 first
    conv = chats.board.require(conv.id)  # D3 (held) and D4 (its card was dropped) stay
    assert conv.open and [i.key for i in conv.draft.items] == ["D3", "D4"]
    assert conv.draft.split is None and conv.draft.approved is None
    assert "2 ideias ficaram de fora" in conv.turns[-1].text
    await ctx.aclose()


async def test_going_back_to_the_conversation_takes_the_first_ok_back(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    await chats.say(conv.id, "Ideia um")
    await chats.approve(conv.id)
    chats.edit(conv.id, [{"op": "update", "ref": "C1", "priority": 1}], split=True)
    assert chats.board.require(conv.id).draft.split[0].priority == 1  # the split is editable
    await chats.say(conv.id, "Ideia dois")  # talking again: the approval no longer stands
    conv = chats.board.require(conv.id)
    assert conv.draft.approved is None and conv.draft.split is None and len(conv.draft.items) == 2
    await chats.approve(conv.id)
    chats.edit(conv.id, [{"op": "drop", "ref": "D2"}])  # changing the ideas takes it back too
    assert chats.board.require(conv.id).draft.split is None
    await chats.approve(conv.id)
    conv = chats.reopen(conv.id)
    assert conv.draft.split is None and "voltamos à conversa" in conv.turns[-1].text
    with pytest.raises(ConversationError, match="divisão"):
        chats.edit(conv.id, [{"op": "drop", "ref": "C1"}], split=True)
    await ctx.aclose()


async def test_the_split_falls_back_to_one_card_per_idea_when_the_po_is_down(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "product_owner" and "Split the direction" in messages[0].content:
            raise RuntimeError("fora do ar")
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    await chats.say(conv.id, "Uma ideia")
    turn = await chats.approve(conv.id)
    conv = chats.board.require(conv.id)
    assert turn.failed and not conv.draft.approved.reviewed and len(conv.draft.split) == 1
    assert (await chats.commit(conv.id)).created == ["S-001"]
    await ctx.aclose()


async def test_a_repeated_idea_points_at_the_existing_card(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    ProductOwnerAgent(ctx).add_item("Ideia: onboarding melhor")
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    await chats.say(conv.id, "onboarding melhor")
    conv = chats.board.require(conv.id)
    assert conv.draft.items[0].story_id == "S-001"  # recognised in the draft already
    await chats.approve(conv.id)
    result = await chats.commit(conv.id)
    assert result.created == [] and result.existing == ["S-001"]
    assert len(ctx.store.list_stories(factory.slug)) == 1
    await ctx.aclose()


def test_only_urls_a_web_tool_returned_survive_in_a_reply():
    seen = {"https://docs.exemplo.com/preco"}
    text = "Veja https://docs.exemplo.com/preco/ e https://inventado.com/x."
    out = AnalystAgent.only_seen_urls(text, seen)
    assert "https://docs.exemplo.com/preco/" in out and "inventado.com" not in out
    assert "fonte não verificada removida" in out


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
SIGNATURE = {"google": {"thought_signature": "c2lnbmF0dXJl"}}


def gemini_answer(message: dict) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "model": "gemini-3.5-flash-lite",
            "choices": [{"finish_reason": "stop", "message": message}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 20},
        },
    )


@respx.mock
async def test_a_gemini_turn_that_uses_a_tool_sends_the_signature_back(
    factory: Factory, monkeypatch
):
    """The brainstorm that failed against the real Gemini (then led by the Analyst; the Master
    leads it now): it made a tool call, and the next request lacked the thought signature. Runs the whole stack (agent, tool loop, router,
    adapter) against an endpoint shaped like Gemini's."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    factory.config.tools.tavily.enabled = False  # repository tools only, no network
    only = [ModelCandidate(provider="gemini", model="gemini-3.5-flash-lite")]
    factory.config.models.tiers = {"tier1": list(only), "tier2": list(only)}
    gemini = OpenAICompatibleProvider("gemini", factory.config.providers["gemini"])
    ctx = EngineContext.build(
        factory, router=ModelRouter(factory.config, providers={"gemini": gemini})
    )
    listing = {
        "tool_calls": [
            {
                "id": "function-call-1",
                "type": "function",
                "extra_content": SIGNATURE,
                "function": {"name": "list_dir", "arguments": '{"path": "."}'},
            }
        ]
    }
    idea = {"op": "add", "title": "Convite por e-mail", "description": "d", "priority": 2}
    final = {"content": json.dumps({"reply": "Vi o projeto.", "ops": [idea], "questions": []})}
    route = respx.post(GEMINI_URL).mock(side_effect=[gemini_answer(listing), gemini_answer(final)])
    try:
        chats = Conversations(ctx)
        conv = chats.open(ConversationKind.BRAINSTORM)
        turn = await chats.say(conv.id, "Como melhorar o onboarding?")
        assert not turn.failed, events(ctx, "conversation.error")
        assert route.call_count == 2
        sent = json.loads(route.calls[1].request.content)["messages"]
        (call,) = [tc for m in sent for tc in m.get("tool_calls") or []]
        assert call["extra_content"] == SIGNATURE
        assert [i.title for i in chats.board.require(conv.id).draft.items] == ["Convite por e-mail"]
    finally:
        await ctx.aclose()
        await gemini.aclose()


async def test_a_chat_turn_asks_for_low_reasoning_effort(factory: Factory):
    """A reasoning model's output budget pays for the thinking and the answer both, and a chat
    turn keeps a small one. Without this, GLM 5.3 spent all 1800 tokens thinking and returned
    an empty reply (live, 2026-09-19)."""
    provider = MockProvider("mock", script=dry_run_script)
    router = ModelRouter(
        factory.config, providers=dict.fromkeys(factory.config.providers, provider)
    )
    ctx = EngineContext.build(factory, router=router, dry_run=True)
    try:
        conv = Conversations(ctx).open(ConversationKind.MEETING)
        await Conversations(ctx).say(conv.id, "Página de login")
    finally:
        ctx.close()
    assert provider.calls and all(c["reasoning_effort"] == "low" for c in provider.calls)
