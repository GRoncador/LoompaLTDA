"""Brownfield risk facts (Fase 7, items 7.3, 7.5 and 7.6), measured in code before any model.

For each path a plan will touch: does it exist yet, is it a schema, a migration or a public
contract, how often it changed (git), how many files depend on it and whether any test mentions
it. That gives a deterministic risk level per file, decides whether a story needs pre-flight
planning (a plan touching schemas, migrations or contracts does), and is what the Architect's
pre-flight risk report starts from, instead of guessing about code it has not read.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from loompa.hygiene import is_test_path

_SKIP = {".git", ".loompa", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}
_SOURCE = (".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".rb", ".sql")
_KINDS = (
    ("migration", re.compile(r"(^|/)(migrations?|alembic|migrate)/", re.I)),
    (
        "schema",
        re.compile(r"(^|/)(schema|schemas|models?)\.(py|sql|ts|prisma|graphql)$|\.sql$", re.I),
    ),
    ("contract", re.compile(r"(openapi|swagger)[^/]*\.(ya?ml|json)$|\.proto$|(^|/)api/", re.I)),
)
RISKY_KINDS = {"migration", "schema", "contract"}
_GENERIC_STEMS = {"__init__", "main", "utils", "util", "helpers", "common", "base", "index", "app"}


@dataclass
class FileRisk:
    path: str
    exists: bool
    kind: str = "code"
    commits: int = 0
    dependents: int = 0
    tested: bool = False
    level: str = "low"
    reasons: list[str] = field(default_factory=list)

    def line(self) -> str:
        state = "existente" if self.exists else "novo"
        facts = [state, self.kind]
        if self.exists:
            facts += [
                f"{self.commits} commit(s)",
                f"{self.dependents} dependente(s)",
                "com teste" if self.tested else "sem teste que o cite",
            ]
        return f"| {self.path} | {self.level} | {', '.join(facts)} |"


def kind_of(path: str) -> str:
    if is_test_path(path):
        return "test"
    for kind, pattern in _KINDS:
        if pattern.search(path):
            return kind
    if path.endswith((".toml", ".json", ".yaml", ".yml", ".cfg", ".ini", ".lock")):
        return "config"
    if path.endswith((".md", ".rst", ".txt")):
        return "doc"
    return "code"


def needs_preflight(paths: list[str]) -> bool:
    """A plan that touches a schema, a migration or a public contract is planned twice."""
    return any(kind_of(p) in RISKY_KINDS for p in paths)


def _expand(root: Path, paths: list[str], limit: int) -> list[str]:
    out: list[str] = []
    for rel in paths:
        if rel.startswith(".loompa/"):
            continue
        p = root / rel
        if p.is_dir():
            out += [
                str(f.relative_to(root))
                for f in sorted(p.rglob("*"))
                if f.is_file()
                and f.suffix in _SOURCE
                and not any(part in _SKIP for part in f.relative_to(root).parts)
            ]
        else:
            out.append(rel.rstrip("/"))
    return list(dict.fromkeys(out))[:limit]


def _sources(root: Path, limit: int = 3000) -> list[tuple[str, str]]:
    found = []
    for f in root.rglob("*"):
        rel = f.relative_to(root)
        if any(part in _SKIP for part in rel.parts) or f.suffix not in _SOURCE or not f.is_file():
            continue
        try:
            found.append((str(rel), f.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
        if len(found) >= limit:
            break
    return found


def _commits(root: Path, rel: str) -> int:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "log", "--oneline", "-n", "200", "--", rel],
            capture_output=True,
            text=True,
            timeout=20,
        ).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return 0
    return len([line for line in out.splitlines() if line.strip()])


def assess(root: Path, paths: list[str], *, limit: int = 30) -> list[FileRisk]:
    """Risk facts for the files a plan lists (directories are expanded to their sources)."""
    root = Path(root)
    sources = _sources(root)
    risks = []
    for rel in _expand(root, paths, limit):
        p = root / rel
        r = FileRisk(rel, exists=p.is_file(), kind=kind_of(rel))
        stem = Path(rel).stem
        if r.exists and r.kind not in ("test", "doc", "config"):
            r.commits = _commits(root, rel)
            if stem not in _GENERIC_STEMS and len(stem) > 2:
                word = re.compile(rf"\b{re.escape(stem)}\b")
                users = [path for path, text in sources if path != rel and word.search(text)]
                r.dependents = len([u for u in users if not is_test_path(u)])
                r.tested = any(is_test_path(u) for u in users)
        r.level, r.reasons = _level(r)
        risks.append(r)
    return risks


def _level(r: FileRisk) -> tuple[str, list[str]]:
    reasons = []
    if r.kind in RISKY_KINDS:
        reasons.append(f"{r.kind} {'existente' if r.exists else 'novo'}")
        return ("high" if r.exists else "medium"), reasons
    if not r.exists or r.kind in ("test", "doc"):
        return "low", reasons
    if not r.tested:
        reasons.append("nenhum teste cita este módulo")
    if r.dependents >= 3:
        reasons.append(f"{r.dependents} arquivos dependem dele")
    if r.commits >= 10:
        reasons.append(f"muda com frequência ({r.commits} commits)")
    if r.dependents >= 5 and not r.tested:
        return "high", reasons
    return ("medium" if reasons else "low"), reasons


def render_matrix(risks: list[FileRisk]) -> str:
    if not risks:
        return "_(nenhum arquivo existente é tocado)_"
    return "| Arquivo | Risco | Fatos |\n| --- | --- | --- |\n" + "\n".join(r.line() for r in risks)


def declared_dependencies(root: Path) -> str:
    """The libraries the project declares (pyproject.toml, package.json), for the spec review:
    a criterion that needs anything else cannot be built as written."""
    root = Path(root)
    lines = []
    py = root / "pyproject.toml"
    if py.is_file():
        try:
            data = tomllib.loads(py.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, OSError):
            data = {}
        project = data.get("project") or {}
        deps = [_name(d) for d in project.get("dependencies") or []]
        dev = [
            _name(d)
            for group in (
                *(project.get("optional-dependencies") or {}).values(),
                *(data.get("dependency-groups") or {}).values(),
            )
            for d in group
            if isinstance(d, str)
        ]
        if deps:
            lines.append("python: " + ", ".join(deps))
        if dev:
            lines.append("python (dev/optional): " + ", ".join(dict.fromkeys(dev)))
    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        for key in ("dependencies", "devDependencies"):
            if data.get(key):
                lines.append(f"node ({key}): " + ", ".join(sorted(data[key])))
    return "\n".join(lines)


def _name(requirement: str) -> str:
    return re.split(r"[\s<>=!~;\[]", requirement.strip(), maxsplit=1)[0]
