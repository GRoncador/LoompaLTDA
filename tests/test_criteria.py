"""Fase 7, item 1: a criterion the product cannot meet goes back to the Product Owner instead of up
the tiers, and the founder is told whether the factory's own test is failing or the product broke
(`contas` S-030: "no border in any help", which Typer always draws)."""

from __future__ import annotations

import json
from typing import Any

from conftest import git
from loompa.aci import summarize_tests
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.agents.inspector import test_origin as origin_of
from loompa.engine import Scheduler, load_state
from loompa.factory import Factory
from loompa.llm import Message, ToolCall
from loompa.speckit import story_dir
from test_engine import factory, make_ctx, seed_story, tool_results  # noqa: F401

PYTEST_OUT = """\
F.                                                                       [100%]
=================================== FAILURES ===================================
_______________________________ test_sem_bordas ________________________________

    def test_sem_bordas():
>       assert "│" not in ajuda()
E       AssertionError: assert '│' not in '│ ajuda │'

src/contas/cli.py:12: AssertionError
=========================== short test summary info ============================
FAILED tests/test_help.py::test_sem_bordas - AssertionError: assert '│' not in...
1 failed, 1 passed in 0.05s
"""


def test_a_pytest_failure_keeps_the_file_of_the_test_itself():
    (failure,) = summarize_tests(PYTEST_OUT, 1).failures
    assert failure.name == "test_sem_bordas"
    assert failure.nodeid == "tests/test_help.py::test_sem_bordas"
    assert failure.location.startswith("src/contas/cli.py")  # the deepest frame: not the test


def test_failing_tests_are_told_apart_by_whether_the_base_had_them(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    wt = ctx.worktrees.create("S-950", title="origem")
    (wt.path / "tests" / "test_calc.py").write_text(
        "from app.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n\n\n"
        "def test_new_here():\n    assert add(2, 2) == 4\n"
    )
    (wt.path / "tests" / "test_help.py").write_text("def test_sem_bordas():\n    assert 0\n")
    ctx.worktrees.commit_all(wt, "test: novos")
    got = origin_of(
        ctx.worktrees,
        wt,
        [
            {"name": "test_add", "nodeid": "tests/test_calc.py::test_add"},
            {"name": "test_new_here", "nodeid": "tests/test_calc.py::test_new_here"},
            {"name": "test_sem_bordas", "location": "tests/test_help.py:2"},
            {"name": "TestX.test_param[1]", "nodeid": "tests/test_help.py::TestX::test_param[1]"},
            {"name": "(unrecognised output)"},
        ],
    )
    assert got == {
        "existing": ["test_add"],
        "own": ["test_new_here", "test_sem_bordas", "TestX.test_param[1]"],
        "unknown": ["(unrecognised output)"],
    }


IMPOSSIBLE = 'def test_sem_bordas():\n    assert "│" not in "│ ajuda │"\n'
POSSIBLE = 'def test_ajuda_lista_comandos():\n    assert "somar" in "│ somar │"\n'


def spec_with_two_criteria(messages: list[Message]) -> str:
    return json.dumps(
        {
            "goal": "Ajuda em português",
            "in_scope": ["ajuda"],
            "out_of_scope": [],
            "acceptance": [
                "Dado o comando de ajuda, então ele lista o comando somar",
                "A ajuda não mostra nenhuma borda",
            ],
            "rules": [],
            "questions": [],
            "needs_decision": False,
        }
    )


def s030(po_withdraws: bool):
    reviews: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role == "product":
            return spec_with_two_criteria(messages)
        if role == "product_owner" and "guardian of the spec" in messages[0].content:
            reviews.append(messages[-1].content)
            criteria = (
                [{"n": 2, "action": "withdraw", "new_text": "", "reason": "o Typer sempre desenha"}]
                if po_withdraws
                else []
            )
            return json.dumps({"criteria": criteria, "summary": "revisado"})
        if role != "worker" or not tools:
            return dry_run_script(model, messages, tools)
        task = next(m.content for m in messages if m.role == "user")
        if tool_results(messages):
            return [ToolCall("d", "done", {"summary": "ok"})]
        content = POSSIBLE if "The Product Owner revised" in task else IMPOSSIBLE
        return [
            ToolCall("r", "read_file", {"path": "tests/test_help.py"}),  # read before write
            ToolCall(
                "w",
                "write_file",
                # a fix pass states its root cause with the first edit (Fase 7, 7.11)
                {"path": "tests/test_help.py", "content": content, "reason": "critério revisto"},
            ),
        ]

    return script, reviews


async def test_a_criterion_the_product_cannot_meet_goes_back_to_the_product_owner(
    factory: Factory,
):
    script, reviews = s030(po_withdraws=True)
    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Ajuda em português")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery"  # delivered after the revision
    assert len(reviews) == 1 and "test_sem_bordas" in reviews[0] and IMPOSSIBLE[:20] in reviews[0]
    assert state.attempts_tier2 == 1 and state.current_tier == "tier2"  # no tier spent on it
    assert state.acceptance == ["Dado o comando de ajuda, então ele lista o comando somar"]
    spec = story_dir(factory.root, sid).spec.read_text()
    assert "Critérios revistos pelo Product Owner" in spec and "o Typer sempre desenha" in spec
    delivery = ctx.store.get_message(state.blocked_message_id)
    assert "O Product Owner revisou 1 critério" in delivery.context
    assert delivery.executive_audit() == []
    types = [e["type"] for e in ctx.store.events_since(0, limit=5000) if e["story_id"] == sid]
    assert "spec.criteria_revised" in types and "story.escalated" not in types
    # the judge reads a spec excerpt that stops before the revision: it is told separately
    from loompa.agents.inspector import _criteria_revision

    assert "withdrawn: A ajuda não mostra nenhuma borda" in _criteria_revision(state)
    fixes = [
        e["payload"]
        for e in ctx.store.events_since(0, limit=5000)
        if e["type"] == "worker.task_finished"
        and e["story_id"] == sid
        and e["payload"]["task"] == 0
    ]
    assert fixes and {f["origin"] for f in fixes} == {"inspector"}
    assert {f["outcome"] for f in fixes} == {"finished"}
    await ctx.aclose()


async def test_when_the_criterion_stays_the_founder_hears_the_product_was_not_affected(
    factory: Factory,
):
    script, reviews = s030(po_withdraws=False)
    ctx = make_ctx(factory, script, dry_run=True)  # the founder's text without a model rewrite
    sid = seed_story(ctx, "Ajuda em português")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert len(reviews) == 1  # asked once; kept, the ladder goes on
    assert state.blocked_reason == "persistent_failure"
    msg = ctx.store.get_message(state.blocked_message_id)
    assert "Os testes que a equipe escreveu" in msg.context and "continuam passando" in msg.context
    assert msg.executive_audit() == []
    assert state.failure_history[-1].startswith("[facts] Every failing test was written")
    await ctx.aclose()


async def test_a_test_the_base_already_had_failing_is_the_product_breaking(factory: Factory):
    factory.config.schedule.tier2_max_attempts = 1
    factory.save()
    reviews: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role == "product_owner" and "guardian of the spec" in messages[0].content:
            reviews.append("asked")
        if role != "worker" or not tools:
            return dry_run_script(model, messages, tools)
        if tool_results(messages):
            return [ToolCall("d", "done", {"summary": "ok"})]
        # breaks a test the base already had (the plan's paths allow tests/ only)
        return [
            ToolCall("r", "read_file", {"path": "tests/test_calc.py"}),
            ToolCall(
                "w",
                "write_file",
                {
                    "path": "tests/test_calc.py",
                    "content": "from app.calc import add\n\n\ndef test_add():\n"
                    "    assert add(1, 2) == 4\n",
                },
            ),
        ]

    ctx = make_ctx(factory, script, dry_run=True)
    sid = seed_story(ctx, "Mexe na soma")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "persistent_failure" and reviews == []  # no criterion to blame
    msg = ctx.store.get_message(state.blocked_message_id)
    assert "partes do produto que já funcionavam" in msg.context and msg.executive_audit() == []
    assert "test_add" in state.failure_history[-1]
    assert git("log", "--oneline", "-1", cwd=factory.root).endswith("feat: calc")  # nothing merged
    await ctx.aclose()
