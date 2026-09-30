"""Diff hygiene (Fase 7, item 7.10): leftovers a test suite never notices, found in code.

In `contas` a `debug.txt` holding paths of the founder's machine travelled from a Worker's task
all the way to the S-005 delivery. Nothing asks a model whether its diff is clean; the diff is
read line by line:

* blocking (the delivery must not carry them): debris files (`*.log`, `*.bak`, `*.orig`,
  `debug.txt`, …), absolute paths of a machine or of a story worktree, debugger statements, and
  merge conflict markers;
* warnings (told to the Worker, noted by the Inspector): a new TODO/FIXME, a debug print or
  `console.log`, and files whose change is whitespace only.

The Worker gets every issue after each task, before its self-check (a follow-up round fixes
them); the Inspector fails a story that still carries a blocking one; the Deployer never commits
an untracked debris file. Texts are English: they are read by the models.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import PurePosixPath

DEBRIS_SUFFIXES = (".log", ".tmp", ".bak", ".orig", ".rej", ".swp", ".swo", ".pyc")
DEBRIS_NAMES = {"nohup.out", ".DS_Store", "Thumbs.db"}
_DEBRIS_STEM = re.compile(
    r"^(debug|scratch|tmp|temp|out|output|test_output)([_.-][\w.-]*)?\.(txt|out|log|json|html|csv)$",
    re.I,
)
_FIXTURE_DIRS = {"fixtures", "testdata", "test_data", "__snapshots__", "snapshots"}
CODE_SUFFIXES = (".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rs", ".rb")

_MACHINE_PATH = re.compile(
    r"(/Users/(?P<mac>[^/\s'\"`]+)/|/home/(?P<linux>[^/\s'\"`]+)/|[A-Za-z]:\\\\?Users\\\\?"
    r"|/private/var/folders/|/var/folders/\w{2}/|\.loompa/worktrees/)"
)
# names that make a path an example, not somebody's machine
_PLACEHOLDER_USERS = {"user", "username", "you", "me", "name", "example", "foo", "<user>", "$user"}
_DEBUGGER_PY = re.compile(r"^\s*(breakpoint\(\)|import i?pdb\b|.*\bi?pdb\.set_trace\()")
_DEBUGGER_JS = re.compile(r"^\s*debugger\s*;?\s*$")
_CONFLICT = re.compile(r"^(<{7} |>{7} )")  # a bare ======= is also a Markdown underline
_TODO = re.compile(r"(#|//|/\*|<!--)\s*(TODO|FIXME|XXX|HACK)\b")
_DEBUG_PRINT = re.compile(r"\bprint\(.*\b(debug|DEBUG|Debug)\b")
_CONSOLE_LOG = re.compile(r"\bconsole\.(log|debug)\(")


@dataclass(frozen=True)
class HygieneIssue:
    path: str
    rule: str
    blocking: bool
    detail: str

    def line(self) -> str:
        return f"{self.path}: {self.detail}"


def is_test_path(path: str) -> bool:
    p = PurePosixPath(path)
    name = p.name
    return (
        any(part in ("tests", "test", "__tests__", "spec") for part in p.parts[:-1])
        or name.startswith("test_")
        or name.endswith(("_test.py", "_test.go"))
        or bool(re.search(r"\.(test|spec)\.[jt]sx?$", name))
        or name == "conftest.py"
    )


def is_debris(path: str) -> bool:
    p = PurePosixPath(path)
    if any(part in _FIXTURE_DIRS for part in p.parts[:-1]):
        return False
    name = p.name
    return (
        name in DEBRIS_NAMES
        or name.endswith(DEBRIS_SUFFIXES)
        or name.endswith("~")
        or bool(_DEBRIS_STEM.match(name))
    )


@dataclass
class _FileDiff:
    path: str
    new: bool = False
    deleted: bool = False
    added: list[str] | None = None
    removed: list[str] | None = None


def _files(diff: str) -> list[_FileDiff]:
    files: list[_FileDiff] = []
    cur: _FileDiff | None = None
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            m = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            cur = _FileDiff(m.group(2) if m else line.split()[-1], added=[], removed=[])
            files.append(cur)
            continue
        if cur is None:
            continue
        if line.startswith("new file mode"):
            cur.new = True
        elif line.startswith("deleted file mode") or line == "+++ /dev/null":
            cur.deleted = True
        elif line.startswith("+++ ") or line.startswith("--- "):
            continue
        elif line.startswith("+"):
            cur.added.append(line[1:])  # type: ignore[union-attr]
        elif line.startswith("-"):
            cur.removed.append(line[1:])  # type: ignore[union-attr]
    return files


def scan_diff(diff: str) -> list[HygieneIssue]:
    """Hygiene issues in a unified diff (`git diff` output); at most one per rule and file."""
    issues: list[HygieneIssue] = []
    for f in _files(diff):
        if f.deleted:
            continue
        found: dict[str, HygieneIssue] = {}

        def add(
            rule: str,
            blocking: bool,
            detail: str,
            f: _FileDiff = f,
            found: dict[str, HygieneIssue] = found,
        ) -> None:
            found.setdefault(rule, HygieneIssue(f.path, rule, blocking, detail))

        if is_debris(f.path):
            add(
                "debris_file",
                True,
                "leftover file (debug output, log, backup or merge residue); delete it",
            )
        code = f.path.endswith(CODE_SUFFIXES)
        test = is_test_path(f.path)
        for text in f.added or []:
            m = _MACHINE_PATH.search(text)
            if m and (m.group("mac") or m.group("linux") or "").lower() not in _PLACEHOLDER_USERS:
                add(
                    "machine_path",
                    not test,  # in a test it may be data; elsewhere it leaks a machine
                    f"absolute path of a machine or worktree: `{text.strip()[:80]}`",
                )
            if _CONFLICT.match(text):
                add("conflict_marker", True, "merge conflict marker left in the file")
            if code and (_DEBUGGER_PY.match(text) or _DEBUGGER_JS.match(text)):
                add("debugger", True, f"debugger statement: `{text.strip()[:80]}`")
            if _TODO.search(text):
                add("todo", False, f"new TODO/FIXME left behind: `{text.strip()[:80]}`")
            if code and not test and _DEBUG_PRINT.search(text):
                add("debug_print", False, f"debug print: `{text.strip()[:80]}`")
            if code and not test and _CONSOLE_LOG.search(text):
                add("debug_print", False, f"console.log left in: `{text.strip()[:80]}`")
        if (
            not f.new
            and f.added
            and f.removed
            and _squash(f.added) == _squash(f.removed)
            and not f.path.endswith((".md", ".txt"))
        ):
            add("whitespace_only", False, "only whitespace changed in this file; revert it")
        issues += found.values()
    return issues


def _squash(lines: list[str]) -> str:
    return "".join("".join(line.split()) for line in lines)


def blocking(issues: list[HygieneIssue]) -> list[HygieneIssue]:
    return [i for i in issues if i.blocking]


def render(issues: list[HygieneIssue], *, limit: int = 8) -> str:
    return "\n".join(f"- {i.line()}" for i in issues[:limit])


# ------------------------------------------------------------------------ test evidence

_TEST_DEF = re.compile(r"^\s*(async\s+)?def\s+test_\w*\(|^\s*(it|test)\(\s*['\"`]")
_ASSERTS = re.compile(r"\bassert\b|\bassert\w*\(|pytest\.raises|\bexpect\(|\.should\b|self\.fail\(")
_TAUTOLOGY = re.compile(
    r"^\s*(assert\s+(True|not\s+False|1\s*==\s*1|(['\"])\w*\3\s*==\s*\3\w*\3)\s*(#.*)?$"
    r"|self\.assertTrue\(\s*True\s*\)|expect\(\s*true\s*\)\.toBe\(\s*true\s*\))"
)


def weak_tests(diff: str) -> list[dict[str, str]]:
    """Inspector findings (TEST-) that need no model: a tautological assertion, new tests that
    assert nothing, and production code changed with no test in the diff (Fase 7, 7.2)."""
    findings: list[dict[str, str]] = []
    files = [f for f in _files(diff) if not f.deleted]
    tests = [f for f in files if is_test_path(f.path) and f.path.endswith(CODE_SUFFIXES)]
    code = [
        f for f in files if f.path.endswith(CODE_SUFFIXES) and not is_test_path(f.path) and f.added
    ]
    for f in tests:
        added = f.added or []
        fake = next((a.strip() for a in added if _TAUTOLOGY.search(a)), None)
        if fake:
            findings.append(
                {
                    "severity": "high",
                    "file": f.path,
                    "text": f"{f.path}: tautological assertion `{fake[:60]}` proves nothing "
                    "about the code",
                }
            )
        elif (
            f.new
            and any(_TEST_DEF.search(a) for a in added)
            and not any(_ASSERTS.search(a) for a in added)
        ):
            findings.append(
                {
                    "severity": "medium",
                    "file": f.path,
                    "text": f"{f.path}: the new tests assert nothing",
                }
            )
    if code and not tests:
        findings.append(
            {
                "severity": "medium",
                "file": code[0].path,
                "text": f"{code[0].path}: production code changed and no test in the diff "
                "exercises it",
            }
        )
    return findings
