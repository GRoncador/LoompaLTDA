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
    return ACI(tmp_path, test_command="python3 -m pytest -q", allowed_paths=["src/", "tests/"])


async def test_aci_read_list_search_symbol(aci: ACI):
    out = (await aci.call("read_file", {"path": "src/calc.py", "lines": 2})).output
    assert (
        out.startswith("src/calc.py [1-2 de 6]")
        and "    1| def add" in out
        and "use start=3" in out
    )
    assert (await aci.call("list_dir", {})).output == "src/\ntests/"
    assert "src/calc.py:5" in (await aci.call("search", {"pattern": "def sub"})).output
    assert "function sub — src/calc.py:5" in (await aci.call("find_symbol", {"name": "sub"})).output
    res = await aci.call("read_file", {"path": "../etc/passwd"})
    assert not res.ok and "fora do repositório" in res.output
    assert not (await aci.call("nope", {})).ok


async def test_aci_edits_respect_scope(aci: ACI):
    res = await aci.call(
        "edit_file", {"path": "src/calc.py", "old": "return a - b", "new": "return a - b  # sub"}
    )
    assert res.ok and "+    return a - b  # sub" in res.output
    res = await aci.call("edit_file", {"path": "src/calc.py", "old": "return a", "new": "x"})
    assert not res.ok and "2 vezes" in res.output
    res = await aci.call("write_file", {"path": "README.md", "content": "x"})
    assert not res.ok and "fora do escopo" in res.output
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
    assert not res.ok and "não casa" in res.output
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
    assert "[lint] nenhum comando" in (await aci.call("run_lint", {})).output


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
    assert "====" not in s.compact() and "saída não reconhecida" not in s.compact()
