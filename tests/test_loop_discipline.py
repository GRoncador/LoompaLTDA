"""Fase 7, item 7.11: read before write, and no more re-reading loops (measured in `contas`)."""

from __future__ import annotations

from pathlib import Path

from loompa.aci import ACI
from loompa.aci.tools import ToolResult
from loompa.agents.loopguard import REPEAT_PREFIX, LoopGuard
from loompa.agents.toolbox import PROFILES, Toolbox, prune_tool_history
from loompa.llm import Message, ToolCall


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    return tmp_path


# --------------------------------------------------------------------- read before write


async def test_an_existing_file_is_edited_only_after_it_was_read(tmp_path: Path):
    aci = ACI(_repo(tmp_path))
    aci.require_read = True
    res = await aci.call("edit_file", {"path": "app/calc.py", "old": "a + b", "new": "b + a"})
    assert not res.ok and "read app/calc.py with read_file" in res.output
    res = await aci.call("write_file", {"path": "app/calc.py", "content": "x = 1\n"})
    assert not res.ok  # overwriting blind is refused too
    assert (await aci.call("write_file", {"path": "app/new.py", "content": "y = 2\n"})).ok
    assert (await aci.call("edit_file", {"path": "app/new.py", "old": "2", "new": "3"})).ok
    assert (await aci.call("read_file", {"path": "app/calc.py", "lines": 1})).ok
    assert (await aci.call("edit_file", {"path": "app/calc.py", "old": "a + b", "new": "b + a"})).ok
    aci.begin_task()  # a new task is a new conversation: what was read is not in it
    res = await aci.call("apply_patch", {"patch": "--- a/app/calc.py\n+++ b/app/calc.py\n"})
    assert not res.ok


async def test_without_the_flag_other_roles_write_as_before(tmp_path: Path):
    aci = ACI(_repo(tmp_path))
    assert (await aci.call("write_file", {"path": "app/calc.py", "content": "x = 1\n"})).ok


# --------------------------------------------------------------------------- the guard


async def test_a_repeated_read_points_back_to_the_history_until_something_changes(
    tmp_path: Path,
):
    aci = ACI(_repo(tmp_path))
    guard = LoopGuard(aci)
    args = {"path": "app/calc.py"}
    assert guard.before("read_file", args) is None
    first = await aci.call("read_file", args)
    msg = Message("tool", first.output, tool_call_id="1", name="read_file")
    guard.after("read_file", args, first, msg)
    again = guard.before("read_file", args)
    assert again is not None and again.output.startswith(REPEAT_PREFIX)
    assert "still above in your history" in again.output and guard.repeats == 1
    msg.content = "[resumido] resultado anterior de read_file ..."  # pruned: reading is fair
    assert guard.before("read_file", args) is None
    msg.content = first.output
    await aci.call("write_file", {"path": "app/other.py", "content": "z = 1\n"})
    assert guard.before("read_file", args) is None  # the tree changed


def test_a_repeated_test_run_with_nothing_changed_is_not_run_again():
    class Tree:
        version = 0

    guard = LoopGuard(Tree())  # type: ignore[arg-type]
    guard.after("run_tests", {}, ToolResult(True, "[pytest] FAIL (1 failed)\n- test_x"), None)
    again = guard.before("run_tests", {})
    assert again is not None and "[pytest] FAIL (1 failed)" in again.output
    Tree.version = 1
    assert guard.before("run_tests", {}) is None


def test_a_streak_of_reads_gets_told_to_stop_exploring():
    guard = LoopGuard(None, explore_nudge=3)
    notes = [
        guard.after("search", {"pattern": str(i)}, ToolResult(True, "x"), None) for i in range(3)
    ]
    assert notes[:2] == ["", ""] and "stop exploring" in notes[2] and guard.nudges == 1
    guard.after("edit_file", {}, ToolResult(True, "ok"), None)
    assert guard.reads_in_a_row == 0


def test_a_write_tool_that_keeps_failing_gets_another_way_suggested():
    guard = LoopGuard(None)
    fail = ToolResult(False, "erro: hunk não casa")
    notes = [guard.after("apply_patch", {"patch": str(i)}, fail, None) for i in range(3)]
    assert notes[2] and "edit_file" in notes[2]
    assert guard.after("apply_patch", {}, ToolResult(True, "ok"), None) == ""


# ------------------------------------------------------------------------ diagnosis


async def test_a_fix_states_its_root_cause_with_its_first_edit(tmp_path: Path):
    aci = ACI(_repo(tmp_path))
    box = Toolbox(aci, PROFILES["worker"], diagnosis=True)
    write = next(t for t in box.spec() if t["name"] == "write_file")
    assert "reason" in write["parameters"]["required"]
    res = await box.call("write_file", {"path": "app/a.py", "content": "a = 1\n"})
    assert not res.ok and "root cause" in res.output
    why = "add() ignored negative numbers because of the early return"
    res = await box.call("write_file", {"path": "app/a.py", "content": "a = 1\n", "reason": why})
    assert res.ok and box.diagnosis == why
    assert (await box.call("write_file", {"path": "app/b.py", "content": "b = 1\n"})).ok
    plain = Toolbox(aci, PROFILES["worker"])
    plain_write = next(t for t in plain.spec() if t["name"] == "write_file")
    assert "reason" not in plain_write["parameters"]["properties"]
    # a `reason` given anyway never reaches the ACI as an unknown argument
    res = await plain.call("write_file", {"path": "app/c.py", "content": "c\n", "reason": "x"})
    assert res.ok


# -------------------------------------------------------------------------- pruning


def test_pruning_keeps_the_latest_read_of_each_file_while_it_is_current():
    body = "x" * 800

    def call(i: int, name: str, path: str) -> list[Message]:
        tc = ToolCall(f"c{i}", name, {"path": path})
        return [
            Message("assistant", "", tool_calls=[tc]),
            Message("tool", f"{path} {body}", tool_call_id=f"c{i}", name=name),
        ]

    messages = [Message("system", "s"), Message("user", "u")]
    messages += call(1, "read_file", "a.py")  # superseded by the read at 3
    messages += call(2, "read_file", "b.py")  # stale: b.py is written at 4
    messages += call(3, "read_file", "a.py")
    messages += call(4, "edit_file", "b.py")
    messages += call(5, "read_file", "c.py")
    prune_tool_history(messages, keep_last=1, keep_files_chars=10_000)
    tools = [m for m in messages if m.role == "tool"]
    assert [t.content.startswith("[resumido]") for t in tools] == [True, True, False, True, False]
    prune_tool_history(messages, keep_last=1, keep_files_chars=0)
    assert tools[2].content.startswith("[resumido]")  # without the budget: as before
