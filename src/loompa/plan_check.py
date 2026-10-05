"""Code-side gate over a plan before any token is spent reviewing it (ADR-0026).

Four things a plan can carry that the repository already knows are wrong, each one cheap to
measure and expensive to find later: an acceptance criterion the traceability skips or plans no
test for, a task that names no file at all (the plan prompt asks every task to name the files it
touches), a task that asks to *change* a file that does not exist — the plan was written
against a repository that is not this one — and a task that appends new tests to a test file that
is already long (ADR-0030). `test` is the only place any of it surfaces today,
after the whole implementation is written (`inspector._judge`), so the gate runs between `plan`
and `dev` instead.

What is deliberately not checked: a fence path that does not exist yet. A new module in a new
folder is how a feature normally starts, and the Worker creates it; only a task that says it is
changing something already there can be measured against the tree.

The same reading says whether the plan is bigger than the label intake guessed: a STANDARD that
comes back with many tasks across many files is a COMPLEX whose classification happened before
anyone had looked at the code. The plan is the better evidence, so `signals` lets the graph
promote the story the way `node_plan` already lifts autonomy from the plan's own paths.

Problems are model-facing (they travel into the Architect's hand-off and the Product Owner's
prompt) and name paths, so they stay in English and never reach founder-facing text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from loompa.agents.architect import FILE_REF, LEAVE_ALONE, PATH_REF
from loompa.hygiene import is_test_path
from loompa.speckit import NO_TEST_MARK, REVIEW_MARK

_TRACE_LINE = re.compile(r"^\s*[-*]\s*Crit[ée]rio\s+(\d+)\s*→\s*(.*)$", re.M)

# A plan bigger than the label it was given. The plan prompt allows 2-8 tasks, so only the top of
# that range counts, and directories in the fence count as one path each.
MAX_TASKS = 6
MAX_FILES = 8


@dataclass(frozen=True)
class PlanReport:
    problems: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def _product(paths: list[str]) -> list[str]:
    return [p for p in paths if not p.startswith(".loompa/")]


_DOC_SUFFIXES = (".md", ".txt", ".rst")


def touches_no_code(files: list[str]) -> bool:
    """Every path the story may edit is a test or a document: what it changes is never run as
    the product (a comment in a test file, a README)."""
    product = _product(files)
    return bool(product) and all(
        is_test_path(p) or is_test_path(f"{p.rstrip('/')}/x") or p.endswith(_DOC_SUFFIXES)
        for p in product
    )


def criteria_without_test(
    plan_md: str, criteria: list[str], files: list[str] | None = None
) -> list[str]:
    """Traceability holes, read back from the plan the Architect wrote (7.1): a criterion marked
    without a planned test, and a criterion traceability skipped altogether. A criterion traced
    to the diff review (`review: …`) counts only when the story changes no product code:
    tamagotchi S-051 fixed four comments and, held to a test per criterion, added 185 lines of
    tests reading a test file as text (plan 12.1)."""
    if not criteria:
        return []
    seen: dict[int, str] = {int(n): body for n, body in _TRACE_LINE.findall(plan_md)}
    reviewable = touches_no_code(files or [])
    out = []
    for n in range(1, len(criteria) + 1):
        body = seen.get(n)
        if body is None:
            out.append(f"acceptance criterion {n} is missing from the plan's traceability")
        elif NO_TEST_MARK in body or not body.strip():
            out.append(f"acceptance criterion {n} has no test planned for it")
        elif body.strip().lower().startswith(REVIEW_MARK) and not reviewable:
            out.append(
                f"acceptance criterion {n} is traced to the review, but the story changes "
                "product code: plan a test for it"
            )
    return out


def tasks_without_file(tasks: list[str]) -> list[str]:
    """The plan prompt: "the task names the exact files it touches". A task that names none is
    either a process step the engine already does or a task nobody can bound."""
    return [
        f"task {i}: names no file ({t[:80]})"
        for i, t in enumerate(tasks, 1)
        if not FILE_REF.search(t)
    ]


# Asking to change something that is already there, as opposed to creating it (`_CHANGE` in
# `architect` covers both; here only the first half can be measured against the tree).
_EDIT = re.compile(
    r"\b(?:ajust|alter|mud[ae]|mudar|troc|remov|exclu(?:a|ir)\b|apag|atualiz|edit|corrij|corrig"
    r"|substitu|renome|refator|mov(?:a|er|endo)\b|chang|updat|modif|fix|delet|renam|refactor"
    r"|replac|move|extend)",
    re.I,
)


def edits_of_missing_files(root: Path, tasks: list[str]) -> list[str]:
    """A task that says it changes a file the repository does not have. The plan was written
    against a tree that is not this one, and the Worker would spend its run looking for it
    (`contas` S-049 shape). A task that *creates* a file is not this: only clauses that ask to
    change something already there count, and never one that says to leave a file alone."""
    out = []
    for i, task in enumerate(tasks, 1):
        for clause in re.split(r"(?<=[.;:])\s+|\n", task):
            if not _EDIT.search(clause) or LEAVE_ALONE.search(clause):
                continue
            for rel in PATH_REF.findall(clause):
                if rel.startswith(".loompa/") or (root / rel).exists():
                    continue
                out.append(f"task {i} changes {rel}, which is not in the repository")
    return list(dict.fromkeys(out))


# A test file this long takes a new story's tests in a file of the story's own (`casca.test.js` and
# `sprites.test.js` in tamagotchi were 1,500-2,100 lines when four stories appended to each).
LONG_TEST_FILE = 400
_ADD = re.compile(
    r"\b(?:adicion|acrescent|cri(?:e|ar|ando)\b|escrev|inclu(?:a|ir)\b|complet|cobr(?:ir|a)\b"
    r"|add|writ|creat|append|cover)",
    re.I,
)


def tests_appended_to_long_files(root: Path, tasks: list[str]) -> list[str]:
    """A task that adds new tests to a test file that is already long. Every story appending to
    the same file made parallel stories conflict at each merge (tamagotchi SP-008), and the
    Worker re-reads the file whole on every task. Changing tests the file already has is fine:
    only a clause that adds and does not also say it changes something counts."""
    out = []
    for i, task in enumerate(tasks, 1):
        for clause in re.split(r"(?<=[.;:])\s+|\n", task):
            if not _ADD.search(clause) or _EDIT.search(clause) or LEAVE_ALONE.search(clause):
                continue
            for rel in PATH_REF.findall(clause):
                path = root / rel
                if not is_test_path(rel) or not path.is_file():
                    continue
                lines = path.read_text(encoding="utf-8", errors="ignore").count("\n")
                if lines > LONG_TEST_FILE:
                    out.append(
                        f"task {i} adds tests to {rel}, already {lines} lines long: put this "
                        "story's new tests in a new test file of its own, next to it and named "
                        "so the test runner finds it"
                    )
    return list(dict.fromkeys(out))


def size_signals(tasks: list[str], files: list[str]) -> list[str]:
    product = _product(files)
    out = []
    if len(tasks) > MAX_TASKS:
        out.append(f"{len(tasks)} tasks (over {MAX_TASKS})")
    if len(product) > MAX_FILES:
        out.append(f"{len(product)} paths in the fence (over {MAX_FILES})")
    return out


def check_plan(
    root: Path, *, acceptance: list[str], files: list[str], tasks: list[str], plan_md: str
) -> PlanReport:
    return PlanReport(
        problems=[
            *criteria_without_test(plan_md, acceptance, files),
            *tasks_without_file(tasks),
            *edits_of_missing_files(root, tasks),
            *tests_appended_to_long_files(root, tasks),
        ],
        signals=size_signals(tasks, files),
    )
