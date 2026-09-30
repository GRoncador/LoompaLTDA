"""Fase 7, item 7.2: the Inspector's rubric, evidence for tests, and self-healing."""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path
from typing import Any

from loompa.agents.dryrun import dry_run_script, role_of
from loompa.engine import Scheduler, load_state
from loompa.factory import Factory
from loompa.hygiene import weak_tests
from loompa.llm import Message, ToolCall
from test_engine import factory, make_ctx, seed_story, tool_results  # noqa: F401


def _worker_writes(path: str, content: str):
    def worker(messages: list[Message]) -> Any:
        if tool_results(messages):
            return [ToolCall("d", "done", {"summary": "ok"})]
        return [ToolCall("w", "write_file", {"path": path, "content": content})]

    return worker


def test_tests_that_prove_nothing_are_found_without_a_model():
    def new_test(body: str) -> str:
        lines = ["def test_x():", *body.splitlines()]
        return (
            "diff --git a/tests/test_x.py b/tests/test_x.py\nnew file mode 100644\n"
            "--- /dev/null\n+++ b/tests/test_x.py\n@@ -0,0 +1 @@\n"
            + "".join(f"+{line}\n" for line in lines)
        )

    assert weak_tests(new_test("    assert True"))[0]["severity"] == "high"
    assert weak_tests(new_test("    run()"))[0]["text"].endswith("assert nothing")
    assert weak_tests(new_test("    assert add(1, 2) == 3")) == []
    code_only = (
        "diff --git a/app/calc.py b/app/calc.py\n--- a/app/calc.py\n+++ b/app/calc.py\n"
        "@@ -1 +1 @@\n-    return a + b\n+    return a - b\n"
    )
    assert "no test in the diff" in weak_tests(code_only)[0]["text"]


async def test_the_judge_sees_the_checks_and_a_failure_needs_a_reason(factory: Factory):
    """`contas` S-002: with 44 tests green the judge wrote "the suite does not pass". It now
    reads the checks as facts, and a criterion failed without a reason is not a failure."""
    judged: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role == "worker":
            return _worker_writes("tests/test_n.py", "def test_n():\n    assert 2 * 3 == 6\n")(
                messages
            )
        if role == "inspector":
            judged.append(messages[-1].content)
            return json.dumps(
                {
                    "criteria": [{"text": "c1", "pass": False, "reason": ""}],
                    "findings": [],
                    "summary": "?",
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Com juiz")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery" and state.qa_verdict == "PASS"
    assert "## Automated checks (already run; facts)" in judged[0]
    assert "[pytest] PASS" in judged[0]
    await ctx.aclose()


async def test_the_founders_guidance_reaches_the_judge_and_the_self_check(factory: Factory):
    """`contas` S-030: the founder withdrew a criterion the spec still stated; the self-check
    kept asking for it and the judge would have failed the story on it."""
    seen: dict[str, list[str]] = {"inspector": [], "dod": []}

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role in seen:
            seen[role].append(messages[-1].content)
        if role == "worker":
            return _worker_writes("tests/test_n.py", "def test_n():\n    assert 2 * 3 == 6\n")(
                messages
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Com orientação")
    await Scheduler(ctx).restart_story(sid, "o critério das bordas saiu; as caixas podem ficar")
    await Scheduler(ctx).run()
    for role in ("inspector", "dod"):
        assert seen[role] and "Guidance from the founder" in seen[role][0], role
        assert "as caixas podem ficar" in seen[role][0]
    await ctx.aclose()


async def test_a_lint_only_failure_is_fixed_by_the_linter_before_it_counts(factory: Factory):
    """`contas` S-003/S-006: green tests, one import-order finding, a whole retry round."""
    factory.config.quality.lint_command = (
        f"{shlex.quote(sys.executable)} -m ruff check --select I ."
    )
    factory.save()
    factory = Factory.open(factory.root)
    unsorted = "import sys\nimport os\n\n\ndef test_n():\n    assert os.sep and sys.maxsize > 0\n"

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "worker":
            return _worker_writes("tests/test_n.py", unsorted)(messages)
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Imports")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery", state.failure_history
    assert state.attempts_tier2 == 0 and not state.failure_history  # never counted as a failure
    text = (Path(state.worktree) / "tests" / "test_n.py").read_text()
    assert text.index("import os") < text.index("import sys")
    types = [e["type"] for e in ctx.store.events_since(0, limit=10_000)]
    assert "story.lint_autofixed" in types
    await ctx.aclose()


async def test_a_tautological_test_gets_one_worker_round_before_the_founder(factory: Factory):
    rounds: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) != "worker":
            return dry_run_script(model, messages, tools)
        task = next(m.content for m in messages if m.role == "user")
        if tool_results(messages):
            return [ToolCall("d", "done", {"summary": "ok"})]
        if "Inspector found (high severity)" in task:
            rounds.append("heal")
            real = "def test_n():\n    assert sum([1, 2]) == 3\n"
            return [
                ToolCall("r", "read_file", {"path": "tests/test_n.py"}),
                ToolCall(
                    "w",
                    "write_file",
                    {"path": "tests/test_n.py", "content": real, "reason": "the test was fake"},
                ),
            ]
        rounds.append("work")
        return [
            ToolCall(
                "w",
                "write_file",
                {"path": "tests/test_n.py", "content": "def test_n():\n    assert True\n"},
            )
        ]

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Teste de verdade")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert rounds == ["work", "heal"]
    assert state.blocked_reason == "delivery" and state.qa_verdict == "PASS"
    types = [e["type"] for e in ctx.store.events_since(0, limit=10_000)]
    assert types.count("story.self_healing") == 1
    await ctx.aclose()
