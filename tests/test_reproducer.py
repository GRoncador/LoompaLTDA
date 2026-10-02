"""Fase 7, items 7.7 (reproducer first on bugfixes) and 7.9 (the Architect's alternatives)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from loompa.agents.architect import REPRODUCER_TASK, ensure_reproducer, writable_tests
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.engine import Scheduler, load_state
from loompa.factory import Factory
from loompa.llm import Message, ToolCall
from test_engine import factory, make_ctx, seed_story, tool_results  # noqa: F401


def test_a_bugfix_plan_starts_with_its_reproducer():
    tasks, files = ensure_reproducer(["Corrigir add"], ["app/"])
    assert tasks == [REPRODUCER_TASK, "Corrigir add"] and files == ["app/", "tests/"]
    kept, same = ensure_reproducer(["Escrever teste que reproduz o bug", "Corrigir"], ["tests/"])
    assert kept[0].startswith("Escrever teste") and same == ["tests/"]
    assert writable_tests(["app/", "tests/", "src/x_test.py", ".loompa/specs/S-1/"]) == [
        "tests/",
        "src/x_test.py",
    ]


def _bug_in_base(root: Path) -> None:
    (root / "app" / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "bug"], cwd=root, check=True)


def _script(worker, *, alternatives: list[Any] | None = None):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role == "master" and "Classify the story" in messages[0].content:
            return json.dumps({"kind": "bugfix", "complexity": "STANDARD", "children": []})
        if role == "architect" and "## Founder's guidance" not in messages[-1].content:
            if "## Failure (filtered)" in messages[-1].content:
                return dry_run_script(model, messages, tools)
            return json.dumps(
                {
                    "approach": "corrigir o operador",
                    "alternatives_considered": alternatives or [],
                    "files": ["app/"],
                    "contracts": "",
                    "risks": [],
                    "tasks": ["Corrigir add"],
                    "adr_proposal": "",
                }
            )
        if role == "worker":
            return worker(messages)
        return dry_run_script(model, messages, tools)

    return script


REPRO = "from app.calc import add\n\n\ndef test_soma():\n    assert add(2, 3) == 5\n"


async def test_the_fix_starts_from_a_test_that_fails_on_the_old_code(factory: Factory):
    _bug_in_base(factory.root)
    seen: list[str] = []

    def worker(messages: list[Message]) -> Any:
        task = next(m.content for m in messages if m.role == "user")
        results = tool_results(messages)
        if "reproducer task" in task:
            if not results:
                seen.append("repro")
                return [
                    ToolCall(
                        "x",
                        "write_file",
                        {"path": "app/calc.py", "content": "no\n", "reason": "add subtracts"},
                    ),
                    ToolCall("t", "write_file", {"path": "tests/test_bug.py", "content": REPRO}),
                ]
            seen.append(results[0])
            return [ToolCall("d", "done", {"summary": "teste que reproduz"})]
        if not results:
            seen.append("fix")
            fixed = "def add(a, b):\n    return a + b\n"
            why = "add subtracted instead of adding"
            return [
                ToolCall("r", "read_file", {"path": "app/calc.py"}),
                ToolCall(
                    "w", "write_file", {"path": "app/calc.py", "content": fixed, "reason": why}
                ),
            ]
        return [ToolCall("d", "done", {"summary": "corrigido"})]

    alternatives = [
        {"option": "Reescrever calc", "tradeoff": "mais limpo", "rejected_because": "escopo"}
    ]
    ctx = make_ctx(factory, _script(worker, alternatives=alternatives))
    sid = seed_story(ctx, "Soma errada")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery", state.failure_history
    assert seen[0] == "repro" and "outside the plan's paths" in seen[1] and seen[2] == "fix"
    repro = state.extra["reproducer"]
    assert repro["status"] == "red" and any("test_soma" in f for f in repro["failing"])
    tasks_md = (factory.paths.specs / sid / "tasks.md").read_text()
    assert f"[x] T1: {REPRODUCER_TASK}" in tasks_md and "[x] T2: Corrigir add" in tasks_md
    log = subprocess.run(
        ["git", "log", "--format=%s"], cwd=state.worktree, capture_output=True, text=True
    ).stdout
    assert f"test({sid.lower()})" in log
    plan = (factory.paths.specs / sid / "plan.md").read_text()
    assert "## Alternativas consideradas" in plan
    assert "Reescrever calc — trade-off: mais limpo — descartada: escopo" in plan
    await ctx.aclose()


async def test_a_reproducer_that_never_fails_is_recorded_not_blocking(factory: Factory):
    rounds: list[str] = []

    def worker(messages: list[Message]) -> Any:
        task = next(m.content for m in messages if m.role == "user")
        if tool_results(messages):
            return [ToolCall("d", "done", {"summary": "ok"})]
        rounds.append("green" if "PASSES on the current code" in task else "task")
        content = "from app.calc import add\n\n\ndef test_ok():\n    assert add(1, 1) == 2\n"
        return [
            ToolCall("r", "read_file", {"path": "tests/test_bug.py"}),
            ToolCall(
                "t",
                "write_file",
                {"path": "tests/test_bug.py", "content": content, "reason": "reproduce it"},
            ),
        ]

    ctx = make_ctx(factory, _script(worker))
    sid = seed_story(ctx, "Bug que não aparece")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery"
    assert (
        rounds[:2] == ["task", "green"] and state.extra["reproducer"]["status"] == "not_reproduced"
    )
    cards = [s["title"] for s in ctx.store.list_stories(factory.slug) if s["origin"] == "kaizen"]
    assert any("reprodução" in t for t in cards)
    await ctx.aclose()


async def test_a_replanned_bugfix_whose_branch_has_the_fix_needs_no_reproducer(factory: Factory):
    """contas S-049: re-planned after its fix was already on the branch, the plan opened with a
    reproducer again; it could only pass, and the Worker blocked asking whether to undo the fix."""
    from loompa.agents import ArchitectAgent
    from loompa.agents.architect import REPRO_KEY
    from loompa.engine.state import StoryKind

    seen: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "architect":
            seen.append(messages[-1].content)
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Corrigir soma")
    st = load_state(ctx, sid)
    st.kind = StoryKind.BUGFIX
    st.commits = ["abc123"]  # the branch already carries work, the fix among it
    (factory.paths.specs / sid).mkdir(parents=True, exist_ok=True)
    res = await ArchitectAgent(ctx).run(st)
    assert res.ok and st.extra[REPRO_KEY] == {"status": "branch_has_fix"}
    assert "reproducer first" not in seen[-1]
    tasks = (factory.paths.specs / sid / "tasks.md").read_text()
    assert REPRODUCER_TASK.strip()[:40] not in tasks
    assert any(p.rstrip("/") == "tests" or p.startswith("tests") for p in st.allowed_paths)
    await ctx.aclose()
