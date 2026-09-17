"""Log compaction (SWE-agent ACI paradigm): keep only the surgical error lines.

A 4,000-line pytest run becomes a dozen lines: which tests failed, the assertion message and
the deepest relevant frame. Success noise is discarded entirely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_PYTEST_SUMMARY = re.compile(r"^=+ (.*?) =+$")
_PYTEST_FAIL_HEADER = re.compile(r"^_{3,} (?:ERROR collecting )?(.+?) _{3,}$")
_PYTEST_SHORT = re.compile(r"^(FAILED|ERROR) (\S+)(?: - (.*))?$")
_PYTEST_COUNTS = re.compile(r"(\d+) (passed|failed|error|errors|skipped|xfailed|xpassed|warnings?)")
_PY_FRAME = re.compile(r'^\s*File "(.+?)", line (\d+)')
_PY_LOC = re.compile(r"^(\S+\.py):(\d+):")
_ASSERT = re.compile(
    r"^(E\s+.*|assert .*|AssertionError.*|\w+Error: .*|\w+Exception: .*|ImportError while importing.*)$"
)
_JEST_FAIL = re.compile(r"^\s*(●|✕|FAIL) (.+)$")
_JEST_COUNTS = re.compile(r"Tests:\s+(.*)$")
_VITEST_COUNTS = re.compile(r"Tests\s+(\d+ failed.*|\d+ passed.*)$")
_RUFF_LINE = re.compile(r"^(\S+?):(\d+):(\d+): ([A-Z]+\d+) (.*)$")
_ESLINT_FILE = re.compile(r"^(/\S+|\S+\.(?:ts|tsx|js|jsx))$")
_ESLINT_LINE = re.compile(r"^\s+(\d+):(\d+)\s+(error|warning)\s+(.*?)\s{2,}(\S+)$")
_MYPY_LINE = re.compile(r"^(\S+?):(\d+):(?:\d+:)? (error|note): (.*)$")
_TSC_LINE = re.compile(r"^(\S+?)\((\d+),(\d+)\): error (TS\d+): (.*)$")


@dataclass
class Failure:
    name: str
    location: str = ""
    message: str = ""
    frames: list[str] = field(default_factory=list)


@dataclass
class CommandSummary:
    tool: str
    ok: bool
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    failures: list[Failure] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)  # lint/typecheck issues, already compact
    notes: list[str] = field(default_factory=list)
    truncated: bool = False

    def compact(self, max_failures: int = 8, max_frames: int = 3) -> str:
        """The only text an agent ever sees about this run."""
        head = f"[{self.tool}] {'PASS' if self.ok else 'FAIL'}"
        counts = []
        if self.passed:
            counts.append(f"{self.passed} passed")
        if self.failed:
            counts.append(f"{self.failed} failed")
        if self.errors:
            counts.append(f"{self.errors} errors")
        if self.skipped:
            counts.append(f"{self.skipped} skipped")
        lines = [head + (f" ({', '.join(counts)})" if counts else "")]
        for f in self.failures[:max_failures]:
            lines.append(f"- {f.name}" + (f" @ {f.location}" if f.location else ""))
            if f.message:
                lines.append(f"    {f.message[:300]}")
            for fr in f.frames[-max_frames:]:
                lines.append(f"    {fr}")
        if len(self.failures) > max_failures:
            lines.append(f"… +{len(self.failures) - max_failures} falhas")
        for issue in self.issues[:40]:
            lines.append(f"- {issue}")
        if len(self.issues) > 40:
            lines.append(f"… +{len(self.issues) - 40} apontamentos")
        lines.extend(f"note: {n}" for n in self.notes[:5])
        return "\n".join(lines)


def summarize_tests(output: str, returncode: int, *, cwd_prefix: str = "") -> CommandSummary:
    text = _strip_ansi(output)
    if "Tests:" in text or "●" in text or "✕" in text or "Test Files" in text:
        return _summarize_js(text, returncode)
    if (
        "test session starts" in text
        or "ERROR collecting" in text
        or re.search(r"\d+ (passed|failed|error)", text)
    ):
        return _summarize_pytest(text, returncode)
    summary = CommandSummary(tool="tests", ok=returncode == 0)
    if returncode != 0:
        tail = [line for line in text.splitlines() if line.strip()][-15:]
        summary.failures.append(
            Failure(name="(saída não reconhecida)", message="; ".join(tail)[:600])
        )
    return summary


def _summarize_pytest(text: str, returncode: int) -> CommandSummary:
    s = CommandSummary(tool="pytest", ok=returncode == 0)
    lines = text.splitlines()
    current: Failure | None = None
    in_failures = False
    for line in lines:
        m = _PYTEST_SUMMARY.match(line.strip())
        if m:
            title = m.group(1)
            if "FAILURES" in title or "ERRORS" in title:
                in_failures = True
                continue
            for n, kind in _PYTEST_COUNTS.findall(title):
                n = int(n)
                if kind == "passed":
                    s.passed = n
                elif kind == "failed":
                    s.failed = n
                elif kind.startswith("error"):
                    s.errors = n
                elif kind == "skipped":
                    s.skipped = n
            if "short test summary" in title:
                in_failures = False
            continue
        fh = _PYTEST_FAIL_HEADER.match(line.strip())
        if in_failures and fh:
            current = Failure(name=fh.group(1).strip())
            s.failures.append(current)
            continue
        if current is not None:
            if _PY_LOC.match(line.strip()):
                current.location = line.strip().split(" ", 1)[0].rstrip(":")
            elif _ASSERT.match(line.strip()) and not current.message:
                current.message = line.strip()
            elif line.startswith("E ") and len(current.message) < 400:
                current.message = (current.message + " " + line[2:].strip()).strip()
            fr = _PY_FRAME.match(line)
            if fr:
                current.frames.append(f"{fr.group(1)}:{fr.group(2)}")
        sm = _PYTEST_SHORT.match(line.strip())
        if sm and not any(f.name.endswith(sm.group(2).split("::")[-1]) for f in s.failures):
            s.failures.append(Failure(name=sm.group(2), message=(sm.group(3) or "")[:300]))
    if returncode != 0 and not s.failures:
        tail = [line for line in lines if line.strip()][-10:]
        s.failures.append(
            Failure(name="(falha sem teste identificado)", message="; ".join(tail)[:600])
        )
    if s.failed == 0 and s.errors == 0 and returncode != 0 and s.failures:
        s.failed = len(s.failures)
    return s


def _summarize_js(text: str, returncode: int) -> CommandSummary:
    s = CommandSummary(tool="jest/vitest", ok=returncode == 0)
    current: Failure | None = None
    current_file = ""
    for line in text.splitlines():
        m = _JEST_FAIL.match(line)
        if m and m.group(1) == "FAIL":
            current_file = m.group(2).strip()
            continue
        if m and m.group(1) in ("●", "✕"):
            name = m.group(2).strip()
            if name.startswith("Test suite failed"):
                continue
            current = Failure(name=name, location=current_file)
            s.failures.append(current)
            continue
        if current is not None:
            st = line.strip()
            if (
                st.startswith(
                    (
                        "Expected",
                        "Received",
                        "expected",
                        "AssertionError",
                        "Error:",
                        "TypeError",
                        "ReferenceError",
                    )
                )
                and len(current.message) < 400
            ):
                current.message = (current.message + " " + st).strip()
            elif st.startswith("at ") and "node_modules" not in st and len(current.frames) < 5:
                current.frames.append(st[3:])
        c = _JEST_COUNTS.search(line) or _VITEST_COUNTS.search(line)
        if c:
            for n, kind in re.findall(r"(\d+) (passed|failed|skipped|todo)", c.group(1)):
                setattr(s, kind if kind != "todo" else "skipped", int(n))
    if returncode != 0 and not s.failures:
        s.failures.append(
            Failure(
                name="(falha sem teste identificado)",
                message="; ".join(text.splitlines()[-8:])[:600],
            )
        )
    return s


def summarize_lint(output: str, returncode: int) -> CommandSummary:
    text = _strip_ansi(output)
    s = CommandSummary(tool="lint", ok=returncode == 0)
    current_file = ""
    for line in text.splitlines():
        m = _RUFF_LINE.match(line.strip())
        if m:
            s.issues.append(f"{m.group(1)}:{m.group(2)} {m.group(4)} {m.group(5)}")
            continue
        if _ESLINT_FILE.match(line.strip()) and not line.startswith(" "):
            current_file = line.strip()
            continue
        e = _ESLINT_LINE.match(line)
        if e:
            s.issues.append(f"{current_file}:{e.group(1)} {e.group(5)} {e.group(4)}")
    if returncode != 0 and not s.issues:
        s.issues.append("; ".join(line for line in text.splitlines()[-6:] if line.strip())[:500])
    s.failed = len(s.issues)
    return s


def summarize_typecheck(output: str, returncode: int) -> CommandSummary:
    text = _strip_ansi(output)
    s = CommandSummary(tool="typecheck", ok=returncode == 0)
    for line in text.splitlines():
        m = _MYPY_LINE.match(line.strip())
        if m and m.group(3) == "error":
            s.issues.append(f"{m.group(1)}:{m.group(2)} {m.group(4)}")
            continue
        t = _TSC_LINE.match(line.strip())
        if t:
            s.issues.append(f"{t.group(1)}:{t.group(2)} {t.group(4)} {t.group(5)}")
    if returncode != 0 and not s.issues:
        s.issues.append("; ".join(line for line in text.splitlines()[-6:] if line.strip())[:500])
    s.failed = len(s.issues)
    return s


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text or "")
