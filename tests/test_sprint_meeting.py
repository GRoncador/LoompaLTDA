"""One sprint at a time, and a Sprint Meeting that adjusts the running sprint or assembles the
next one to wait for it (ADR-0018). No network."""

from __future__ import annotations

import json
from typing import Any

import pytest

from loompa.agents import Conversations, MasterAgent, ProductOwnerAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.comms import FounderAnswer
from loompa.conversations import ConversationError, ConversationKind, MeetingMode
from loompa.engine import Scheduler, Stage, load_state
from loompa.engine.lock import EngineLock
from loompa.factory import Factory
from loompa.llm import Message
from loompa.sprints import SprintBoard, SprintError, SprintStatus
from test_dashboard import client  # noqa: F401
from test_engine import factory, make_ctx  # noqa: F401

BASE = "/api/factories/demo-hq"


def running_sprint(ctx, *titles: str) -> list[str]:
    po = ProductOwnerAgent(ctx)
    ids = [po.add_item(t).story_id for t in titles]
    MasterAgent(ctx).start_sprint(ids)
    return ids


def finish(ctx, sprint_id: str) -> None:
    for sid in SprintBoard(ctx.store, ctx.slug).get(sprint_id).story_ids:
        ctx.store.update_story(sid, stage=Stage.DONE.value)
    MasterAgent(ctx).close_finished_sprints()


def master_says(*answers: dict[str, Any]):
    queue = list(answers)

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "master" and "Sprint Meeting" in messages[0].content:
            return json.dumps(queue.pop(0))
        return dry_run_script(model, messages, tools)

    return script


# ----------------------------------------------------------------------- one at a time


def test_only_one_sprint_runs_at_a_time(factory: Factory):
    ctx = make_ctx(factory)
    running_sprint(ctx, "Login")
    later = ProductOwnerAgent(ctx).add_item("Relatório").story_id
    with pytest.raises(SprintError, match="só um sprint roda por vez"):
        MasterAgent(ctx).start_sprint([later])
    assert ctx.store.get_story(later)["stage"] == Stage.BACKLOG  # nothing moved
    board = SprintBoard(ctx.store, ctx.slug)
    planned = board.add(later)  # assembling the next one is fine
    with pytest.raises(SprintError, match="só um sprint roda por vez"):
        board.start(planned.id)
    finish(ctx, "SP-001")
    assert MasterAgent(ctx).start_sprint().id == planned.id
    ctx.close()


async def test_a_story_sent_back_to_the_backlog_leaves_the_running_sprint(factory: Factory):
    """Otherwise a card waiting in the backlog would hold its sprint open forever."""
    ctx = make_ctx(factory, dry_run=True)
    a, b = running_sprint(ctx, "Login", "Relatório")
    state = load_state(ctx, a)
    from loompa.engine.graph import block
    from loompa.engine.state import BlockedReason

    state = await block(ctx, state, BlockedReason.QUESTION, "dúvida", options=["a"])
    from loompa.engine.scheduler import save_state

    save_state(ctx, state, "block")
    await Scheduler(ctx).aanswer(state.blocked_message_id, FounderAnswer(option_key="skip"))
    sprint = SprintBoard(ctx.store, ctx.slug).get("SP-001")
    assert sprint.story_ids == [b] and ctx.store.get_story(a)["stage"] == Stage.BACKLOG
    ctx.store.update_story(b, stage=Stage.DONE.value)
    MasterAgent(ctx).close_finished_sprints()
    assert SprintBoard(ctx.store, ctx.slug).get("SP-001").status == SprintStatus.CLOSED
    await ctx.aclose()


# ----------------------------------------------------------------- the meeting's choice


async def test_with_a_sprint_running_the_meeting_asks_what_it_is_about(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    running_sprint(ctx, "Login")
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    assert conv.mode is None and conv.draft.items == []
    text = await chats.brief(conv.id)
    assert "O SP-001 está rodando" in text and "pré-montar o próximo" in text
    for attempt in (
        lambda: chats.say(conv.id, "oi"),
        lambda: chats.propose(conv.id),
        lambda: chats.commit(conv.id),
    ):
        with pytest.raises(ConversationError, match="escolha primeiro"):
            await attempt()
    with pytest.raises(ConversationError, match="escolha primeiro"):
        chats.edit(conv.id, [])
    conv = chats.choose(conv.id, MeetingMode.CURRENT)
    assert conv.mode == MeetingMode.CURRENT and conv.draft.members == ["S-001"]
    assert [(i.key, i.in_sprint, i.stage) for i in conv.draft.items] == [("S-001", True, "SPEC")]
    assert "vamos olhar o SP-001" in conv.turns[-1].text
    with pytest.raises(ConversationError, match="já sabe"):
        chats.choose(conv.id, MeetingMode.NEXT)
    await ctx.aclose()


async def test_adjusting_the_running_sprint(factory: Factory):
    seen: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "master" and "review of the running sprint" in messages[0].content:
            seen.append(messages[-1].content)
            return json.dumps(
                {
                    "reply": "Tiro o relatório, recomeço o login e trago a exportação.",
                    "ops": [
                        {"op": "update", "ref": "S-002", "in_sprint": False},
                        {"op": "restart", "ref": "S-001", "reason": "usar a tabela nova"},
                        {"op": "update", "ref": "S-004", "in_sprint": True},
                        {"op": "restart", "ref": "S-004"},  # not in the sprint: refused
                    ],
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    a, b, c = running_sprint(ctx, "Login", "Relatório", "Perfil")
    waiting = ProductOwnerAgent(ctx).add_item("Exportar CSV").story_id  # S-004
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    chats.choose(conv.id, MeetingMode.CURRENT)
    turn = await chats.say(conv.id, "quero tirar o relatório e recomeçar o login")
    assert "não está no sprint em andamento" in " ".join(turn.ignored)
    assert (
        "## Running sprint SP-001" in seen[0] and 'S-002 · SPEC · intake · "Relatório"' in seen[0]
    )
    # whatever joins a running sprint is reviewed by the Product Owner first
    with pytest.raises(ConversationError, match="avaliação dele"):
        await chats.commit(conv.id)
    await chats.propose(conv.id)
    # a story that may be executing is only touched with the engine stopped
    assert chats.touches_work_in_flight(conv.id)
    lock = EngineLock(factory.paths.loompa / "engine.lock")
    lock.acquire()
    try:
        with pytest.raises(ConversationError, match="pause a esteira"):
            await chats.commit(conv.id)
    finally:
        lock.release()
    result = await chats.commit(conv.id)
    assert result.withdrawn == [b] and result.restarted == [a] and result.joined == [waiting]
    sprint = SprintBoard(ctx.store, ctx.slug).get("SP-001")
    assert sprint.status == SprintStatus.RUNNING and sprint.story_ids == [a, c, waiting]
    assert ctx.store.get_story(b)["stage"] == Stage.BACKLOG
    assert ctx.store.get_story(waiting)["stage"] == Stage.SPEC  # admitted into the sprint
    restarted = load_state(ctx, a)
    assert restarted.phase == "intake" and "usar a tabela nova" in restarted.founder_notes
    conv = chats.board.require(conv.id, open_only=False)
    assert "SP-001 ajustado" in conv.turns[-1].text and conv.result["joined"] == [waiting]
    await ctx.aclose()


async def test_cancelling_the_running_sprint(factory: Factory):
    ctx = make_ctx(
        factory,
        master_says(
            {"reply": "Cancelo.", "ops": [{"op": "cancel_sprint", "reason": "mudou tudo"}]}
        ),
    )
    a, b = running_sprint(ctx, "Login", "Relatório")
    ctx.store.update_story(a, stage=Stage.DONE.value)  # delivered before the cancel: it stays
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    chats.choose(conv.id, MeetingMode.CURRENT)
    await chats.say(conv.id, "cancela o sprint")
    assert chats.board.require(conv.id).draft.cancel_sprint
    result = await chats.commit(conv.id)
    assert result.withdrawn == [b]
    sprint = SprintBoard(ctx.store, ctx.slug).get("SP-001")
    assert sprint.status == SprintStatus.CANCELLED and sprint.story_ids == [a, b]  # the record
    assert ctx.store.get_story(b)["stage"] == Stage.BACKLOG
    assert ctx.store.get_story(a)["stage"] == Stage.DONE
    assert "SP-001 foi cancelado" in chats.board.require(conv.id, open_only=False).turns[-1].text
    assert MasterAgent(ctx).start_sprint([b]).id == "SP-002"  # nothing runs: a new one may start
    await ctx.aclose()


async def test_restart_and_cancel_belong_to_a_meeting_about_the_running_sprint(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    assert conv.mode == MeetingMode.NEXT  # nothing running: it plans the next sprint
    report = chats.edit(conv.id, [{"op": "cancel_sprint"}, {"op": "restart", "ref": "S-001"}])
    assert len(report.ignored) == 2 and not chats.board.require(conv.id).draft.cancel_sprint
    await ctx.aclose()


# --------------------------------------------------------------------- the next sprint


async def test_the_next_sprint_is_assembled_then_reviewed_and_started(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    running_sprint(ctx, "Login")
    chats = Conversations(ctx)
    conv = chats.open(ConversationKind.MEETING)
    conv = chats.choose(conv.id, MeetingMode.NEXT)
    assert "fica esperando o SP-001 terminar" in conv.turns[-1].text
    await chats.say(conv.id, "Relatório; Exportar CSV")
    with pytest.raises(ConversationError, match="só um sprint roda por vez"):
        await chats.commit(conv.id, start_sprint=True)
    with pytest.raises(ConversationError, match="proposta do Product Owner"):
        await chats.commit(conv.id, plan_next=True)
    await chats.propose(conv.id)
    result = await chats.commit(conv.id, plan_next=True, goal="Relatórios")
    board = SprintBoard(ctx.store, ctx.slug)
    planned = board.get(result.sprint_id)
    assert planned.status == SprintStatus.OPEN and planned.story_ids == ["S-002", "S-003"]
    assert planned.goal == "Relatórios" and board.running().id == "SP-001"
    assert {ctx.store.get_story(s)["stage"] for s in planned.story_ids} == {Stage.BACKLOG}
    closing = chats.board.require(conv.id, open_only=False).turns[-1].text
    assert "está montado" in closing and "abra uma reunião de sprint" in closing

    # the running sprint ends: nothing starts by itself, the founder is told how to go on
    finish(ctx, "SP-001")
    assert board.running() is None and board.get(planned.id).status == SprintStatus.OPEN
    note = [m for m in ctx.store.list_messages(ctx.slug) if "SP-001 concluído" in m.title][0]
    assert f"O {planned.id} já está montado" in note.impact

    # the next meeting is there to review and start it
    conv = chats.open(ConversationKind.MEETING)
    text = await chats.brief(conv.id)
    assert f"O {planned.id} já está montado com 2 histórias" in text
    conv = chats.board.require(conv.id)
    assert conv.mode == MeetingMode.NEXT and conv.draft.sprint_id == planned.id
    assert [(i.key, i.in_sprint) for i in conv.draft.items] == [("S-002", True), ("S-003", True)]
    assert conv.draft.goal == "Relatórios"
    await chats.propose(conv.id)
    result = await chats.commit(conv.id, start_sprint=True)
    assert result.sprint_id == planned.id
    assert board.get(planned.id).status == SprintStatus.RUNNING
    await ctx.aclose()


async def test_the_one_turn_meeting_still_fills_the_backlog_while_a_sprint_runs(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    running_sprint(ctx, "Login")
    result = await MasterAgent(ctx).meeting("Relatório; CSV")
    assert [s["title"] for s in result["stories"]] == ["Relatório", "CSV"]
    await ctx.aclose()


# --------------------------------------------------------------------------------- API


def test_the_meeting_api_offers_the_choice_and_applies_it(client):
    ctx = client.app.state.hub.get("demo-hq").ctx
    a, b = running_sprint(ctx, "Login", "Relatório")
    body = client.post(f"{BASE}/conversations", json={"kind": "meeting"}).json()
    cid = body["conversation"]["id"]
    assert body["conversation"]["mode"] is None
    assert body["sprints"]["running"]["id"] == "SP-001" and body["sprints"]["planned"] is None
    r = client.post(f"{BASE}/conversations/{cid}/messages", json={"text": "oi"})
    assert r.status_code == 409 and "escolha primeiro" in r.json()["detail"]
    body = client.post(f"{BASE}/conversations/{cid}/mode", json={"mode": "current"}).json()
    assert [i["stage"] for i in body["conversation"]["draft"]["items"]] == ["SPEC", "SPEC"]
    client.post(
        f"{BASE}/conversations/{cid}/draft",
        json={"ops": [{"op": "update", "ref": b, "in_sprint": False}]},
    )
    r = client.post(f"{BASE}/conversations/{cid}/commit", json={})
    assert r.status_code == 200 and r.json()["result"]["withdrawn"] == [b]
    assert r.json()["sprints"]["running"]["story_ids"] == [a]
    # the next sprint, assembled while this one runs, shows on the board
    other = client.post(f"{BASE}/conversations", json={"kind": "meeting"}).json()
    oid = other["conversation"]["id"]
    client.post(f"{BASE}/conversations/{oid}/mode", json={"mode": "next"})
    client.post(
        f"{BASE}/conversations/{oid}/draft",
        json={"ops": [{"op": "update", "ref": b, "in_sprint": True}]},
    )
    client.post(f"{BASE}/conversations/{oid}/propose")
    r = client.post(f"{BASE}/conversations/{oid}/commit", json={"plan_next": True})
    assert r.status_code == 200 and r.json()["result"]["sprint_id"] == "SP-002"
    ov = client.get(f"{BASE}/overview").json()
    assert ov["sprint"]["id"] == "SP-001" and ov["next_sprint"]["story_ids"] == [b]


def test_the_terminal_meeting_asks_too(git_repo, hub, monkeypatch):
    from loompa.cli.main import app
    from test_chat import init_factory, runner

    init_factory(git_repo, monkeypatch)
    for title in ("Login", "Relatório"):
        assert runner.invoke(app, ["story", "add", title, "--dry-run"]).exit_code == 0
    assert runner.invoke(app, ["sprint", "start", "--dry-run"]).exit_code == 0
    r = runner.invoke(
        app,
        ["chat", "meeting", "--dry-run"],
        input="tira o relatório\n/atual\n/excluir S-002\n/sprint\n/aplicar\n",
    )
    assert r.exit_code == 0, r.stdout
    out = " ".join(r.stdout.replace("│", " ").split())  # Rich wraps the panels
    assert "O SP-001 está rodando" in out and "/atual" in out
    assert "escolha primeiro" in out  # a message before the choice is not lost silently
    assert "vamos olhar o SP-001" in out and "Comando desconhecido" in out  # no /sprint here
    assert "voltaram ao backlog: S-002" in out
    r = runner.invoke(app, ["sprint", "status"])
    assert "S-001" in r.stdout and "S-002" not in r.stdout
