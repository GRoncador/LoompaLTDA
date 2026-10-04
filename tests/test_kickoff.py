"""Plan 10.10: the opening brainstorm of a greenfield factory with no code."""

from __future__ import annotations

import json
from typing import Any

import pytest

from loompa.agents import Conversations
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.conversations import ConversationError, ConversationKind
from loompa.factory import Factory, bootstrap_factory
from loompa.kickoff import KICKOFF_QUESTION, SKELETON_TITLE, needs_kickoff
from loompa.llm import Message
from test_engine import make_ctx


@pytest.fixture
def empty(git_repo, hub) -> Factory:
    bootstrap_factory(git_repo, name="Novo", store=hub, force_mode="greenfield")
    return Factory.open(git_repo)


def master(*answers: dict[str, Any]):
    queue = list(answers)

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "master" and "Brainstorming session" in messages[0].content:
            return json.dumps(queue.pop(0))
        if role_of(messages) == "product_owner" and "Split the direction" in messages[0].content:
            return json.dumps(
                {
                    "reply": "Proponho dois cards.",
                    "cards": [
                        {"title": "Registrar tarefa", "ideas": ["D1"], "priority": 1},
                        {"title": "Listar tarefas", "ideas": ["D2"], "priority": 2},
                    ],
                }
            )
        return dry_run_script(model, messages, tools)

    return script


async def test_the_kickoff_decides_vision_v1_and_stack_before_any_card(empty: Factory):
    ctx = make_ctx(
        empty,
        master(
            {
                "reply": "Entendi: um app de tarefas para você. O que fica de fora da v1?",
                "direction": "Um app de tarefas pessoal no terminal.",
                "vision": "App de tarefas pessoal no terminal, para quem esquece compromissos.",
                "out_of_v1": ["sincronizar com a nuvem"],
                "ops": [
                    {"op": "add", "title": "Registrar tarefa", "description": "com prazo"},
                    {"op": "add", "title": "Listar tarefas", "description": "por prazo"},
                ],
                "ready": True,
            },
            {
                "reply": "Fica a recomendada.",
                "ops": [{"op": "choose_stack", "option": 1}],
                "ready": True,
            },
        ),
    )
    assert needs_kickoff(ctx)
    chats = Conversations(ctx)
    with pytest.raises(ConversationError, match="brainstorm de abertura"):
        chats.open(ConversationKind.MEETING)
    with pytest.raises(ConversationError, match="brainstorm de abertura"):
        await chats.quick_story("Exportar PDF")
    conv = chats.open(ConversationKind.BRAINSTORM)
    assert conv.draft.kickoff is not None and conv.turns[0].text == KICKOFF_QUESTION
    await chats.say(conv.id, "Quero um app de tarefas no terminal, só para mim.")
    conv = chats.board.require(conv.id)
    k = conv.draft.kickoff
    assert k.vision.startswith("App de tarefas") and k.out_of_v1 == ["sincronizar com a nuvem"]
    assert len(k.stacks) == 2 and conv.turns[-1].name.startswith("Architect")  # proposed by itself
    assert not conv.draft.ready  # no stack chosen yet: the Master's `ready` does not stand
    with pytest.raises(ConversationError, match="escolha da stack"):
        await chats.approve(conv.id)
    await chats.say(conv.id, "Pode ser a 1.")
    conv = chats.board.require(conv.id)
    assert conv.draft.kickoff.chosen == 1 and conv.draft.ready
    await chats.approve(conv.id)  # first OK: the stack becomes the factory's
    assert ctx.config.stack.languages == ["Python"]
    assert ctx.config.quality.test_command == "uv run pytest -q"
    assert "Python" in ctx.factory.paths.constitution.read_text(encoding="utf-8")
    assert any("stack-do-produto" in p.name for p in ctx.factory.paths.decisions.glob("*.md"))
    split = chats.board.require(conv.id).draft.split
    assert split[0].title == SKELETON_TITLE
    assert all(split[0].key in i.depends_on for i in split[1:])
    result = await chats.commit(conv.id)  # second OK
    assert len(result.created) == 3 and not needs_kickoff(ctx)
    skeleton = result.created[0]
    others = [ctx.store.get_story(s) for s in result.created[1:]]
    assert all(skeleton in (o.get("depends_on") or []) for o in others)
    chats.open(ConversationKind.MEETING)  # the Sprint Meeting opens now
    await ctx.aclose()


def test_a_repository_with_code_has_no_kickoff(git_repo, hub):
    (git_repo / "main.py").write_text("print('oi')\n")
    bootstrap_factory(git_repo, name="Com código", store=hub, force_mode="greenfield")
    ctx = make_ctx(Factory.open(git_repo))
    assert not needs_kickoff(ctx)
