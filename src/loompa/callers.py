"""Who uses what a change touches, found by name in the code, with no model call.

`contas` Sprint 2, S-047: the story moved `valor` from reais to integer cents. Its diff and its 138
tests were right, and the Inspector passed it, but `resumo`, `export` and the `list` total in
`cli.py` still read `valor` as reais and showed every amount 100 times too big. Those lines were
not in the diff, so nobody looking at the diff could see them. This module lists them: the names a
change defines or alters, and every line outside the change that mentions one.

It is a text search, not a type checker: a common name also matches unrelated lines, which is why
the output says so and stays short. The reader (the Inspector, the Architect's Pre-flight) decides.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from loompa.hygiene import is_test_path
from loompa.memory.lexical import CodeSearch

CODE_EXT = {".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".go", ".rs", ".java", ".rb"}
# A definition on one line: a function, a class, a declared name, or a typed field / parameter.
_DEF = re.compile(
    r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|type|const|let|var|struct|enum)"
    r"\s+([A-Za-z_]\w*)"
    r"|^\s+([A-Za-z_]\w*)\s*\??\s*:\s*[A-Za-z_\[\"'(]"
)
_HUNK = re.compile(r"^@@ [^@]* @@ ?(.*)$")
_FILE = re.compile(r"^\+\+\+ b/(.+)$")
_DEF_OF = r"^\s*(?:export\s+)?(?:async\s+)?(?:def|class|function|interface|type|const|let|var|struct|enum)\s+{name}\b"
# Names too generic to search for: every file has them.
STOP = {
    "self", "cls", "args", "kwargs", "main", "name", "path", "data", "value", "values", "result",
    "text", "state", "config", "ctx", "type", "key", "item", "items", "line", "lines", "file",
    "files", "root", "app", "log", "out", "msg", "err", "exc", "env", "opts", "options", "default",
    "return", "none", "true", "false", "string", "number", "boolean", "props", "children",
}  # fmt: skip


@dataclass
class Use:
    name: str
    path: str
    line: int
    text: str


def _usable(name: str) -> bool:
    return (
        len(name) >= 3
        and name.lower() not in STOP
        and not (name.startswith("__") and name.endswith("__"))
        and not name.startswith("test")
    )


def code_file(path: str) -> bool:
    return PurePosixPath(path).suffix.lower() in CODE_EXT and not is_test_path(path)


def changed_names(diff: str) -> list[str]:
    """Names the diff defines, alters or removes in product code, in order of appearance: the
    definitions on its added and removed lines, and the function or class each changed line sits
    in (from the hunk header, or from a definition shown as context above it)."""
    names: list[str] = []
    current = ""
    enclosing = ""
    for line in diff.splitlines():
        if m := _FILE.match(line):
            current, enclosing = m.group(1), ""
            continue
        if not code_file(current):
            continue
        if m := _HUNK.match(line):
            d = _DEF.match(m.group(1))
            enclosing = (d.group(1) or "") if d else ""
            continue
        if line.startswith(("+++", "---")) or line[:1] not in "+- ":
            continue
        d = _DEF.match(line[1:])
        if d and d.group(1):
            enclosing = d.group(1)
        if line[:1] == " ":
            continue
        if d and _usable(name := d.group(1) or d.group(2)):
            names.append(name)
        if enclosing and _usable(enclosing):
            names.append(enclosing)
    return list(dict.fromkeys(names))


def added_lines(diff: str) -> dict[str, set[str]]:
    """Per file, the stripped text of the lines the diff adds: those are already in front of the
    reader, so a use found on one of them is not news."""
    out: dict[str, set[str]] = {}
    current = ""
    for line in diff.splitlines():
        if m := _FILE.match(line):
            current = m.group(1)
        elif line.startswith("+") and not line.startswith("+++") and current:
            out.setdefault(current, set()).add(line[1:].strip())
    return out


def uses(
    root: Path,
    names: list[str],
    *,
    skip: dict[str, set[str]] | None = None,
    per_name: int = 12,
    limit: int = 50,
) -> list[Use]:
    """Lines in product code that mention each name as a whole word, minus its own definitions
    and the lines in `skip` (per file). At most `per_name` per name and `limit` in all."""
    search = CodeSearch(root)
    skip = skip or {}
    found: list[Use] = []
    for name in names:
        own = re.compile(_DEF_OF.format(name=re.escape(name)))
        n = 0
        for hit in search.grep(rf"\b{re.escape(name)}\b", case_sensitive=True):
            if not code_file(hit.path) or own.match(hit.text) or hit.text in skip.get(hit.path, ()):
                continue
            found.append(Use(name, hit.path, hit.line, hit.text))
            n += 1
            if n >= per_name or len(found) >= limit:
                break
        if len(found) >= limit:
            break
    return found


def render(found: list[Use], *, mark_outside: list[str] | None = None) -> str:
    """Grouped by name. With `mark_outside` (the plan's paths), uses in other files are marked."""

    def outside(path: str) -> bool:
        return mark_outside is not None and not any(
            path == p or path.startswith(p.rstrip("/") + "/") for p in mark_outside
        )

    lines: list[str] = []
    for name in dict.fromkeys(u.name for u in found):
        lines.append(f"`{name}`:")
        lines += [
            f"- {u.path}:{u.line}{' (outside the plan)' if outside(u.path) else ''}: {u.text}"
            for u in found
            if u.name == name
        ]
    return "\n".join(lines)


def callers_of_diff(root: Path, diff: str, **kw: int) -> str:
    """For the Inspector: uses, outside the diff's added lines, of the names the diff changed."""
    names = changed_names(diff)
    return render(uses(root, names, skip=added_lines(diff), **kw)) if names else ""


def callers_of_plan(root: Path, plan_text: str, files: list[str], **kw: int) -> str:
    """For the Pre-flight: the names the plan's text mentions that are defined in the product files
    it will change, and every use of them, marked when it lies outside the plan's paths."""
    defined: set[str] = set()
    for rel in files:
        p = root / rel
        if not (p.is_file() and code_file(rel)):
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            if (d := _DEF.match(line)) and _usable(name := d.group(1) or d.group(2)):
                defined.add(name)
    mentioned = [w for w in dict.fromkeys(re.findall(r"[A-Za-z_]\w+", plan_text)) if w in defined]
    return render(uses(root, mentioned, **kw), mark_outside=files) if mentioned else ""
