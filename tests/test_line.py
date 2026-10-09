"""ADR-0036 §4: the base's own suite runs after it moves; a red base stops the line once."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import git
from loompa.agents import ProductOwnerAgent
from loompa.agents.inspector import InspectorAgent
from loompa.engine import StoryState
from loompa.engine.line import LINE_KEY, LineWatch, line_card
from loompa.factory import Factory
from loompa.sprints import SprintBoard
from test_engine import factory, make_ctx, seed_story  # noqa: F401


def _land(root: Path, name: str, body: str, subject: str) -> None:
    (root / "tests" / name).write_text(body)
    git("add", ".", cwd=root)
    git("commit", "-qm", subject, cwd=root)


async def test_a_red_base_after_a_merge_opens_one_card_and_tells_the_founder_once(
    factory: Factory,  # noqa: F811
):
    """tamagotchi SP-013: two merges left the base with 3 failing tests; five stories found them
    in their baselines, Kaizen filed a card from each (12 duplicates) and the founder was asked
    about "falhas vindas de outra história" again and again."""
    ctx = make_ctx(factory)
    watch = LineWatch(ctx)
    assert not watch.due()  # nothing merged yet: the founder's base is remembered, not judged
    assert not watch.due()
    board = SprintBoard(ctx.store, ctx.slug)
    running = ProductOwnerAgent(ctx).add_item("Uma frente em andamento").story_id
    board.add(running)
    board.start(board.draft().id, "meta")
    sprint = board.running()
    assert sprint is not None
    ctx.emit("story.merged", story_id="S-070", sha="x")
    _land(
        factory.root,
        "test_cache.py",
        "def test_cache_lists_every_module():\n    assert False\n",
        "feat(s-070): Exibir o pet em modo só ver",
    )
    assert watch.due()
    state = await watch.check()
    assert state["red"] and state["failing"] and state["card"]
    card = state["card"]
    row = ctx.store.get_story(card)
    assert row["priority_pinned"] and "S-070" in row["description"]
    assert card in (board.get(sprint.id) or sprint).story_ids  # it joins the running sprint
    notes = [
        m for m in ctx.store.list_messages(ctx.slug, "pending") if "versão principal" in m.title
    ]
    assert len(notes) == 1 and "S-070" in notes[0].context and notes[0].executive_audit() == []
    assert line_card(ctx) == card

    # the base moves again and stays red: no second card, no second note
    _land(factory.root, "test_other.py", "def test_ok():\n    assert True\n", "feat(s-089): x")
    assert watch.due()
    again = await watch.check()
    assert again["card"] == card
    assert (
        len([m for m in ctx.store.list_messages(ctx.slug, "pending") if "falhando" in m.title]) == 1
    )

    # a story's baseline sees the red base but files no copy of the card
    st = StoryState(story_id=running, title="t")
    wt = ctx.worktrees.create(running, title="t")
    base = await InspectorAgent(ctx).baseline(st, wt)
    assert not base["tests_ok"] and st.learnings == []

    # fixed: green again, and the founder hears it
    (factory.root / "tests" / "test_cache.py").unlink()
    git("add", "-A", cwd=factory.root)
    git("commit", "-qm", f"feat({card.lower()}): conserta", cwd=factory.root)
    green = await watch.check()
    assert not green["red"] and line_card(ctx) is None
    assert [m for m in ctx.store.list_messages(ctx.slug, "pending") if "voltou" in m.title]
    assert json.loads(ctx.store.get(LINE_KEY))["sha"] == watch.base_sha()
    await ctx.aclose()


async def test_the_worker_is_told_which_failures_belong_to_the_base(factory: Factory):  # noqa: F811
    from loompa.agents.worker import WorkerAgent
    from loompa.llm import Message, ToolCall

    seen: list[list[Message]] = []

    def script(model, messages, tools):
        seen.append(list(messages))
        return [ToolCall("d", "done", {"summary": "ok"})]

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Qualquer")
    st = StoryState(story_id=sid, title="Qualquer")
    st.extra["baseline"] = {"failing": ["tests/test_cache.py::test_cache_lists_every_module"]}
    wt = ctx.worktrees.create(sid, title="Qualquer")
    aci = ctx.aci_for(wt.path, allowed_paths=["app/"], diff_base=wt.base)
    await WorkerAgent(ctx)._run_task(st, aci, 1, "Fazer algo", "spec", "plan", "- [ ] T1")
    user = next(m.content for m in seen[0] if m.role == "user")
    assert "Already failing on the base" in user and "test_cache_lists_every_module" in user
    await ctx.aclose()
