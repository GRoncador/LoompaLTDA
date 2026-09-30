"""Fase 7, item 7.10: diff hygiene — leftovers found in code, not by asking a model."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loompa.agents import DeployerAgent, InspectorAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.engine import Scheduler, load_state
from loompa.engine.state import StoryState
from loompa.factory import Factory
from loompa.hygiene import blocking, is_debris, is_test_path, scan_diff
from loompa.llm import Message, ToolCall
from test_engine import factory, make_ctx, seed_story, tool_results  # noqa: F401


def _diff(path: str, added: list[str], *, new: bool = True, removed: list[str] = ()) -> str:
    head = f"diff --git a/{path} b/{path}\n" + ("new file mode 100644\n" if new else "")
    body = "".join(f"-{r}\n" for r in removed) + "".join(f"+{a}\n" for a in added)
    return head + f"--- a/{path}\n+++ b/{path}\n@@ -1 +1 @@\n" + body


def test_debris_and_test_paths():
    assert is_debris("debug.txt") and is_debris("src/app.py.orig") and is_debris("run.log")
    assert is_debris("output.json") and not is_debris("tests/fixtures/output.json")
    assert not is_debris("src/debug.py") and not is_debris("README.md")
    assert is_test_path("tests/test_cli.py") and is_test_path("src/a.test.ts")
    assert not is_test_path("src/contas/cli.py")


def test_the_contas_leftover_is_blocking():
    issues = scan_diff(_diff("debug.txt", ["/Users/gustavo/.loompa/worktrees/contas/S-005"]))
    assert {i.rule for i in blocking(issues)} == {"debris_file", "machine_path"}


def test_debugger_and_conflict_markers_block_todo_and_prints_only_warn():
    issues = scan_diff(
        _diff(
            "src/app.py",
            [
                "import pdb",
                "<<<<<<< HEAD",
                "# TODO: later",
                'print("debug", x)',
                "=======",  # a Markdown underline looks the same: never flagged alone
            ],
            new=False,
        )
    )
    rules = {i.rule: i.blocking for i in issues}
    assert rules == {"debugger": True, "conflict_marker": True, "todo": False, "debug_print": False}


def test_placeholders_and_test_data_are_not_leaks():
    assert scan_diff(_diff("docs/setup.md", ["cd /home/user/projeto"])) == []
    in_test = scan_diff(_diff("tests/test_paths.py", ['assert f("/home/ana/x") == "x"']))
    assert in_test and not blocking(in_test)  # noted, not failed


def test_whitespace_only_change_is_noted():
    issues = scan_diff(_diff("src/app.py", ["x  =  1"], new=False, removed=["x = 1"]))
    assert [(i.rule, i.blocking) for i in issues] == [("whitespace_only", False)]
    assert scan_diff(_diff("src/app.py", ["x = 2"], new=False, removed=["x = 1"])) == []


# ------------------------------------------------------------------------ in the pipeline


async def test_the_worker_cleans_what_it_left_before_the_task_is_committed(factory: Factory):
    rounds: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) != "worker":
            return dry_run_script(model, messages, tools)
        task = next(m.content for m in messages if m.role == "user")
        if tool_results(messages):
            return [ToolCall("d", "done", {"summary": "ok"})]
        if "Diff hygiene" in task:
            rounds.append("cleanup")
            return [ToolCall("x", "delete_file", {"path": "tests/debug.txt"})]
        rounds.append("work")
        return [
            ToolCall(
                "a",
                "write_file",
                {"path": "tests/test_n.py", "content": "def test_n():\n    assert 2 + 2 == 4\n"},
            ),
            ToolCall(
                "b", "write_file", {"path": "tests/debug.txt", "content": "/Users/gustavo/x\n"}
            ),
        ]

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Com resíduo")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery" and rounds == ["work", "cleanup"]
    assert not (Path(state.worktree) / "tests" / "debug.txt").exists()
    assert "debug.txt" not in ctx.worktrees.diff(ctx.worktrees.get(sid))
    events = [e for e in ctx.store.events_since(0, limit=10_000) if e["type"] == "worker.hygiene"]
    assert len(events) == 1 and "tests/debug.txt" in json.dumps(events[0]["payload"])
    await ctx.aclose()


async def test_the_inspector_fails_a_story_that_still_carries_debris(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    wt = ctx.worktrees.create("S-900", title="resíduo")
    (wt.path / "app" / "calc.py").write_text("def add(a, b):\n    breakpoint()\n    return a + b\n")
    ctx.worktrees.commit_all(wt, "feat: add")
    state = StoryState(story_id="S-900", title="resíduo")
    res = await InspectorAgent(ctx).run(state, wt)
    assert not res.ok and res.data["verdict"] == "FAIL"
    assert "[hygiene] FAIL" in res.summary and "breakpoint()" in res.summary
    await ctx.aclose()


async def test_the_deployer_never_commits_untracked_debris(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    wt = ctx.worktrees.create("S-901", title="entrega")
    (wt.path / "app" / "extra.py").write_text("X = 1\n")
    ctx.worktrees.commit_all(wt, "feat: extra")
    (wt.path / "app" / "calc.py.orig").write_text("old\n")
    (wt.path / "notes.md").write_text("fica\n")
    state = StoryState(story_id="S-901", title="entrega")
    res = await DeployerAgent(ctx).run(state, wt)
    assert res.ok
    stat = ctx.worktrees.diff_stat(wt)
    assert "calc.py.orig" not in stat and "notes.md" in stat
    assert not (wt.path / "app" / "calc.py.orig").exists()
    await ctx.aclose()


# ------------------------------------------------- data a test run leaves in the repository


def test_a_new_data_file_at_the_root_is_stray_unless_the_plan_lists_it():
    from loompa.hygiene import is_stray_data

    assert is_stray_data("gastos.json") and is_stray_data("export.csv")
    assert not is_stray_data("data/gastos.json")  # inside a folder: the project's own data
    assert not is_stray_data("package.json") and not is_stray_data("tsconfig.app.json")
    assert not is_stray_data(".eslintrc.json") and not is_stray_data("README.md")
    assert not is_stray_data("gastos.json", ["gastos.json"])
    assert not is_stray_data("categorias.csv", ["*.csv"])
    issues = scan_diff(_diff("gastos.json", ['[{"valor": 10}]']))
    assert [(i.rule, i.blocking) for i in issues] == [("stray_data", True)]
    assert scan_diff(_diff("gastos.json", ["[]"]), ["gastos.json"]) == []
    assert scan_diff(_diff("gastos.json", ["[]"], new=False, removed=["{}"])) == []


WRITES_TO_CWD = (
    "import json\nfrom pathlib import Path\n\n\ndef test_saves():\n"
    '    Path("gastos.json").write_text(json.dumps([10]))\n'
    '    assert json.loads(Path("gastos.json").read_text()) == [10]\n'
)
WRITES_TO_TMP = (
    "import json\n\n\ndef test_saves(tmp_path):\n"
    '    f = tmp_path / "gastos.json"\n    f.write_text(json.dumps([10]))\n'
    "    assert json.loads(f.read_text()) == [10]\n"
)


async def test_run_tests_says_at_once_what_the_run_left_in_the_repository(factory: Factory):
    from loompa.aci import ACI

    root = factory.root
    (root / "tests" / "test_saves.py").write_text(WRITES_TO_CWD)
    aci = ACI(root, test_command=factory.config.quality.test_command)
    out = (await aci.call("run_tests", {})).output
    assert "[hygiene] This test run created files in the repository: gastos.json" in out
    assert aci.test_residue == {"gastos.json"}


def test_reports_a_runner_writes_by_configuration_are_not_residue():
    from loompa.hygiene import run_residue

    after = {"gastos.json", ".coverage", "coverage.xml", "htmlcov/index.html", "junit.xml"}
    assert run_residue(set(), after | {"tests/fixtures/saida.json"}) == ["gastos.json"]


async def test_the_inspector_fails_a_suite_that_writes_into_the_repository(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    wt = ctx.worktrees.create("S-902", title="grava na raiz")
    (wt.path / "tests" / "test_saves.py").write_text(WRITES_TO_CWD)
    ctx.worktrees.commit_all(wt, "test: saves")
    state = StoryState(story_id="S-902", title="grava na raiz")
    res = await InspectorAgent(ctx).run(state, wt)
    assert not res.ok and "[hygiene] FAIL" in res.summary and "gastos.json" in res.summary
    assert not (wt.path / "gastos.json").exists()  # this run's copy is not left behind
    await ctx.aclose()


async def test_the_deployer_drops_an_untracked_data_file_the_plan_does_not_list(
    factory: Factory,
):
    ctx = make_ctx(factory, dry_run=True)
    wt = ctx.worktrees.create("S-903", title="entrega")
    (wt.path / "app" / "extra.py").write_text("X = 1\n")
    ctx.worktrees.commit_all(wt, "feat: extra")
    (wt.path / "gastos.json").write_text("[]\n")
    (wt.path / "categorias.json").write_text("[]\n")
    state = StoryState(story_id="S-903", title="entrega", allowed_paths=["app/", "categorias.json"])
    res = await DeployerAgent(ctx).run(state, wt)
    assert res.ok
    stat = ctx.worktrees.diff_stat(wt)
    assert "gastos.json" not in stat and "categorias.json" in stat
    await ctx.aclose()


async def test_the_worker_fixes_a_test_that_wrote_into_the_repository(factory: Factory):
    """`contas` S-007: a test wrote `gastos.json` to the working directory and the task commit
    took it. Now the test run says so, the self-check asks for the fix, and the file is never
    committed."""
    rounds: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) != "worker" or not tools:
            return dry_run_script(model, messages, tools)
        task = next(m.content for m in messages if m.role == "user")
        results = tool_results(messages)
        if "a test run created it" in task:
            if results:
                return [ToolCall("d2", "done", {"summary": "teste usa tmp_path"})]
            rounds.append("fix")
            return [
                ToolCall("r", "read_file", {"path": "tests/test_saves.py"}),
                ToolCall(
                    "w", "write_file", {"path": "tests/test_saves.py", "content": WRITES_TO_TMP}
                ),
                ToolCall("x", "delete_file", {"path": "gastos.json"}),
            ]
        if not results:
            rounds.append("work")
            return [
                ToolCall(
                    "a", "write_file", {"path": "tests/test_saves.py", "content": WRITES_TO_CWD}
                ),
                ToolCall("t", "run_tests", {}),
            ]
        assert "[hygiene] This test run created files" in results[-1]
        return [ToolCall("d", "done", {"summary": "ok"})]

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Salvar gastos")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert rounds == ["work", "fix"] and state.blocked_reason == "delivery"
    wt = ctx.worktrees.get(sid)
    assert "diff --git a/gastos.json" not in ctx.worktrees.diff(wt)  # never committed
    assert not (wt.path / "gastos.json").exists()
    await ctx.aclose()


async def test_leftovers_are_dropped_even_after_the_inspector_looked_at_the_diff(
    factory: Factory,
):
    """The Inspector's `diff_working` runs `git add -N`, after which a new file no longer shows
    as untracked (`??`): the Deployer's old check never saw a leftover in a real run."""
    ctx = make_ctx(factory, dry_run=True)
    wt = ctx.worktrees.create("S-904", title="entrega")
    (wt.path / "app" / "extra.py").write_text("X = 1\n")
    ctx.worktrees.commit_all(wt, "feat: extra")
    (wt.path / "debug.txt").write_text("/Users/gustavo/x\n")
    (wt.path / "gastos.json").write_text("[]\n")
    ctx.worktrees.diff_working(wt)  # what the Inspector does before the delivery
    state = StoryState(story_id="S-904", title="entrega", allowed_paths=["app/"])
    res = await DeployerAgent(ctx).run(state, wt)
    assert res.ok
    stat = ctx.worktrees.diff_stat(wt)
    assert "debug.txt" not in stat and "gastos.json" not in stat and "extra.py" in stat
    await ctx.aclose()
