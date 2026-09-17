"""Lexical + structural code search: exact matches, no hallucinated symbols.

Uses `ripgrep` when a real binary exists, otherwise a pure-Python scanner. Python symbols
come from the stdlib `ast` module; other languages use conservative regexes.
"""

from __future__ import annotations

import ast
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_SKIP_DIRS = {
    ".git",
    ".loompa",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".next",
    "target",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}
_TEXT_EXT = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".go",
    ".rs",
    ".java",
    ".kt",
    ".rb",
    ".php",
    ".md",
    ".yaml",
    ".yml",
    ".toml",
    ".json",
    ".sql",
    ".html",
    ".css",
    ".sh",
    ".txt",
    ".cfg",
    ".ini",
}


@dataclass
class SearchHit:
    path: str
    line: int
    text: str


@dataclass
class Symbol:
    name: str
    kind: str  # function | class | method | const | interface | type
    path: str
    line: int
    signature: str = ""
    parent: str | None = None
    doc: str = ""


def _real_rg() -> str | None:
    rg = shutil.which("rg")
    if not rg:
        return None
    try:
        out = subprocess.run([rg, "--version"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    return rg if out.startswith("ripgrep") else None


class CodeSearch:
    def __init__(self, root: Path, *, max_hits: int = 200):
        self.root = Path(root).resolve()
        self.max_hits = max_hits
        self._rg = _real_rg()

    def files(self, *, exts: set[str] | None = None) -> list[Path]:
        exts = exts or _TEXT_EXT
        out: list[Path] = []
        stack = [self.root]
        while stack:
            cur = stack.pop()
            try:
                entries = sorted(cur.iterdir())
            except OSError:
                continue
            for e in entries:
                if e.is_dir():
                    if e.name not in _SKIP_DIRS and not e.is_symlink():
                        stack.append(e)
                elif e.suffix.lower() in exts and e.stat().st_size < 2_000_000:
                    out.append(e)
        return out

    def grep(
        self,
        pattern: str,
        *,
        regex: bool = True,
        glob: str | None = None,
        case_sensitive: bool = False,
    ) -> list[SearchHit]:
        if self._rg:
            args = [self._rg, "--line-number", "--no-heading", "--color=never", "-m", "50"]
            if not case_sensitive:
                args.append("-i")
            if not regex:
                args.append("-F")
            if glob:
                args += ["-g", glob]
            for d in _SKIP_DIRS:
                args += ["-g", f"!{d}"]
            args += [pattern, str(self.root)]
            proc = subprocess.run(args, capture_output=True, text=True, timeout=60)
            hits = []
            for line in proc.stdout.splitlines()[: self.max_hits]:
                parts = line.split(":", 2)
                if len(parts) == 3:
                    hits.append(
                        SearchHit(
                            str(Path(parts[0]).relative_to(self.root)),
                            int(parts[1]),
                            parts[2].strip()[:200],
                        )
                    )
            return hits
        flags = 0 if case_sensitive else re.I
        rx = re.compile(pattern if regex else re.escape(pattern), flags)
        hits: list[SearchHit] = []
        for file in self.files():
            if glob and not file.match(glob):
                continue
            try:
                for i, line in enumerate(
                    file.read_text(encoding="utf-8", errors="ignore").splitlines(), 1
                ):
                    if rx.search(line):
                        hits.append(
                            SearchHit(str(file.relative_to(self.root)), i, line.strip()[:200])
                        )
                        if len(hits) >= self.max_hits:
                            return hits
            except OSError:
                continue
        return hits


_JS_SYMBOL = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?(?:async\s+)?(function|class|interface|type|const|let|enum)\s+([A-Za-z_$][\w$]*)"
)
_GO_SYMBOL = re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)|^\s*type\s+([A-Za-z_]\w*)")
_RS_SYMBOL = re.compile(
    r"^\s*(?:pub(?:\([^)]*\))?\s+)?(fn|struct|enum|trait|impl|type)\s+([A-Za-z_]\w*)"
)


@dataclass
class SymbolIndex:
    root: Path
    symbols: list[Symbol] = field(default_factory=list)

    @classmethod
    def build(cls, root: Path) -> SymbolIndex:
        root = Path(root).resolve()
        idx = cls(root=root)
        for file in CodeSearch(root).files(
            exts={".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs"}
        ):
            rel = str(file.relative_to(root))
            try:
                text = file.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if file.suffix == ".py":
                idx.symbols.extend(_python_symbols(text, rel))
            elif file.suffix in (".ts", ".tsx", ".js", ".jsx"):
                idx.symbols.extend(_regex_symbols(text, rel, _JS_SYMBOL))
            elif file.suffix == ".go":
                idx.symbols.extend(_regex_symbols(text, rel, _GO_SYMBOL, kind_group=None))
            elif file.suffix == ".rs":
                idx.symbols.extend(_regex_symbols(text, rel, _RS_SYMBOL))
        return idx

    def find(self, name: str, *, exact: bool = True, kind: str | None = None) -> list[Symbol]:
        name_l = name.lower()
        out = []
        for s in self.symbols:
            if kind and s.kind != kind:
                continue
            if (s.name == name) if exact else (name_l in s.name.lower()):
                out.append(s)
        return out

    def outline(self, path: str) -> list[Symbol]:
        return [s for s in self.symbols if s.path == path]


def _python_symbols(text: str, rel: str) -> list[Symbol]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out: list[Symbol] = []
    lines = text.splitlines()

    def sig(node: ast.AST) -> str:
        line = lines[node.lineno - 1].strip() if node.lineno - 1 < len(lines) else ""
        return line[:160]

    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            out.append(
                Symbol(
                    node.name,
                    "class",
                    rel,
                    node.lineno,
                    sig(node),
                    None,
                    (ast.get_docstring(node) or "")[:120],
                )
            )
            for child in node.body:
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
                    out.append(
                        Symbol(
                            child.name,
                            "method",
                            rel,
                            child.lineno,
                            sig(child),
                            node.name,
                            (ast.get_docstring(child) or "")[:120],
                        )
                    )
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            if not any(node.lineno == s.line for s in out):
                out.append(
                    Symbol(
                        node.name,
                        "function",
                        rel,
                        node.lineno,
                        sig(node),
                        None,
                        (ast.get_docstring(node) or "")[:120],
                    )
                )
        elif isinstance(node, ast.Assign) and node.col_offset == 0:
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    out.append(Symbol(t.id, "const", rel, node.lineno, sig(node)))
    return out


def _regex_symbols(
    text: str, rel: str, rx: re.Pattern[str], *, kind_group: int | None = 1
) -> list[Symbol]:
    out = []
    for i, line in enumerate(text.splitlines(), 1):
        m = rx.match(line)
        if not m:
            continue
        if kind_group is None:
            name = m.group(1) or m.group(2)
            kind = "function" if m.group(1) else "type"
        else:
            kind, name = m.group(1), m.group(2)
            kind = {
                "const": "const",
                "let": "const",
                "fn": "function",
                "struct": "class",
                "enum": "type",
                "trait": "interface",
                "impl": "class",
            }.get(kind, kind)
        out.append(Symbol(name, kind, rel, i, line.strip()[:160]))
    return out
