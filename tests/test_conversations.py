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
from loompa.llm import Message, ModelRouter, OpenAICompatibleProvider
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
    assert [c.id for c in board.list(ConversationStatus.OPEN)] == ["C-002", "C-001"]
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


async def test_a_brainstorm_is_led_by_the_analyst_and_declares_the_missing_web(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    turn = await chats.say(conv.id, "Como melhorar o onboarding?")
    conv = chats.board.require(conv.id)
    assert conv.turns[1].name == "Analyst Loompa" and turn.changes
    assert [(i.origin, i.in_sprint) for i in conv.draft.items] == [("brainstorm", False)]
    assert conv.limits and "simulação" in conv.limits[0]  # declared by code, not by the model
    assert ctx.store.list_stories(factory.slug) == []
    with pytest.raises(ConversationError, match="só uma reunião"):
        await chats.commit(conv.id, start_sprint=True)
    await ctx.aclose()


def test_only_urls_a_web_tool_returned_survive_in_a_reply():
    seen = {"https://docs.exemplo.com/preco"}
    text = "Veja https://docs.exemplo.com/preco/ e https://inventado.com/x."
    out = AnalystAgent.only_seen_urls(text, seen)
    assert "https://docs.exemplo.com/preco/" in out and "inventado.com" not in out
    assert "fonte não verificada removida" in out


async def test_the_product_owner_decides_which_ideas_enter_the_backlog(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role, system = role_of(messages), messages[0].content
        if role == "analyst" and "Brainstorming session" in system:
            return json.dumps(
                {
                    "reply": "Três ideias.",
                    "ops": [
                        add("Convite por e-mail", priority=2),
                        add("Deixar tudo melhor"),
                        add("Tour guiado"),
                    ],
                }
            )
        if role == "product_owner" and "## Ideas to review" in messages[-1].content:
            return json.dumps(
                {
                    "verdicts": [
                        {"key": "D1", "admit": True, "priority": 1},
                        {"key": "D2", "admit": False, "reason": "Vago demais para construir."},
                        {"key": "D3", "admit": True},
                    ]
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    await chats.say(conv.id, "Ideias de onboarding")
    result = await chats.commit(conv.id)
    assert result.created == ["S-001", "S-002"]
    assert result.held == [
        {"key": "D2", "title": "Deixar tudo melhor", "reason": "Vago demais para construir."}
    ]
    assert ctx.store.get_story("S-001")["priority"] == 100  # the PO raised it
    assert {ctx.store.get_story(s)["origin"] for s in result.created} == {"brainstorm"}
    conv = chats.board.require(conv.id)  # held ideas keep the session open
    assert conv.open and [(i.key, i.note) for i in conv.draft.items] == [
        ("D2", "Vago demais para construir.")
    ]
    assert "Deixei “Deixar tudo melhor” de fora" in conv.turns[-1].text
    assert conv.turns[-1].name == "Product Owner Loompa"
    assert events(ctx, "ideas.admitted")
    chats.edit(conv.id, [{"op": "drop", "ref": "D2"}])
    with pytest.raises(ConversationError, match="rascunho está vazio"):
        await chats.commit(conv.id)
    await ctx.aclose()


async def test_ideas_are_admitted_when_the_product_owner_is_unavailable(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "product_owner":
            raise RuntimeError("fora do ar")
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    await chats.say(conv.id, "Uma ideia")
    result = await chats.commit(conv.id)
    assert result.created == ["S-001"] and result.held == []
    assert chats.board.require(conv.id, open_only=False).status == ConversationStatus.COMMITTED
    await ctx.aclose()


async def test_a_hold_without_a_reason_is_not_a_hold(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "product_owner" and "## Ideas to review" in messages[-1].content:
            return json.dumps({"verdicts": [{"key": "D1", "admit": False}]})
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.BRAINSTORM)
    await chats.say(conv.id, "Uma ideia")
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
    result = await chats.commit(conv.id)
    assert result.created == [] and result.existing == ["S-001"]
    assert len(ctx.store.list_stories(factory.slug)) == 1
    await ctx.aclose()


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
    """The brainstorm that failed against the real Gemini: it made a tool call, and the next
    request lacked the thought signature. Runs the whole stack (agent, tool loop, router,
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
