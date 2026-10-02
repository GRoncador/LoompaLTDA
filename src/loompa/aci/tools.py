"""Agent-Computer Interface: the only tools a Worker/Inspector Loompa gets.

No raw bash. Reads are paginated, edits are structured (exact replace / unified diff),
searches are exact, and command output is compacted before it reaches the model.
"""

from __future__ import annotations

import difflib
import inspect
import json
import re
import shlex
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loompa.aci.filters import summarize_lint, summarize_tests, summarize_typecheck
from loompa.aci.runner import run_command
from loompa.hygiene import RESIDUE_NOTE, new_files, run_residue
from loompa.memory.lexical import CodeSearch, SymbolIndex


class ToolError(Exception):
    pass


_SAFE_ENV_FILES = {".env.example", ".env.sample", ".env.template"}
_KEY_FILE_SUFFIXES = (".pem", ".key", ".p12", ".pfx")
_KEY_FILE_NAMES = {"secrets.env", "id_rsa", "id_ed25519", "id_ecdsa"}


def is_protected(rel: str | Path) -> bool:
    """Files no agent tool may read or write: credentials, git internals and the factory's own
    state. A model that browses the web must not be able to be talked into reading a key."""
    parts = Path(rel).parts
    if not parts:
        return False
    name = parts[-1]
    if ".git" in parts:
        return True
    if name == ".env" or name.endswith(".env"):  # .env, prod.env, secrets.env
        return True
    if name.startswith(".env.") and name not in _SAFE_ENV_FILES:
        return True
    if name in _KEY_FILE_NAMES or name.endswith(_KEY_FILE_SUFFIXES):
        return True
    return parts[0] == ".loompa" and (
        name.endswith((".db", ".db-wal", ".db-shm")) or "logs" in parts or "worktrees" in parts
    )


@dataclass
class ToolResult:
    ok: bool
    output: str

    def __str__(self) -> str:
        return self.output


TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read a file by line range (at most 200 lines per call). Line numbers are shown; continue with `start`.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "start": {"type": "integer", "default": 1},
                "lines": {"type": "integer", "default": 120},
            },
            "required": ["path"],
        },
    },
    {
        "name": "list_dir",
        "description": "List files and folders in one directory (not recursive; skips node_modules, .venv, build output).",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string", "default": "."}},
        },
    },
    {
        "name": "search",
        "description": "Search the code with a regular expression; returns file:line and the matching line. `glob` narrows the files (e.g. `*.py`).",
        "parameters": {
            "type": "object",
            "properties": {"pattern": {"type": "string"}, "glob": {"type": "string"}},
            "required": ["pattern"],
        },
    },
    {
        "name": "find_symbol",
        "description": "Find where a function, class or symbol is defined (from the syntax tree), with its signature.",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    },
    {
        "name": "write_file",
        "description": "Create a file, or replace a whole file. Use it for new or small files; prefer edit_file for changes to an existing one.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "Replace one exact snippet (`old`) with `new` in a file. `old` must appear exactly once: copy it from read_file, with enough context to be unique.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string"},
                "old": {"type": "string"},
                "new": {"type": "string"},
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "delete_file",
        "description": "Delete one file (leftovers, a file moved in a refactor). Does not delete folders.",
        "parameters": {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    },
    {
        "name": "apply_patch",
        "description": "Apply a unified diff (`git diff` format) to one or more files. Context lines must match the current file.",
        "parameters": {
            "type": "object",
            "properties": {"patch": {"type": "string"}},
            "required": ["patch"],
        },
    },
    {
        "name": "run_tests",
        "description": "Run the project's test suite; returns the counts and only the relevant failures.",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "optional extra argument, e.g. tests/test_x.py to run one file",
                }
            },
        },
    },
    {
        "name": "run_lint",
        "description": "Run the configured linter and type checker; returns only their findings.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "fix_lint",
        "description": "Apply the linter's automatic fixes and the formatter, only inside the paths the plan allows (import order, spacing, formatting), and return what is left.",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "done",
        "description": "Signal that the current task is complete and its checks are green, with a one-sentence summary.",
        "parameters": {
            "type": "object",
            "properties": {"summary": {"type": "string"}},
            "required": ["summary"],
        },
    },
    {
        "name": "blocked",
        "description": "Signal that you cannot continue without a human decision. `reason` is plain, non-technical language for the founder; `options` are 2-3 choices.",
        "parameters": {
            "type": "object",
            "properties": {
                "reason": {"type": "string"},
                "options": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["reason"],
        },
    },
    {
        "name": "note_learning",
        "description": "Record a side bug, technical debt or opportunity outside this task's scope, for the backlog. Do not fix it now.",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "detail": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["bug", "tech_debt", "opportunity", "architecture"],
                },
            },
            "required": ["title"],
        },
    },
]


class ACI:
    def __init__(
        self,
        root: Path,
        *,
        test_command: str = "",
        lint_command: str = "",
        typecheck_command: str = "",
        format_command: str = "",
        allowed_paths: list[str] | None = None,
        max_read_lines: int = 200,
    ):
        self.root = Path(root).resolve()
        self.test_command = test_command
        self.lint_command = lint_command
        self.format_command = format_command
        self.typecheck_command = typecheck_command
        self.allowed_paths = allowed_paths
        self.max_read_lines = max_read_lines
        self._search = CodeSearch(self.root)
        self._symbols: SymbolIndex | None = None
        self.learnings: list[dict[str, str]] = []
        self.touched: set[str] = set()
        # Bumped by every change to the tree: a read or a test run repeated at the same version
        # would return the same thing (`agents/loopguard.py` answers it from memory).
        self.version = 0
        # Read before write (Fase 7, 7.11): with `require_read`, an existing file can only be
        # edited or overwritten once this task has read it. What the model "remembers" of a
        # file from an earlier task or a pruned result is a guess, and so is an edit built on it.
        self.require_read = False
        self._known: set[str] = set()  # files read or written since `begin_task`
        self._just_written: list[str] = []  # by the call being run (for `quick_check`)
        # files a `run_tests` call created in the repository (a test writing to the working
        # directory, `contas` S-007): the Worker is told at once and they are never committed
        self.test_residue: set[str] = set()

    # ------------------------------------------------------------------ helpers
    def _resolve(self, path: str, *, for_write: bool = False) -> Path:
        p = (self.root / path).resolve()
        if self.root not in (p, *p.parents):
            raise ToolError(f"path outside the repository: {path}")
        if is_protected(p.relative_to(self.root)):
            raise ToolError(f"protected file (credentials or factory state): {path}")
        if for_write and self.allowed_paths is not None:
            rel = str(p.relative_to(self.root))
            if not any(
                rel == a or rel.startswith(a.rstrip("/") + "/") or Path(rel).match(a)
                for a in self.allowed_paths
            ):
                raise ToolError(
                    f"outside the plan's paths: {rel}. If the task needs it, record why with note_learning."
                )
        return p

    def spec(self) -> list[dict[str, Any]]:
        return TOOL_SPECS

    def begin_task(self) -> None:
        """A new task is a new conversation: nothing read before it is in the model's context."""
        self._known.clear()

    def _rel(self, p: Path) -> str:
        return str(p.relative_to(self.root))

    def _check_read(self, p: Path, path: str) -> None:
        if self.require_read and p.is_file() and self._rel(p) not in self._known:
            raise ToolError(
                f"read {path} with read_file before changing it: its current content may not be "
                "what you expect. Read the tests that cover it too."
            )

    def _wrote(self, p: Path) -> None:
        rel = self._rel(p)
        self._just_written.append(rel)
        self.touched.add(rel)
        self._known.add(rel)
        self._symbols = None
        self.version += 1

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return ToolResult(False, f"unknown tool: {name}")
        self._just_written = []
        try:
            out = handler(**args)
            if inspect.isawaitable(out):
                out = await out
            if self._just_written and name in QUICK_CHECKED:
                problems = await self.quick_check(self._just_written)
                if problems:
                    out = f"{out}\n\n[quick check] problems in what you just wrote:\n{problems}"
            return ToolResult(True, out)
        except ToolError as exc:
            return ToolResult(False, f"error: {exc}")
        except TypeError as exc:
            return ToolResult(False, f"invalid arguments for {name}: {exc}")

    # -------------------------------------------------------------------- tools
    def tool_read_file(self, path: str, start: int = 1, lines: int = 120) -> str:
        p = self._resolve(path)
        if not p.is_file():
            raise ToolError(f"no such file: {path}")
        lines = max(1, min(int(lines), self.max_read_lines))
        start = max(1, int(start))
        content = p.read_text(encoding="utf-8", errors="replace").splitlines()
        self._known.add(self._rel(p))
        total = len(content)
        chunk = content[start - 1 : start - 1 + lines]
        body = "\n".join(f"{start + i:5d}| {line}" for i, line in enumerate(chunk))
        end = start + len(chunk) - 1
        more = f"\n… ({total - end} more lines; use start={end + 1})" if end < total else ""
        return f"{path} [{start}-{end} of {total}]\n{body}{more}"

    def tool_list_dir(self, path: str = ".") -> str:
        p = self._resolve(path)
        if not p.is_dir():
            raise ToolError(f"no such folder: {path}")
        skip = {"node_modules", ".venv", "__pycache__", ".git", ".loompa", "dist", "build"}
        entries = sorted(e for e in p.iterdir() if e.name not in skip)
        return "\n".join(f"{e.name}/" if e.is_dir() else e.name for e in entries[:300]) or "(empty)"

    def tool_search(self, pattern: str, glob: str | None = None) -> str:
        hits = [h for h in self._search.grep(pattern, glob=glob) if not is_protected(h.path)]
        if not hits:
            return "no matches"
        return "\n".join(f"{h.path}:{h.line}: {h.text}" for h in hits[:60])

    def tool_find_symbol(self, name: str) -> str:
        if self._symbols is None:
            self._symbols = SymbolIndex.build(self.root)
        syms = self._symbols.find(name) or self._symbols.find(name, exact=False)
        if not syms:
            return f"symbol not found: {name}"
        return "\n".join(
            f"{s.kind} {s.name} — {s.path}:{s.line}\n    {s.signature}" for s in syms[:20]
        )

    def tool_write_file(self, path: str, content: str) -> str:
        p = self._resolve(path, for_write=True)
        self._check_read(p, path)
        p.parent.mkdir(parents=True, exist_ok=True)
        existed = p.exists()
        p.write_text(content, encoding="utf-8")
        self._wrote(p)
        return (
            f"{'overwritten' if existed else 'created'}: {path} ({len(content.splitlines())} lines)"
        )

    def tool_delete_file(self, path: str) -> str:
        p = self._resolve(path, for_write=True)
        if p.is_dir():
            raise ToolError(f"{path} is a folder; delete its files one by one")
        if not p.is_file():
            raise ToolError(f"no such file: {path}")
        p.unlink()
        self._wrote(p)
        return f"deleted: {path}"

    def tool_edit_file(self, path: str, old: str, new: str) -> str:
        p = self._resolve(path, for_write=True)
        if not p.is_file():
            raise ToolError(f"no such file: {path}")
        self._check_read(p, path)
        text = p.read_text(encoding="utf-8")
        count = text.count(old)
        if count == 0:
            raise ToolError("`old` not found; read the file and copy the exact snippet")
        if count > 1:
            raise ToolError(f"`old` appears {count} times; include more context so it is unique")
        updated = text.replace(old, new, 1)
        p.write_text(updated, encoding="utf-8")
        self._wrote(p)
        diff = difflib.unified_diff(text.splitlines(), updated.splitlines(), lineterm="", n=1)
        return "\n".join(list(diff)[2:20])

    def tool_apply_patch(self, patch: str) -> str:
        # models trained on OpenAI's apply_patch write its "*** Begin Patch" format (contas
        # Sprint 2: refused as "unknown format", a round lost each time); both are accepted
        v4a = patch.lstrip().startswith("*** Begin Patch")
        files = _parse_v4a_patch(patch) if v4a else _parse_unified_diff(patch)
        if not files:
            raise ToolError("empty patch or unknown format (use a unified diff)")
        applied = []
        for rel in files:
            self._check_read(self._resolve(rel, for_write=True), rel)
        for rel, hunks in files.items():
            p = self._resolve(rel, for_write=True)
            original = p.read_text(encoding="utf-8").splitlines() if p.is_file() else []
            updated = _apply_hunks(original, hunks)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("\n".join(updated) + ("\n" if updated else ""), encoding="utf-8")
            self._wrote(p)
            applied.append(rel)
        return "patch applied to: " + ", ".join(applied)

    async def tool_run_tests(self, selector: str | None = None) -> str:
        from loompa.onboarding.greenfield import test_command_for

        # none configured (a `custom` greenfield): the tests this checkout has decide, instead of
        # a Worker asking the founder to edit the config (tamagotchi-retro S-006, twice)
        command = test_command_for(self.test_command, self.root)
        if not command:
            return "[tests] no test command configured (quality.test_command) and no tests found"
        cmd = f"{command} {selector}".strip() if selector else command
        before = new_files(self.root)
        res = await run_command(cmd, self.root, timeout=900)
        created = [p for p in run_residue(before, new_files(self.root)) if p not in self.touched]
        self.test_residue.update(created)
        note = ("\n\n" + RESIDUE_NOTE.format(files=", ".join(created[:5]))) if created else ""
        if res.timed_out:
            return "[tests] FAIL: timed out (900s)" + note
        return summarize_tests(res.output, res.returncode).compact() + note

    async def tool_run_lint(self) -> str:
        parts = []
        if self.lint_command:
            res = await run_command(self.lint_command, self.root, timeout=300)
            parts.append(summarize_lint(res.output, res.returncode).compact())
        if self.typecheck_command:
            res = await run_command(self.typecheck_command, self.root, timeout=600)
            parts.append(summarize_typecheck(res.output, res.returncode).compact())
        return "\n".join(parts) or "[lint] no command configured"

    async def tool_fix_lint(self) -> str:
        """Mechanical fixes a model gets wrong by hand: in `contas` S-003 the Worker made eleven
        edits and never found the import order ruff wanted. Only commands whose target is `.`
        can be narrowed to the plan's paths; the others are skipped rather than run repo-wide."""
        targets = (
            ["."]
            if self.allowed_paths is None
            else [p for p in self.allowed_paths if (self.root / p).exists() and not is_protected(p)]
        )
        if not targets:
            return "[fix] none of the plan's paths exists yet"
        commands = []
        if "ruff check" in self.lint_command:
            commands.append(self.lint_command + " --fix")
        if self.format_command:
            commands.append(self.format_command)
        ran, skipped = [], []
        for cmd in commands:
            parts = shlex.split(cmd)
            if "." not in parts:
                skipped.append(cmd)
                continue
            i = len(parts) - 1 - parts[::-1].index(".")
            scoped = parts[:i] + targets + parts[i + 1 :]
            await run_command(shlex.join(scoped), self.root, timeout=300)
            ran.append(parts[-1] if parts else cmd)
        self._symbols = None
        self.version += 1
        head = (
            "[fix] automatic fixes applied" if ran else "[fix] no automatic-fix command configured"
        )
        if skipped:
            head += f" (cannot be narrowed to the plan: {', '.join(skipped)})"
        return head + "\n" + await self.tool_run_lint()

    async def quick_check(self, paths: list[str]) -> str:
        """Fast feedback right after a write (Fase 7, 7.8; Aider-style): syntax errors, undefined
        names and unparseable config files come back in the write's own result, instead of a
        full test run later. Milliseconds in-process; one narrow `ruff` run when the project
        lints with ruff. Unused imports are not reported: mid-task they are normal."""
        found: list[str] = []
        py: list[str] = []
        for rel in dict.fromkeys(paths):
            p = self.root / rel
            if not p.is_file():
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            try:
                if rel.endswith(".py"):
                    compile(text, rel, "exec")
                    py.append(rel)
                elif rel.endswith(".json"):
                    json.loads(text)
                elif rel.endswith(".toml"):
                    tomllib.loads(text)
            except SyntaxError as exc:
                found.append(f"- {rel}:{exc.lineno}: syntax error: {exc.msg}")
            except (ValueError, tomllib.TOMLDecodeError) as exc:
                found.append(f"- {rel}: invalid {Path(rel).suffix[1:]}: {str(exc)[:160]}")
        ruff = _ruff_prefix(self.lint_command)
        if py and ruff:
            cmd = shlex.join(
                [*ruff, "check", "--select", "E9,F63,F7,F82", "--output-format", "concise", *py]
            )
            res = await run_command(cmd, self.root, timeout=60)
            if res.returncode == 1:  # 2 = ruff itself failed: say nothing rather than guess
                plain = re.sub(r"\x1b\[[0-9;]*m", "", res.output)
                found += [
                    f"- {line.strip()}"
                    for line in plain.splitlines()
                    if re.match(r"^\S+:\d+:\d+: [A-Z]+\d+", line.strip())
                ][:10]
        return "\n".join(found)

    def tool_done(self, summary: str) -> str:
        return f"DONE: {summary}"

    def tool_blocked(self, reason: str, options: list[str] | None = None) -> str:
        return "BLOCKED: " + json.dumps(
            {"reason": reason, "options": options or []}, ensure_ascii=False
        )

    def tool_note_learning(self, title: str, detail: str = "", kind: str = "opportunity") -> str:
        self.learnings.append({"title": title, "detail": detail, "kind": kind})
        return f"recorded for the backlog: {title}"


QUICK_CHECKED = frozenset({"write_file", "edit_file", "apply_patch"})


def _ruff_prefix(lint_command: str) -> list[str]:
    """How the project runs ruff (`uv run ruff`, `ruff`, `python -m ruff`), or [] if it does not."""
    try:
        parts = shlex.split(lint_command)
    except ValueError:
        return []
    return parts[: parts.index("ruff") + 1] if "ruff" in parts else []


# ----------------------------------------------------------------------- patching

_HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _parse_unified_diff(patch: str) -> dict[str, list[tuple[int, list[str]]]]:
    files: dict[str, list[tuple[int, list[str]]]] = {}
    current: str | None = None
    hunk_lines: list[str] = []
    hunk_start = 0
    for line in patch.splitlines():
        if line.startswith("+++ "):
            name = line[4:].strip()
            if name.startswith("b/"):
                name = name[2:]
            current = name
            files.setdefault(current, [])
            continue
        if line.startswith(("--- ", "diff --git", "index ")):
            continue
        m = _HUNK.match(line)
        if m and current is not None:
            if hunk_lines:
                files[current].append((hunk_start, hunk_lines))
            hunk_start = int(m.group(1))
            hunk_lines = []
            continue
        if current is not None and line[:1] in (" ", "+", "-", "\\"):
            if not line.startswith("\\"):
                hunk_lines.append(line)
    if current is not None and hunk_lines:
        files[current].append((hunk_start, hunk_lines))
    return {k: v for k, v in files.items() if v}


def _parse_v4a_patch(patch: str) -> dict[str, list[tuple[int, list[str]]]]:
    """OpenAI's apply_patch format: `*** Update File: path` / `*** Add File: path`, hunks after
    `@@` lines, ` `/`-`/`+` lines without line numbers. Hunks are located by their content."""
    files: dict[str, list[tuple[int, list[str]]]] = {}
    current: str | None = None
    hunk: list[str] = []

    def flush() -> None:
        nonlocal hunk
        while hunk and hunk[-1] == " ":  # a blank line between hunks is not context
            hunk.pop()
        if current is not None and hunk:
            files.setdefault(current, []).append((1, hunk))
        hunk = []

    for line in patch.splitlines():
        if line.startswith("*** Delete File:"):
            raise ToolError("to delete a file use `delete_file`, not a patch")
        head = re.match(r"\*\*\* (?:Update|Add) File: (.+)$", line)
        if head:
            flush()
            current = head.group(1).strip()
            files.setdefault(current, [])
            continue
        if line.startswith("***"):  # Begin/End Patch, End of File, Move to
            if line.startswith("*** Move to:"):
                raise ToolError("moving a file is not supported in a patch; write the new one")
            continue
        if line.startswith("@@"):
            flush()
            continue
        if current is not None and line[:1] in (" ", "+", "-"):
            hunk.append(line)
        elif current is not None and line == "":
            hunk.append(" ")  # an empty context line lost its leading space
    flush()
    return {k: v for k, v in files.items() if v}


def _apply_hunks(original: list[str], hunks: list[tuple[int, list[str]]]) -> list[str]:
    result = list(original)
    offset = 0
    for start, lines in hunks:
        before = [line[1:] for line in lines if line[0] in " -"]
        after = [line[1:] for line in lines if line[0] in " +"]
        idx = start - 1 + offset
        if result[idx : idx + len(before)] != before:
            found = _locate(result, before, idx)
            if found is None:
                raise ToolError(f"hunk @@ -{start} does not match the current file; read it again")
            idx = found
        result[idx : idx + len(before)] = after
        offset += len(after) - len(before)
    return result


def _locate(haystack: list[str], needle: list[str], near: int) -> int | None:
    if not needle:
        return near
    candidates = [
        i for i in range(len(haystack) - len(needle) + 1) if haystack[i : i + len(needle)] == needle
    ]
    if not candidates:
        stripped = [n.strip() for n in needle]
        candidates = [
            i
            for i in range(len(haystack) - len(needle) + 1)
            if [h.strip() for h in haystack[i : i + len(needle)]] == stripped
        ]
    if not candidates:
        return None
    return min(candidates, key=lambda i: abs(i - near))
