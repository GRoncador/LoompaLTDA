import shlex
import sys
from pathlib import Path

import pytest

from loompa.aci import ACI, run_command, summarize_lint, summarize_tests, summarize_typecheck

PYTEST_OUT = (
    """============================= test session starts ==============================
collected 3 items

tests/test_a.py .F.                                                      [100%]

=================================== FAILURES ===================================
__________________________________ test_two ____________________________________

    def test_two():
>       assert add(1, 1) == 3
E       assert 2 == 3
E        +  where 2 = add(1, 1)

tests/test_a.py:7: AssertionError
=========================== short test summary info ============================
FAILED tests/test_a.py::test_two - assert 2 == 3
========================= 1 failed, 2 passed in 0.03s ==========================
"""
    + "INFO noise line\n" * 500
)


def test_pytest_summary_is_surgical():
    s = summarize_tests(PYTEST_OUT, 1)
    assert not s.ok and s.passed == 2 and s.failed == 1
    assert len(s.failures) == 1
    f = s.failures[0]
    assert (
        f.name == "test_two" and f.location == "tests/test_a.py:7" and "assert 2 == 3" in f.message
    )
    text = s.compact()
    assert "noise" not in text and len(text) < 400 and "[pytest] FAIL (2 passed, 1 failed)" in text
    ok = summarize_tests("============ 3 passed in 0.1s ============", 0)
    assert ok.ok and ok.passed == 3 and ok.compact() == "[pytest] PASS (3 passed)"


def test_jest_and_unknown_summaries():
    out = "FAIL src/a.test.ts\n  ● adds numbers\n\n    Expected: 3\n    Received: 2\n\n      at Object.<anonymous> (src/a.test.ts:4:15)\n\nTests:       1 failed, 2 passed, 3 total\n"
    s = summarize_tests(out, 1)
    assert s.tool == "jest/vitest" and s.failed == 1 and s.passed == 2
    assert s.failures[0].name == "adds numbers" and "Expected: 3" in s.failures[0].message
    unk = summarize_tests("boom\nsomething broke", 2)
    assert not unk.ok and "something broke" in unk.failures[0].message


def test_lint_and_typecheck_summaries():
    ruff = "src/x.py:10:5: F401 `os` imported but unused\nsrc/y.py:2:1: E302 expected 2 blank lines\nFound 2 errors.\n"
    s = summarize_lint(ruff, 1)
    assert s.issues == [
        "src/x.py:10 F401 `os` imported but unused",
        "src/y.py:2 E302 expected 2 blank lines",
    ]
    eslint = "/app/src/a.ts\n  3:7  error  'x' is assigned but never used  no-unused-vars\n\n✖ 1 problem\n"
    assert summarize_lint(eslint, 1).issues == [
        "/app/src/a.ts:3 no-unused-vars 'x' is assigned but never used"
    ]
    mypy = "src/x.py:5: error: Incompatible return value type  [return-value]\nsrc/x.py:9: note: hint\nFound 1 error\n"
    assert summarize_typecheck(mypy, 1).issues == [
        "src/x.py:5 Incompatible return value type  [return-value]"
    ]
    tsc = "src/a.ts(3,7): error TS2322: Type 'string' is not assignable to type 'number'.\n"
    assert summarize_typecheck(tsc, 2).issues == [
        "src/a.ts:3 TS2322 Type 'string' is not assignable to type 'number'."
    ]
    assert summarize_lint("", 0).ok and summarize_lint("", 0).compact() == "[lint] PASS"


async def test_run_command_bounded(tmp_path: Path):
    res = await run_command("python3 -c \"print('hi'); import sys; sys.exit(3)\"", tmp_path)
    assert res.returncode == 3 and res.stdout.strip() == "hi" and not res.ok
    res = await run_command('python3 -c "import time; time.sleep(5)"', tmp_path, timeout=1)
    assert res.timed_out and not res.ok


@pytest.fixture
def aci(tmp_path: Path) -> ACI:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef sub(a, b):\n    return a - b\n"
    )
    (tmp_path / "tests").mkdir()
    return ACI(
        tmp_path,
        test_command=f"{shlex.quote(sys.executable)} -m pytest -q",
        allowed_paths=["src/", "tests/"],
    )


async def test_aci_read_list_search_symbol(aci: ACI):
    out = (await aci.call("read_file", {"path": "src/calc.py", "lines": 2})).output
    assert (
        out.startswith("src/calc.py [1-2 of 6]")
        and "    1| def add" in out
        and "use start=3" in out
    )
    assert (await aci.call("list_dir", {})).output == "src/\ntests/"
    assert "src/calc.py:5" in (await aci.call("search", {"pattern": "def sub"})).output
    assert "function sub — src/calc.py:5" in (await aci.call("find_symbol", {"name": "sub"})).output
    res = await aci.call("read_file", {"path": "../etc/passwd"})
    assert not res.ok and "outside the repository" in res.output
    assert not (await aci.call("nope", {})).ok


async def test_aci_edits_respect_scope(aci: ACI):
    res = await aci.call(
        "edit_file", {"path": "src/calc.py", "old": "return a - b", "new": "return a - b  # sub"}
    )
    assert res.ok and "+    return a - b  # sub" in res.output
    res = await aci.call("edit_file", {"path": "src/calc.py", "old": "return a", "new": "x"})
    assert not res.ok and "2 times" in res.output
    res = await aci.call("write_file", {"path": "README.md", "content": "x"})
    assert not res.ok and "outside the plan's paths" in res.output
    res = await aci.call(
        "write_file",
        {
            "path": "tests/test_calc.py",
            "content": "from src.calc import add\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        },
    )
    assert res.ok and aci.touched == {"src/calc.py", "tests/test_calc.py"}


async def test_aci_apply_patch(aci: ACI):
    patch = """--- a/src/calc.py
+++ b/src/calc.py
@@ -1,2 +1,3 @@
 def add(a, b):
+    # sum
     return a + b
"""
    res = await aci.call("apply_patch", {"patch": patch})
    assert res.ok and "# sum" in (aci.root / "src" / "calc.py").read_text()
    bad = "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1,1 +1,1 @@\n-nonexistent line\n+x\n"
    res = await aci.call("apply_patch", {"patch": bad})
    assert not res.ok and "does not match" in res.output
    new = "--- /dev/null\n+++ b/src/new.py\n@@ -0,0 +1,2 @@\n+A = 1\n+B = 2\n"
    assert (await aci.call("apply_patch", {"patch": new})).ok and (
        aci.root / "src" / "new.py"
    ).read_text() == "A = 1\nB = 2\n"


async def test_aci_tests_and_signals(aci: ACI):
    (aci.root / "tests" / "test_calc.py").write_text(
        "import sys; sys.path.insert(0, '.')\nfrom src.calc import add\n\ndef test_add():\n    assert add(1, 2) == 4\n"
    )
    out = (await aci.call("run_tests", {})).output
    assert out.startswith("[pytest] FAIL") and "test_add" in out and "assert 3 == 4" in out
    assert (await aci.call("done", {"summary": "ok"})).output == "DONE: ok"
    assert (await aci.call("blocked", {"reason": "r", "options": ["a"]})).output.startswith(
        "BLOCKED:"
    )
    await aci.call("note_learning", {"title": "bug em X", "kind": "bug"})
    assert aci.learnings == [{"title": "bug em X", "detail": "", "kind": "bug"}]
    assert "[lint] no command" in (await aci.call("run_lint", {})).output


def test_pytest_collection_error_is_parsed():
    out = """==================================== ERRORS ====================================
_____________________ ERROR collecting tests/test_calc.py ______________________
ImportError while importing test module '/w/tests/test_calc.py'.
Traceback:
  File "/w/tests/test_calc.py", line 1, in <module>
    from app.calc import add
ModuleNotFoundError: No module named 'app'
=========================== short test summary info ============================
ERROR tests/test_calc.py
!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
=============================== 1 error in 0.05s ===============================
"""
    s = summarize_tests(out, 2)
    assert not s.ok and s.errors == 1
    assert s.failures[0].name == "tests/test_calc.py" and "ImportError" in s.failures[0].message
    assert "====" not in s.compact() and "unrecognised output" not in s.compact()


def test_pytest_quiet_counts_are_read_without_the_banner():
    """`uv run pytest -q` ends in a bare `1 passed in 0.02s`: the Inspector used to record 0
    passed for every factory on the presets' test command (seen live in `contas`)."""
    s = summarize_tests(".\n1 passed in 0.02s\n", 0)
    assert (s.passed, s.failed, s.ok) == (1, 0, True)
    s = summarize_tests(
        ".F.\n___ test_b ___\nE   assert 1 == 2\nFAILED tests/t.py::test_b - assert 1 == 2\n"
        "1 failed, 2 passed, 1 skipped in 0.31s\n",
        1,
    )
    assert (s.passed, s.failed, s.skipped) == (2, 1, 1) and not s.ok


def test_ruff_current_output_names_the_file_and_the_rule():
    """`contas` S-003 was blocked with green tests and one lint error the Worker never saw:
    ruff's current format puts the rule and the location on separate lines, and the summary
    kept only `-    |; Found 1 error.`."""
    out = (
        "I001 [*] Import block is un-sorted or un-formatted\n"
        " --> src/contas/storage.py:1:1\n"
        "  |\n1 | / import sys\n2 | | import os\n  | |_________^\n"
        "F401 `os` imported but unused\n --> tests/test_x.py:3:8\n  |\n"
        "Found 2 errors.\n[*] 1 fixable with the `--fix` option.\n"
    )
    s = summarize_lint(out, 1)
    assert s.issues[:2] == [
        "src/contas/storage.py:1 I001 Import block is un-sorted or un-formatted",
        "tests/test_x.py:3 F401 `os` imported but unused",
    ]
    assert s.failed == 2 and "mechanical fix" in s.issues[2]


async def test_loompas_own_virtualenv_does_not_reach_factory_commands(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("VIRTUAL_ENV", sys.prefix)
    res = await run_command(
        f"\"{sys.executable}\" -c \"import os; print(os.environ.get('VIRTUAL_ENV', '-'))\"",
        tmp_path,
    )
    assert res.stdout.strip() == "-"
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "their-venv"))  # the founder's own: kept
    res = await run_command(
        f"\"{sys.executable}\" -c \"import os; print(os.environ.get('VIRTUAL_ENV', '-'))\"",
        tmp_path,
    )
    assert res.stdout.strip().endswith("their-venv")


async def test_fix_lint_applies_the_linters_own_fixes_inside_the_plan_only(tmp_path: Path):
    """`contas` S-003: eleven hand edits never found the import order ruff wanted."""
    unsorted = "import sys\nimport os\n\nprint(os, sys)\n"
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "a.py").write_text(unsorted)
    (tmp_path / "other.py").write_text(unsorted)
    ruff = f'"{sys.executable}" -m ruff'
    aci = ACI(
        tmp_path,
        lint_command=f"{ruff} check --select I --no-cache .",
        format_command=f"{ruff} format --no-cache .",
        allowed_paths=["app/"],
    )
    out = await aci.call("fix_lint", {})
    assert out.ok and "automatic fixes applied" in out.output
    assert (tmp_path / "app" / "a.py").read_text().startswith("import os\nimport sys\n")
    assert (tmp_path / "other.py").read_text() == unsorted  # outside the plan: untouched
    assert "other.py:1 I001" in out.output  # and still reported, for the Worker to see
    unscopable = ACI(tmp_path, lint_command="npm run lint", allowed_paths=["app/"])
    assert "no automatic-fix command" in (await unscopable.call("fix_lint", {})).output


# ------------------------------------------------------------ quick check (Fase 7, 7.8)


async def test_a_write_reports_its_syntax_error_at_once(tmp_path):
    aci = ACI(tmp_path)
    res = await aci.call("write_file", {"path": "app.py", "content": "def f(:\n    pass\n"})
    assert res.ok and "[quick check]" in res.output and "app.py:1: syntax error" in res.output
    res = await aci.call("write_file", {"path": "cfg.json", "content": "{nope"})
    assert "cfg.json: invalid json" in res.output
    res = await aci.call("write_file", {"path": "ok.py", "content": "x = 1\n"})
    assert "[quick check]" not in res.output


async def test_undefined_names_come_from_the_projects_ruff(tmp_path):
    import shlex
    import sys

    aci = ACI(tmp_path, lint_command=f"{shlex.quote(sys.executable)} -m ruff check .")
    code = "import os\n\n\ndef f():\n    return undefined_thing\n"
    res = await aci.call("write_file", {"path": "m.py", "content": code})
    assert "F821" in res.output and "undefined_thing" in res.output
    assert "F401" not in res.output  # an unused import mid-task is normal, not reported


async def test_commands_never_run_with_colour_forced(tmp_path, monkeypatch):
    """`contas` S-030: `FORCE_COLOR=0` switched Rich's colours ON in Typer's help."""
    monkeypatch.setenv("FORCE_COLOR", "1")
    code = "import os; print(sorted(k for k in ('FORCE_COLOR', 'NO_COLOR') if k in os.environ))"
    res = await run_command(f"{shlex.quote(sys.executable)} -c {shlex.quote(code)}", tmp_path)
    assert res.output.strip() == "['NO_COLOR']"


async def test_aci_apply_patch_takes_openais_begin_patch_format(aci: ACI):
    """contas Sprint 2: glm-5.3-flash wrote its patch as '*** Begin Patch' and it was refused
    as an unknown format, a round lost each time."""
    patch = """*** Begin Patch
*** Update File: src/calc.py
@@ def add(a, b):
 def add(a, b):
-    return a + b
+    return int(a) + int(b)

*** Add File: src/money.py
+CENTS = 100
*** End Patch"""
    res = await aci.call("apply_patch", {"patch": patch})
    assert res.ok, res.output
    assert "int(a) + int(b)" in (aci.root / "src" / "calc.py").read_text()
    assert (aci.root / "src" / "money.py").read_text() == "CENTS = 100\n"
    gone = "*** Begin Patch\n*** Delete File: src/money.py\n*** End Patch"
    res = await aci.call("apply_patch", {"patch": gone})
    assert not res.ok and "delete_file" in res.output


async def test_branch_diff_shows_what_the_story_changed(tmp_path: Path):
    """`contas` S-047: the Worker reformatted a line, could not see what it was before, and
    asked the founder to run git for it."""
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (tmp_path / "app.py").write_text("print('a', 'b')\n")
    (tmp_path / "keep.py").write_text("x = 1\n")
    git("add", ".")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "story")
    (tmp_path / "keep.py").write_text("x = 2\n")
    git("commit", "-q", "-am", "task 1")  # committed in the branch
    (tmp_path / "app.py").write_text("print(\n    'a', 'b'\n)\n")  # uncommitted
    (tmp_path / "new.py").write_text("y = 1\n")  # untracked

    aci = ACI(tmp_path, diff_base="main")
    summary = await aci.call("branch_diff", {})
    assert summary.ok
    assert "app.py" in summary.output and "keep.py" in summary.output
    assert "new files: new.py" in summary.output
    one = await aci.call("branch_diff", {"path": "app.py"})
    assert "-print('a', 'b')" in one.output  # the original form of the reformatted line
    assert "is new in this branch" in (await aci.call("branch_diff", {"path": "new.py"})).output
    nothing = ACI(tmp_path)  # outside a story branch there is no base to compare with
    assert not (await nothing.call("branch_diff", {})).ok
