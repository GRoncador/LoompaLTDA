"""Batch answers: a delivery carries the suggested cards as independent decisions."""

from __future__ import annotations

from typing import Any

import pytest

from loompa.agents import MasterAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.comms import FounderAnswer, MessageStatus, compose_delivery_message
from loompa.engine import Scheduler, Stage, load_state
from loompa.factory import Factory
from loompa.llm import Message, ToolCall
from loompa.sprints import SprintBoard
from test_engine import factory, make_ctx, seed_story, tool_results  # noqa: F401


def finder(model: str, messages: list[Message], tools: Any) -> Any:
    """A worker that writes its test and files two findings on the way."""
    if role_of(messages) != "worker":
        return dry_run_script(model, messages, tools)
    if not tool_results(messages):
        return [
            ToolCall(
                "l1",
                "note_learning",
                {
                    "title": "Erro ao salvar com nome vazio",
                    "kind": "bug",
                    "detail": "quebra o cadastro",
                },
            ),
            ToolCall(
                "l2",
                "note_learning",
                {"title": "Módulo de envio duplicado", "kind": "tech_debt", "detail": "consolidar"},
            ),
            ToolCall(
                "w",
                "write_file",
                {"path": "tests/test_f.py", "content": "def test_f():\n    assert True\n"},
            ),
        ]
    return [ToolCall("d", "done", {"summary": "pronto"})]


async def deliver(factory: Factory):
    ctx = make_ctx(factory, finder)
    sid = seed_story(ctx, "Cadastro")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery"
    msg = ctx.store.get_message(state.blocked_message_id)
    return ctx, sid, state, msg


async def test_delivery_offers_each_finding_as_its_own_decision(factory: Factory):
    ctx, sid, state, msg = await deliver(factory)
    bug, debt = state.finding_cards
    assert [d.id for d in msg.decisions] == [bug, debt]
    assert msg.executive_audit() == [] and [o.key for o in msg.options] == ["approve", "changes"]
    by_id = {d.id: d for d in msg.decisions}
    assert [o.key for o in by_id[bug].options] == ["sprint", "backlog", "drop"]
    assert next(o for o in by_id[bug].options if o.recommended).key == "sprint"  # bugs: urgent
    assert next(o for o in by_id[debt].options if o.recommended).key == "backlog"
    assert by_id[bug].title.startswith("Achado: [Bug colateral]")
    await ctx.aclose()


async def test_one_answer_settles_the_delivery_and_every_card(factory: Factory):
    ctx, sid, state, msg = await deliver(factory)
    bug, debt = state.finding_cards
    answered = await Scheduler(ctx).aanswer(
        msg.id, FounderAnswer(option_key="approve", decisions={bug: "sprint", debt: "drop"})
    )
    assert answered.stage == Stage.DONE  # the delivery itself
    assert ctx.store.get_story(debt)["stage"] == Stage.CANCELLED
    assert ctx.store.get_story(bug)["stage"] == Stage.BACKLOG  # planned, not started
    draft = SprintBoard(ctx.store, factory.slug).open_sprint()
    assert draft is not None and draft.story_ids == [bug]
    stored = ctx.store.get_message(msg.id)
    assert stored.status == MessageStatus.ANSWERED
    assert {d.id: d.chosen for d in stored.decisions} == {bug: "sprint", debt: "drop"}
    assert {
        e["payload"]["choice"] for e in ctx.store.events_since(0) if e["type"] == "finding.decided"
    } == {
        "sprint",
        "drop",
    }
    # the finding planned into the sprint runs when the founder starts it
    sprint = MasterAgent(ctx).start_sprint()
    assert sprint.story_ids == [bug] and ctx.store.get_story(bug)["stage"] == Stage.SPEC
    await ctx.aclose()


async def test_answering_only_the_cards_leaves_the_delivery_pending(factory: Factory):
    ctx, sid, state, msg = await deliver(factory)
    bug, debt = state.finding_cards
    result = await Scheduler(ctx).aanswer(msg.id, FounderAnswer(decisions={bug: "backlog"}))
    assert result.stage == Stage.AWAITING_FOUNDER and result.blocked_reason == "delivery"
    stored = ctx.store.get_message(msg.id)
    assert stored.status == MessageStatus.PENDING and stored.answer is None
    assert {d.id: d.chosen for d in stored.decisions} == {bug: "backlog", debt: None}
    # the founder can still approve later and decide the rest then
    done = await Scheduler(ctx).aanswer(
        msg.id, FounderAnswer(option_key="approve", decisions={bug: "drop", debt: "sprint"})
    )
    assert done.stage == Stage.DONE
    assert ctx.store.get_story(bug)["stage"] == Stage.BACKLOG  # already decided: not overruled
    assert SprintBoard(ctx.store, factory.slug).open_sprint().story_ids == [debt]
    await ctx.aclose()


async def test_bad_decisions_are_ignored_and_undecided_cards_are_never_lost(factory: Factory):
    ctx, sid, state, msg = await deliver(factory)
    bug, debt = state.finding_cards
    await Scheduler(ctx).aanswer(
        msg.id,
        FounderAnswer(option_key="approve", decisions={bug: "explodir", "S-404": "drop", "x": "y"}),
    )
    assert ctx.store.get_story(bug)["stage"] == Stage.BACKLOG
    assert ctx.store.get_story(debt)["stage"] == Stage.BACKLOG
    assert not any(e["type"] == "finding.decided" for e in ctx.store.events_since(0))
    report = MasterAgent(ctx).end_of_day_report()
    assert "Achados aguardando um sprint no backlog: 2" in report.context
    assert report.executive_audit() == []
    await ctx.aclose()


async def test_a_decided_card_is_not_asked_again_on_the_next_delivery(factory: Factory):
    ctx, sid, state, msg = await deliver(factory)
    bug, debt = state.finding_cards
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(decisions={bug: "backlog"}))
    state = load_state(ctx, sid)
    # the founder asks for changes; the same story delivers again
    state = await Scheduler(ctx).aanswer(
        msg.id, FounderAnswer(option_key="changes", text="mais um ajuste")
    )
    await Scheduler(ctx).run()
    second = ctx.store.get_message(load_state(ctx, sid).blocked_message_id)
    assert second.id != msg.id
    assert [d.id for d in second.decisions] == [debt]  # only what is still undecided
    await ctx.aclose()


def test_delivery_without_findings_has_no_decisions():
    msg = compose_delivery_message(
        factory="f", story_id="S-1", story_title="X", summary="ok", cards=[]
    )
    assert msg.decisions == [] and msg.executive_audit() == []


@pytest.mark.parametrize("bad", ["S-1", "S-1=", "=sprint"])
def test_cli_rejects_malformed_decisions(git_repo, hub, monkeypatch, bad):
    from typer.testing import CliRunner

    from loompa.cli.main import app

    monkeypatch.chdir(git_repo)
    runner = CliRunner()
    assert runner.invoke(app, ["init", ".", "--yes", "--name", "Cli"]).exit_code == 0
    r = runner.invoke(app, ["inbox", "reply", "abc", "-d", bad])
    assert r.exit_code == 1 and "Decisão inválida" in r.stdout
