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


# A criterion about a record (a decision, the spec) rather than about what the product does: its
# proof is the record itself, read in the review, never a test that reads it (ADR-0036 §5).
_RECORD_CRITERION = re.compile(
    r"\b(?:ADR|decis[ãa]o|decis[õo]es|decision|registr\w*|record\w*)\b", re.I
)


def criteria_without_test(
    plan_md: str, criteria: list[str], files: list[str] | None = None
) -> list[str]:
    """Traceability holes, read back from the plan the Architect wrote (7.1): a criterion marked
    without a planned test, and a criterion traceability skipped altogether. A criterion traced
    to the diff review (`review: …`) counts only when the story changes no product code:
    tamagotchi S-051 fixed four comments and, held to a test per criterion, added 185 lines of
    tests reading a test file as text (plan 12.1). Or when the criterion is about a record: S-077
    held two "the decision records …" criteria to tests that read the ADRs, which the merge then
    left out of the base, and the base went red (ADR-0036 §5)."""
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
        elif (
            body.strip().lower().startswith(REVIEW_MARK)
            and not reviewable
            and not _RECORD_CRITERION.search(criteria[n - 1])
        ):
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
            # "create tests/novo.test.js and adjust tests/old.test.js": the new file is created
            # (12.1, S-061: flagged as "changes a missing file", the Worker made an empty stub)
            if _ADD.search(clause):
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
    r"\b(?:adicion|acrescent|cri(?:a|e|ar|ando)\b|grav(?:a|e|ar)\b|salv(?:a|e|ar)\b"
    r"|ger(?:a|e|ar)\b|escrev|inclu(?:a|ir)\b|complet|cobr(?:ir|a)\b"
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


_FACTORY_PATH = re.compile(r"(?<![\w/.-])\.loompa/[\w./-]*")
# a product path named after a story: the factory's records copied into the product
# (tamagotchi: specs/S-068/tasks.md, specs/S-070/tasks.md, five of them, a Worker task each)
_STORY_SEGMENT = re.compile(r"(?:^|/)S-\d+[A-Z]?(?:/|$)")
_RECORD_NAMES = ("spec.md", "plan.md", "tasks.md")
# A clause that names a record without using it: the read being removed ("remover o it que lia
# .loompa/specs/S-077/plan.md"), the copy that leaves it out ("cópia sem .loompa/", "excluindo
# entradas ocultas"), the bug as it is today ("hoje falha com ENOENT em .loompa/…") or a check
# that nothing touches it ("nenhum arquivo de tests/ lê .loompa/"). tamagotchi S-101 and S-106,
# whose whole goal was to take a records read out of a test: every honest task named the path,
# the gate withdrew them all, S-101 closed as "resolved" over a red base and S-106 asked the
# founder three times. The diff scan (`hygiene.scan_diff`, `reads_records`) still refuses a test
# that opens a record, so a miss here costs a hand-off later, never a red base.
_NOT_USED = re.compile(
    r"\b(?:remov\w*|apag\w*|elimin\w*|exclu\w*|retir\w*|tir(?:a|e|ar|ando)|delet\w*|strip\w*"
    r"|drop\w*|ausen\w*|inexist\w*|enoent|missing|absent|hoje|today|currently|falh\w*|fail\w*"
    r"|lan[çc]a|throws|lia|liam|used to read|nenhum\w*|nada|zero|none|nothing|no longer|pul(?:a|ando))\b",
    re.I,
)
_NONE = re.compile(r"\b(?:nenhum\w*|nada|none|nothing|no longer)\b", re.I)


def factory_records_in_tasks(tasks: list[str]) -> list[str]:
    """Tasks that write the factory's own records, or test against them (ADR-0036 §5). The
    factory writes and commits a story's spec, plan, checklist and decisions itself, and leaves
    `.loompa/` out of every story's branch at merge: a task that writes there is lost, a test that
    reads it fails on the base (tamagotchi S-077: two ADRs written by tasks, two tests reading
    them, a red base), and a copy of the records in the product is work nobody uses."""
    out = []
    for i, task in enumerate(tasks, 1):
        names_test = any(is_test_path(p) for p in PATH_REF.findall(task))
        refs: list[str] = []
        for clause in re.split(r"(?<=[.;])\s+|\n", task):
            if LEAVE_ALONE.search(clause):
                continue
            if not _NOT_USED.search(clause):
                refs.extend(_FACTORY_PATH.findall(clause))
            if _NONE.search(clause):
                continue
            writes = _ADD.search(clause) or _EDIT.search(clause)
            # "create tests/x.test.js covering what .loompa/… says": the test is written, the
            # record is read (that is the other problem, below)
            makes_test = any(is_test_path(p) for p in PATH_REF.findall(clause))
            for rel in _FACTORY_PATH.findall(clause):
                if writes and not makes_test:
                    out.append(
                        f"task {i} writes {rel}: the factory keeps its records under .loompa/ "
                        "itself and leaves them out of the story's branch at merge, so nothing "
                        "the product or its tests use can live there; put a decision in the "
                        "plan's `adr_proposal`"
                    )
            for rel in PATH_REF.findall(clause):
                if rel.startswith(".loompa/") or not writes:
                    continue
                if _STORY_SEGMENT.search(rel) or (rel.endswith(_RECORD_NAMES) and "specs/" in rel):
                    out.append(
                        f"task {i} writes {rel}, a copy of the factory's own records in the "
                        "product: the spec, plan and checklist live under .loompa/specs and are "
                        "committed with the merge; drop the task"
                    )
        if refs and names_test:
            out.append(
                f"task {i} has a test read {refs[0]}: the factory's records are not part of "
                "the product and never reach the base with the story; test what the product "
                "does, and trace a criterion about a record to `review:`"
            )
    return list(dict.fromkeys(out))


# How `factory_records_in_tasks` words a task that writes or tests the factory's records.
_RECORD_PROBLEM = re.compile(
    r"^task (\d+) (?:writes \.loompa/|writes \S+, a copy of the factory's own records"
    r"|has a test read \.loompa/)"
)
# The records a story's criteria have no business asking for: the factory's decisions, specs,
# lessons and constitution. Not every `.loompa/` path: "the server never serves
# .loompa/config.yaml" is a product criterion.
_RECORD_PATH = re.compile(
    r"(?<![\w/.-])\.loompa/(?:(?:decisions|specs)/[\w./-]*|learnings\.md|constitution\.md)"
)


def record_tasks(problems: list[str]) -> list[int]:
    """The task numbers the gate refused for writing or testing the factory's records."""
    return sorted({int(m.group(1)) for p in problems if (m := _RECORD_PROBLEM.match(p))})


def records_in_criteria(criteria: list[str]) -> list[tuple[int, str]]:
    """(number, path) of each acceptance criterion that names one of the factory's records
    (ADR-0037 §5). tamagotchi S-094: ten criteria asked to create two documents under
    `.loompa/decisions/` and to change no test, the one fix the factory can never deliver."""
    out = []
    for i, text in enumerate(criteria, 1):
        found = _RECORD_PATH.findall(text or "")
        if found:
            out.append((i, found[0]))
    return out


def size_signals(tasks: list[str], files: list[str]) -> list[str]:
    """Only product paths count: tamagotchi S-061 (12.1) changed three source files, and the six
    old tests founder had let it adjust (pixel-exact ones) made it "9 paths", COMPLEX, and a
    pre-flight that added two more test files."""
    product = [
        p for p in _product(files) if not is_test_path(p) and not is_test_path(f"{p.rstrip('/')}/x")
    ]
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
            *factory_records_in_tasks(tasks),
        ],
        signals=size_signals(tasks, files),
    )
