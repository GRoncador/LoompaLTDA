"""ADR-0037 §1: every option of a team question is an action the engine carries out.

tamagotchi S-094 (10/10): the founder chose "encerrar como já resolvida" four times and the story
went straight back to the same task, because every option of a question was read as guidance for
an Architect who can only add tasks. Eight questions in 24 hours, none with a way out."""

from __future__ import annotations

import json
from typing import Any

from loompa.agents.dryrun import dry_run_script, role_of
from loompa.backlog import OUTCOME_KEY
from loompa.comms import FounderAnswer
from loompa.comms.answers import (
    DROP,
    GUIDE,
    RESOLVED,
    RESPEC,
    SKIP,
    action_of,
    by_words,
    executable_options,
)
from loompa.engine import Scheduler, Stage, load_state
from loompa.factory import Factory
from loompa.llm import Message, ToolCall
from test_engine import factory, make_ctx, seed_story, tool_results  # noqa: F401

S094 = [
    "Encerrar essa história como já resolvida: o teste já passa sem os cartões",
    "Devolver a história ao time para reescrever a especificação e o plano com o estado atual",
    "Encerrar a tarefa sem nenhuma mudança no código do produto",
    "Recriar os cartões apenas como registro interno, sem teste",
]


def test_the_founders_words_of_s094_become_actions():
    assert [by_words(x) for x in S094] == [RESOLVED, RESPEC, RESOLVED, GUIDE]
    assert by_words("Deixar para depois (volta ao backlog)") == SKIP
    assert by_words("Cancelar esta entrega") == DROP
    # "cancel" about something else is not the story's cancellation
    assert by_words("Cancelar o teste que lê a pasta interna") == GUIDE


def test_options_are_keyed_by_action_and_always_carry_the_ways_out():
    opts = executable_options(S094)
    assert [o.key for o in opts] == ["resolved", "respec", "resolved2", "opt1", "skip", "drop"]
    assert opts[0].recommended and not any(o.recommended for o in opts[1:])
    assert [action_of(o.key) for o in opts] == [RESOLVED, RESPEC, RESOLVED, GUIDE, SKIP, DROP]
    assert "sem entrega" in opts[0].description and opts[3].description == ""
    # a model's reading wins over the words; an unknown action falls back to them
    opts = executable_options(["E-mail", "SMS"], ["guide", "bogus"])
    assert [o.key for o in opts] == ["opt1", "opt2", "skip", "drop"]
    # an option that already offers a way out is not offered twice
    opts = executable_options(["Deixar para depois", "Seguir"])
    assert [o.key for o in opts] == ["skip", "opt1", "drop"]
    assert action_of("opt3") == GUIDE and action_of("retry") == GUIDE


def _worker_that_asks(options: list[str], asked: list[int]):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "worker":
            if "Guidance from the founder" in messages[1].content and not tool_results(messages):
                asked.append(2)
                return [
                    ToolCall(
                        "w",
                        "write_file",
                        {"path": "tests/test_reset.py", "content": "def test_ok():\n    pass\n"},
                    )
                ]
            if "Guidance from the founder" in messages[1].content:
                return [ToolCall("d", "done", {"summary": "feito"})]
            asked.append(1)
            return [
                ToolCall(
                    "q", "blocked", {"reason": "O defeito já não aparece.", "options": options}
                )
            ]
        return dry_run_script(model, messages, tools)

    return script


async def test_already_resolved_closes_the_story_instead_of_asking_again(
    factory: Factory,  # noqa: F811
):
    asked: list[int] = []
    ctx = make_ctx(factory, _worker_that_asks(S094[:2], asked))
    sid = seed_story(ctx, "Consertar a versão principal")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    msg = ctx.store.get_message(state.blocked_message_id)
    assert [o.key for o in msg.options] == ["resolved", "respec", "skip", "drop"]
    assert msg.executive_audit() == []
    state = await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="resolved"))
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.CANCELLED and asked == [1]  # the Worker never ran again
    assert state.extra[OUTCOME_KEY]["kind"] == "resolved"
    assert ctx.worktrees.get(sid) is None
    events = [e["type"] for e in ctx.store.events_since(0, limit=10_000) if e["story_id"] == sid]
    assert "story.resolved" in events and "plan.amended" not in events
    await ctx.aclose()


async def test_rewrite_the_requirements_goes_back_to_the_spec(factory: Factory):  # noqa: F811
    asked: list[int] = []
    specs: list[str] = []
    inner = _worker_that_asks(S094[:2], asked)

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "product":
            specs.append(messages[1].content)
        return inner(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Consertar a versão principal")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    await Scheduler(ctx).aanswer(state.blocked_message_id, FounderAnswer(option_key="respec"))
    state = load_state(ctx, sid)
    assert state.phase == "spec" and not state.spec_ready and not state.plan_ready
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert len(specs) == 2 and "reescrever a especificação" in specs[1]
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    await ctx.aclose()


def test_the_sprint_report_counts_a_resolved_story_apart():
    from loompa.sprint_report import RESULT_LABEL

    assert RESULT_LABEL["resolved"] == "encerrada como já resolvida"
    assert json.dumps(RESULT_LABEL, ensure_ascii=False)
