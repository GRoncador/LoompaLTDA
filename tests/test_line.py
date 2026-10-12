"""ADR-0036 §4: the base's own suite runs after it moves; a red base stops the line once."""

from __future__ import annotations

import json
from pathlib import Path

from conftest import git
from loompa.agents import ProductOwnerAgent
from loompa.agents.inspector import InspectorAgent
from loompa.engine import Stage, StoryState
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


async def test_a_card_whose_failures_left_the_base_closes_and_the_rest_gets_its_own(
    factory: Factory,  # noqa: F811
):
    """ADR-0037 §4, tamagotchi S-094: S-092 fixed CA1/CA2 at 18:31, and the line card opened for
    them went on, then asked the founder eight times about failures that no longer existed. Now
    the card closes when its failures leave the base, and what is still red gets a card of its
    own."""
    from loompa.backlog import OUTCOME_KEY
    from loompa.engine import load_state

    ctx = make_ctx(factory)
    watch = LineWatch(ctx)
    _land(factory.root, "test_a.py", "def test_a_fails_here():\n    assert False\n", "feat(s-1): a")
    first = await watch.check()
    card = first["card"]
    assert card and first["targets"] == ["test_a_fails_here"]
    # another delivery fixes test_a and breaks test_b while the card waits in the backlog
    (factory.root / "tests" / "test_a.py").unlink()
    _land(factory.root, "test_b.py", "def test_b_fails_now():\n    assert False\n", "fix(s-2): b")
    second = await watch.check()
    state = load_state(ctx, card)
    assert state.stage == Stage.CANCELLED and state.extra[OUTCOME_KEY]["kind"] == "resolved"
    assert state.extra[OUTCOME_KEY]["by"] == ["S-2"]
    assert second["card"] and second["card"] != card
    assert second["targets"] == ["test_b_fails_now"]
    notes = [m.title for m in ctx.store.list_messages(ctx.slug, "pending")]
    assert "O conserto da versão principal não é mais necessário" in notes
    await ctx.aclose()


async def test_a_card_in_the_pipeline_closes_at_its_next_step(factory: Factory):  # noqa: F811
    from loompa.engine import Scheduler, load_state
    from loompa.engine.line import line_resolved

    ctx = make_ctx(factory)
    board = SprintBoard(ctx.store, ctx.slug)
    other = ProductOwnerAgent(ctx).add_item("Outra frente").story_id
    board.add(other)
    board.start(board.draft().id, "meta")
    watch = LineWatch(ctx)
    _land(factory.root, "test_a.py", "def test_a_fails_here():\n    assert False\n", "feat(s-1): a")
    card = (await watch.check())["card"]
    assert ctx.store.get_story(card)["stage"] != "BACKLOG"  # it joined the running sprint
    (factory.root / "tests" / "test_a.py").unlink()
    git("add", "-A", cwd=factory.root)
    git("commit", "-qm", "fix(s-2): conserta", cwd=factory.root)
    assert not (await watch.check())["red"]
    assert line_resolved(ctx, card) is not None
    await Scheduler(ctx).run()
    state = load_state(ctx, card)
    assert state.stage == Stage.CANCELLED and state.extra["outcome"]["kind"] == "resolved"
    assert line_resolved(ctx, card) is None
    events = [e["type"] for e in ctx.store.events_since(0, limit=10_000) if e["story_id"] == card]
    assert "story.resolved" in events and "worker.task_started" not in events
    await ctx.aclose()


async def test_the_line_adopts_a_card_that_already_names_the_failures(
    factory: Factory,  # noqa: F811
):
    """SP-013: S-091 ("A suíte de verificações já falha na versão principal", with the failing
    tests word for word) waited in the backlog while the line opened S-094 for the same tests."""
    ctx = make_ctx(factory)
    name = "tests/test_a.py::test_a_fails_here"
    existing = (
        ProductOwnerAgent(ctx)
        .add_item(
            "[Débito técnico] A suíte já falha na versão principal",
            f"Falhas pré-existentes: {name}.",
            origin="kaizen",
        )
        .story_id
    )
    _land(factory.root, "test_a.py", "def test_a_fails_here():\n    assert False\n", "feat(s-1): a")
    state = await LineWatch(ctx).check()
    assert state["card"] == existing
    assert ctx.store.get_story(existing)["priority_pinned"]
    titles = [s["title"] for s in ctx.store.list_stories(ctx.slug)]
    assert not any(t.startswith("Consertar a versão principal") for t in titles)
    await ctx.aclose()


async def test_a_failing_test_that_reads_the_factorys_records_is_named_in_the_card(
    factory: Factory,  # noqa: F811
):
    """tamagotchi AC6 read `.loompa/specs/S-077/plan.md`; S-094's spec asked to create records."""
    ctx = make_ctx(factory)
    _land(
        factory.root,
        "test_records.py",
        "from pathlib import Path\n\n\ndef test_plan_mentions_it():\n"
        "    assert 'x' in Path('.loompa/specs/S-1/plan.md').read_text()\n",
        "feat(s-1): a",
    )
    card = (await LineWatch(ctx).check())["card"]
    description = ctx.store.get_story(card)["description"]
    assert "tests/test_records.py lê(em) os registros internos da fábrica" in description
    assert "nunca criar ou editar registros da fábrica" in description
    await ctx.aclose()


async def test_a_green_base_closes_the_findings_filed_while_it_was_red(
    factory: Factory,  # noqa: F811
):
    """SP-013: S-091 ("A suíte de verificações já falha na versão principal") stayed in the
    backlog after the base it described was fixed."""
    from loompa.agents.inspector import BASE_RED_TITLE

    ctx = make_ctx(factory)
    watch = LineWatch(ctx)
    _land(factory.root, "test_a.py", "def test_a_fails_here():\n    assert False\n", "feat(s-1): a")
    card = (await watch.check())["card"]
    stale = (
        ProductOwnerAgent(ctx)
        .add_item(f"[Débito técnico] {BASE_RED_TITLE}", "Falhas: outra coisa.", origin="kaizen")
        .story_id
    )
    (factory.root / "tests" / "test_a.py").unlink()
    git("add", "-A", cwd=factory.root)
    git("commit", "-qm", f"fix({card.lower()}): conserta", cwd=factory.root)
    assert not (await watch.check())["red"]
    row = ctx.store.get_story(stale)
    assert row["stage"] == "CANCELLED" and row["state"]["extra"]["outcome"]["kind"] == "resolved"
    await ctx.aclose()


async def test_a_card_still_failing_says_who_thought_it_was_over(factory: Factory):  # noqa: F811
    """tamagotchi S-106 (11/10): the gate emptied its plan and the founder read "a história foi
    encerrada como já resolvida", which nobody had asked for, about a card that stayed open."""
    from loompa.engine import load_state
    from loompa.engine.graph import RESOLVE_KEY, node_resolve

    ctx = make_ctx(factory)
    _land(factory.root, "test_a.py", "def test_a_fails_here():\n    assert False\n", "feat(s-1): a")
    card = (await LineWatch(ctx).check())["card"]
    seen = []
    for founder in (True, False):
        state = load_state(ctx, card)
        state.extra[RESOLVE_KEY] = {"why": "x", "resume": "dev", "founder": founder}
        state = await node_resolve(ctx, state)
        assert state.stage == Stage.AWAITING_FOUNDER
        msg = ctx.store.get_message(state.blocked_message_id)
        assert "foi encerrada" not in msg.context and "continua aberta" in msg.context
        assert msg.executive_audit() == []
        seen.append(msg.context.split("\n\n")[-1].split(",")[0])
    assert seen == [
        "Você pediu para encerrar esta história como já resolvida",
        "A equipe concluiu que não havia mais nada a fazer nesta história",
    ]
    await ctx.aclose()


async def test_the_line_adopts_a_sprint_story_that_names_the_test_by_its_label(
    factory: Factory,  # noqa: F811
):
    """tamagotchi 11/10: S-101 ("Trocar o critério AC6 de casca_body_scripts por teste de
    comportamento ou removê-lo") was in the running sprint for node's failing "AC6: o
    plano/tasks da T8 da S-077 declara…", and the line opened S-106 for the same test."""
    ctx = make_ctx(factory)
    board = SprintBoard(ctx.store, ctx.slug)
    s101 = (
        ProductOwnerAgent(ctx)
        .add_item(
            "Trocar o critério AC6 de casca_body_scripts por teste de comportamento ou removê-lo",
            "Em tests/casca_body_scripts.test.js, o critério AC6 lê um documento interno da "
            "fábrica em vez de conferir o jogo e mantém a versão principal vermelha.",
        )
        .story_id
    )
    board.add(s101)
    board.start(board.draft().id, "meta")
    name = "AC6: o plano/tasks da T8 da S-077 declara a atualização de tests/casca.test.js"
    watch = LineWatch(ctx)
    other = ["casca_extra.test.js", "AC6: outro teste qualquer de outro arquivo"]
    assert watch._already_fixing([other[1]], [f"tests/{other[0]}"]) is None
    assert watch._already_fixing([name], ["tests/casca_body_scripts.test.js"]) == s101
    assert (
        watch._already_fixing(["AC7: outro critério"], ["tests/casca_body_scripts.test.js"]) is None
    )
    await ctx.aclose()


async def test_a_line_card_plans_the_fix_without_a_new_reproducer(factory: Factory):  # noqa: F811
    """tamagotchi S-106 (11/10): "reproducer first" gave the line card a test file that copied
    the repository and ran the whole suite again, to show what the red base already showed."""
    from typing import Any

    from loompa.agents.architect import REPRO_KEY, REPRODUCER_TASK
    from loompa.agents.dryrun import dry_run_script, role_of
    from loompa.engine import Scheduler, load_state
    from loompa.llm import Message
    from loompa.speckit import story_dir

    prompts: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "architect" and "## Spec" in messages[-1].content:
            prompts.append(messages[-1].content)
        if "Classify the story" in messages[0].content:  # as the Master classified S-106
            data = json.loads(dry_run_script(model, messages, tools))
            return json.dumps({**data, "kind": "bugfix"})
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    board = SprintBoard(ctx.store, ctx.slug)
    other = ProductOwnerAgent(ctx).add_item("Outra frente").story_id
    board.add(other)
    board.start(board.draft().id, "meta")
    _land(factory.root, "test_a.py", "def test_a_fails_here():\n    assert False\n", "feat(s-1): a")
    card = (await LineWatch(ctx).check())["card"]
    await Scheduler(ctx).run()
    state = load_state(ctx, card)
    mine = [p for p in prompts if card in p]
    assert mine and "This story fixes tests that fail on the main branch" in mine[0]
    assert "reproducer first" not in mine[0] and "test_a_fails_here" in mine[0]
    assert state.extra[REPRO_KEY]["status"] == "base_red"
    assert REPRODUCER_TASK not in story_dir(factory.root, card).tasks.read_text()
    await ctx.aclose()
