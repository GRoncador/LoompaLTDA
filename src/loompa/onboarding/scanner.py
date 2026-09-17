"""Brownfield Scanner: audits an existing repository to calibrate the factory.

Deterministic (Tier 3, $0.00): inspects manifests, configs, tests, CI, docker, migrations,
git history and produces a `RepoAudit` plus a draft `constitution.md`. The Architect Loompa
may later refine the draft with an LLM, but the factory works without one.
"""

from __future__ import annotations

import json
import re
import subprocess
import tomllib
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from loompa.config.schema import StackProfile
from loompa.speckit import render_constitution

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
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "target",
    "coverage",
    ".tox",
    ".idea",
    ".vscode",
}
_LANG_BY_EXT = {
    ".py": "Python",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".rs": "Rust",
    ".java": "Java",
    ".kt": "Kotlin",
    ".rb": "Ruby",
    ".php": "PHP",
    ".cs": "C#",
    ".swift": "Swift",
    ".dart": "Dart",
    ".sql": "SQL",
    ".sh": "Shell",
}
_FRAMEWORK_HINTS = {
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "sqlalchemy": "SQLAlchemy",
    "pydantic": "Pydantic",
    "typer": "Typer",
    "click": "Click",
    "celery": "Celery",
    "langgraph": "LangGraph",
    "langchain": "LangChain",
    "react": "React",
    "next": "Next.js",
    "vue": "Vue",
    "nuxt": "Nuxt",
    "svelte": "Svelte",
    "express": "Express",
    "nestjs": "NestJS",
    "@nestjs/core": "NestJS",
    "fastify": "Fastify",
    "tailwindcss": "Tailwind CSS",
    "prisma": "Prisma",
    "@prisma/client": "Prisma",
    "drizzle-orm": "Drizzle",
    "phaser": "Phaser",
    "vite": "Vite",
    "gin": "Gin",
    "echo": "Echo",
    "actix-web": "Actix",
    "axum": "Axum",
    "rails": "Rails",
}
_TEST_HINTS = {
    "pytest": "pytest",
    "unittest": "unittest",
    "jest": "Jest",
    "vitest": "Vitest",
    "mocha": "Mocha",
    "cypress": "Cypress",
    "playwright": "Playwright",
    "@playwright/test": "Playwright",
    "rspec": "RSpec",
}
_LINT_HINTS = {
    "ruff": "ruff",
    "black": "black",
    "flake8": "flake8",
    "mypy": "mypy",
    "pyright": "pyright",
    "isort": "isort",
    "eslint": "ESLint",
    "prettier": "Prettier",
    "biome": "Biome",
    "@biomejs/biome": "Biome",
}
_DB_HINTS = {
    "psycopg": "PostgreSQL",
    "psycopg2": "PostgreSQL",
    "asyncpg": "PostgreSQL",
    "pg": "PostgreSQL",
    "mysqlclient": "MySQL",
    "pymysql": "MySQL",
    "mysql2": "MySQL",
    "sqlite": "SQLite",
    "aiosqlite": "SQLite",
    "better-sqlite3": "SQLite",
    "redis": "Redis",
    "ioredis": "Redis",
    "pymongo": "MongoDB",
    "mongoose": "MongoDB",
    "motor": "MongoDB",
    "alembic": "PostgreSQL/SQL (Alembic)",
}


class ManifestInfo(BaseModel):
    path: str
    kind: str
    dependencies: list[str] = Field(default_factory=list)
    dev_dependencies: list[str] = Field(default_factory=list)
    scripts: dict[str, str] = Field(default_factory=dict)


class GitInfo(BaseModel):
    is_repo: bool = False
    commits: int = 0
    contributors: int = 0
    default_branch: str = ""
    last_commit_iso: str = ""
    dirty: bool = False


class RepoAudit(BaseModel):
    root: str
    file_count: int = 0
    language_files: dict[str, int] = Field(default_factory=dict)
    top_level: list[str] = Field(default_factory=list)
    manifests: list[ManifestInfo] = Field(default_factory=list)
    stack: StackProfile = Field(default_factory=StackProfile)
    test_dirs: list[str] = Field(default_factory=list)
    test_file_count: int = 0
    lint_configs: list[str] = Field(default_factory=list)
    ci_files: list[str] = Field(default_factory=list)
    docker: list[str] = Field(default_factory=list)
    migrations: list[str] = Field(default_factory=list)
    schemas: list[str] = Field(default_factory=list)
    docs: list[str] = Field(default_factory=list)
    coverage_hint: str = "desconhecida"
    git: GitInfo = Field(default_factory=GitInfo)
    suggested_commands: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)

    @property
    def primary_language(self) -> str:
        return (
            max(self.language_files, key=self.language_files.get)
            if self.language_files
            else "desconhecida"
        )


class BrownfieldScanner:
    def __init__(self, root: Path, *, max_files: int = 20000):
        self.root = Path(root).resolve()
        self.max_files = max_files

    # ------------------------------------------------------------------ walk
    def _walk(self):
        count = 0
        stack = [self.root]
        while stack:
            current = stack.pop()
            try:
                entries = sorted(current.iterdir(), key=lambda p: p.name)
            except (PermissionError, FileNotFoundError):
                continue
            for entry in entries:
                if entry.is_dir():
                    if entry.name not in _SKIP_DIRS and not entry.is_symlink():
                        stack.append(entry)
                elif entry.is_file():
                    count += 1
                    if count > self.max_files:
                        return
                    yield entry

    def scan(self) -> RepoAudit:
        audit = RepoAudit(root=str(self.root))
        langs: Counter[str] = Counter()
        test_dirs: set[str] = set()
        for file in self._walk():
            audit.file_count += 1
            rel = file.relative_to(self.root)
            lang = _LANG_BY_EXT.get(file.suffix.lower())
            if lang:
                langs[lang] += 1
            name = file.name.lower()
            parts = [p.lower() for p in rel.parts]
            if any(
                p in ("tests", "test", "__tests__", "spec", "specs") for p in parts[:-1]
            ) or re.match(r"^(test_.*|.*_test|.*\.(test|spec))\.(py|ts|tsx|js|jsx|go|rs)$", name):
                audit.test_file_count += 1
                test_dirs.add(str(rel.parent))
            self._classify(audit, rel, name)
        audit.language_files = dict(langs.most_common())
        audit.stack.languages = [lang for lang, _ in langs.most_common(4)]
        audit.test_dirs = sorted(test_dirs)[:12]
        audit.top_level = sorted(
            p.name + ("/" if p.is_dir() else "")
            for p in self.root.iterdir()
            if p.name not in _SKIP_DIRS
        )[:40]
        self._manifests(audit)
        self._git(audit)
        self._commands(audit)
        self._dedupe(audit)
        return audit

    def _classify(self, audit: RepoAudit, rel: Path, name: str) -> None:
        s = str(rel)
        if name in (
            "pyproject.toml",
            "package.json",
            "requirements.txt",
            "go.mod",
            "cargo.toml",
            "gemfile",
            "pom.xml",
            "build.gradle",
            "composer.json",
        ):
            audit.manifests.append(ManifestInfo(path=s, kind=name))
        if name in (
            "ruff.toml",
            ".ruff.toml",
            ".flake8",
            "setup.cfg",
            "mypy.ini",
            ".eslintrc",
            ".eslintrc.js",
            ".eslintrc.json",
            ".eslintrc.cjs",
            "eslint.config.js",
            "eslint.config.mjs",
            ".prettierrc",
            "biome.json",
            "tox.ini",
            ".pylintrc",
            "pyrightconfig.json",
        ):
            audit.lint_configs.append(s)
        if ".github/workflows" in s or name in (
            ".gitlab-ci.yml",
            ".travis.yml",
            "jenkinsfile",
            ".circleci",
        ):
            audit.ci_files.append(s)
            audit.stack.ci.append("GitHub Actions" if ".github" in s else name)
        if name.startswith("dockerfile") or name in (
            "docker-compose.yml",
            "docker-compose.yaml",
            "compose.yaml",
            "compose.yml",
        ):
            audit.docker.append(s)
        if (
            any(seg in ("migrations", "alembic", "migrate") for seg in rel.parts[:-1])
            or name == "alembic.ini"
        ):
            if len(audit.migrations) < 20:
                audit.migrations.append(s)
        if name.endswith(
            (
                ".prisma",
                ".graphql",
                ".proto",
                "openapi.yaml",
                "openapi.yml",
                "openapi.json",
                "schema.sql",
            )
        ) or name in ("schema.py", "models.py"):
            if len(audit.schemas) < 20:
                audit.schemas.append(s)
        if rel.parts[0].lower() in ("docs", "doc", "adr", "adrs") or name in (
            "readme.md",
            "contributing.md",
            "architecture.md",
            "changelog.md",
        ):
            if len(audit.docs) < 30:
                audit.docs.append(s)
        if name in (".coveragerc", "coverage.xml", "lcov.info") or "coverage" in name:
            audit.coverage_hint = "há relatórios/config de cobertura no repositório"

    # ------------------------------------------------------------- manifests
    def _manifests(self, audit: RepoAudit) -> None:
        for m in audit.manifests:
            path = self.root / m.path
            try:
                if m.kind == "pyproject.toml":
                    self._pyproject(m, path)
                elif m.kind == "package.json":
                    self._package_json(m, path)
                elif m.kind == "requirements.txt":
                    m.dependencies = [
                        re.split(r"[<>=!~\[; ]", line.strip(), maxsplit=1)[0].lower()
                        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()
                        if line.strip() and not line.startswith(("#", "-"))
                    ]
                elif m.kind == "go.mod":
                    m.dependencies = re.findall(
                        r"^\s*([\w./\-]+) v[\w.\-]+",
                        path.read_text(encoding="utf-8", errors="ignore"),
                        re.M,
                    )
                elif m.kind == "cargo.toml":
                    data = tomllib.loads(path.read_text(encoding="utf-8", errors="ignore"))
                    m.dependencies = list((data.get("dependencies") or {}).keys())
            except Exception as exc:  # noqa: BLE001 - scanner must never crash onboarding
                audit.warnings.append(f"não consegui ler {m.path}: {type(exc).__name__}")
            for dep in [*m.dependencies, *m.dev_dependencies]:
                key = dep.lower()
                if key in _FRAMEWORK_HINTS:
                    audit.stack.frameworks.append(_FRAMEWORK_HINTS[key])
                if key in _TEST_HINTS:
                    audit.stack.test_frameworks.append(_TEST_HINTS[key])
                if key in _LINT_HINTS:
                    audit.stack.linters.append(_LINT_HINTS[key])
                if key in _DB_HINTS:
                    audit.stack.databases.append(_DB_HINTS[key])
        for cfg in audit.lint_configs:
            base = Path(cfg).name.lower()
            for hint, label in _LINT_HINTS.items():
                if hint in base:
                    audit.stack.linters.append(label)

    def _pyproject(self, m: ManifestInfo, path: Path) -> None:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="ignore"))
        project = data.get("project", {})
        deps = list(project.get("dependencies", []))
        for group in (project.get("optional-dependencies") or {}).values():
            deps.extend(group)
        dev = []
        for group in (data.get("dependency-groups") or {}).values():
            dev.extend(d for d in group if isinstance(d, str))
        poetry = (data.get("tool") or {}).get("poetry") or {}
        deps.extend(k for k in (poetry.get("dependencies") or {}) if k != "python")
        for group in (poetry.get("group") or {}).values():
            dev.extend((group.get("dependencies") or {}).keys())
        m.dependencies = [re.split(r"[<>=!~\[; ]", d, maxsplit=1)[0].lower() for d in deps]
        m.dev_dependencies = [re.split(r"[<>=!~\[; ]", d, maxsplit=1)[0].lower() for d in dev]
        tool = data.get("tool") or {}
        for name in ("ruff", "black", "mypy", "pytest", "isort", "pyright"):
            if name in tool:
                m.dev_dependencies.append(name)
        if "uv" in tool or (self.root / "uv.lock").exists():
            m.scripts["package_manager"] = "uv"
        elif poetry:
            m.scripts["package_manager"] = "poetry"
        else:
            m.scripts["package_manager"] = "pip"

    def _package_json(self, m: ManifestInfo, path: Path) -> None:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
        m.dependencies = list((data.get("dependencies") or {}).keys())
        m.dev_dependencies = list((data.get("devDependencies") or {}).keys())
        m.scripts = {k: str(v) for k, v in (data.get("scripts") or {}).items()}
        parent = path.parent
        if (parent / "pnpm-lock.yaml").exists():
            m.scripts["package_manager"] = "pnpm"
        elif (parent / "yarn.lock").exists():
            m.scripts["package_manager"] = "yarn"
        elif (parent / "bun.lockb").exists() or (parent / "bun.lock").exists():
            m.scripts["package_manager"] = "bun"
        else:
            m.scripts["package_manager"] = "npm"

    # ------------------------------------------------------------------- git
    def _git(self, audit: RepoAudit) -> None:
        if not (self.root / ".git").exists():
            return
        audit.git.is_repo = True

        def run(*args: str) -> str:
            try:
                return subprocess.run(
                    ["git", "-C", str(self.root), *args], capture_output=True, text=True, timeout=20
                ).stdout.strip()
            except (FileNotFoundError, subprocess.TimeoutExpired):
                return ""

        audit.git.commits = int(run("rev-list", "--count", "HEAD") or 0)
        audit.git.contributors = len(
            [line for line in run("shortlog", "-sn", "HEAD").splitlines() if line.strip()]
        )
        audit.git.default_branch = run("rev-parse", "--abbrev-ref", "HEAD")
        audit.git.last_commit_iso = run("log", "-1", "--format=%cI")
        audit.git.dirty = bool(run("status", "--porcelain"))

    # -------------------------------------------------------------- commands
    def _commands(self, audit: RepoAudit) -> None:
        cmds = audit.suggested_commands
        py = next(
            (m for m in audit.manifests if m.kind in ("pyproject.toml", "requirements.txt")), None
        )
        js = next((m for m in audit.manifests if m.kind == "package.json"), None)
        if py:
            pm = py.scripts.get("package_manager", "pip")
            prefix = "uv run " if pm == "uv" else ("poetry run " if pm == "poetry" else "")
            if (
                "pytest" in audit.stack.test_frameworks
                or "pytest" in py.dev_dependencies
                or audit.test_file_count
            ):
                cmds["test"] = f"{prefix}pytest -q"
            if "ruff" in audit.stack.linters:
                cmds["lint"] = f"{prefix}ruff check ."
                cmds["format"] = f"{prefix}ruff format ."
            elif "flake8" in audit.stack.linters:
                cmds["lint"] = f"{prefix}flake8"
            if "mypy" in audit.stack.linters:
                cmds["typecheck"] = f"{prefix}mypy ."
            elif "pyright" in audit.stack.linters:
                cmds["typecheck"] = f"{prefix}pyright"
            audit.stack.package_managers.append(pm)
        if js:
            pm = js.scripts.get("package_manager", "npm")
            runner = f"{pm} run" if pm != "npm" else "npm run"
            if "test" in js.scripts:
                cmds.setdefault("test", f"{runner} test")
            if "lint" in js.scripts:
                cmds.setdefault("lint", f"{runner} lint")
            if "typecheck" in js.scripts:
                cmds.setdefault("typecheck", f"{runner} typecheck")
            elif "typescript" in js.dev_dependencies:
                cmds.setdefault("typecheck", "npx tsc --noEmit")
            audit.stack.package_managers.append(pm)

    @staticmethod
    def _dedupe(audit: RepoAudit) -> None:
        s = audit.stack
        for field in (
            "languages",
            "frameworks",
            "package_managers",
            "test_frameworks",
            "linters",
            "ci",
            "databases",
        ):
            seen: list[str] = []
            for item in getattr(s, field):
                if item not in seen:
                    seen.append(item)
            setattr(s, field, seen)
        if not audit.test_file_count and not s.test_frameworks:
            audit.warnings.append(
                "nenhum teste automatizado encontrado — o Inspector Loompa começará criando a suíte base"
            )
        if not s.linters:
            audit.warnings.append(
                "nenhum linter configurado — o Architect Loompa proporá um no primeiro ADR"
            )
        if audit.git.dirty:
            audit.warnings.append("há alterações não commitadas no repositório")


def draft_constitution_from_audit(audit: RepoAudit, name: str, mission: str = "") -> str:
    s = audit.stack
    stack = [f"Linguagem principal: {audit.primary_language}"]
    stack += [f"Framework: {f}" for f in s.frameworks]
    stack += [f"Banco de dados: {d}" for d in s.databases]
    stack += [f"Gerenciador de pacotes: {p}" for p in s.package_managers]
    allowed: list[str] = []
    for m in audit.manifests:
        allowed.extend(sorted(set(m.dependencies))[:40])
    conventions = []
    if audit.test_dirs:
        conventions.append(f"Testes vivem em: {', '.join(audit.test_dirs[:5])}")
    if s.linters:
        conventions.append(f"Estilo verificado por: {', '.join(s.linters)}")
    if audit.ci_files:
        conventions.append(f"CI existente: {', '.join(audit.ci_files[:3])}")
    if audit.migrations:
        conventions.append("Alterações de banco passam por migrations (nunca editar schema à mão)")
    if audit.schemas:
        conventions.append(f"Contratos/schemas em: {', '.join(audit.schemas[:5])}")
    if audit.docker:
        conventions.append(f"Ambiente containerizado: {', '.join(audit.docker[:3])}")
    quality = [f"`{k}`: `{v}`" for k, v in audit.suggested_commands.items()]
    quality.append(f"Cobertura atual: {audit.coverage_hint}")
    rules = [
        "Não introduzir bibliotecas ‘alienígenas’: qualquer dependência nova exige ADR.",
        "Respeitar a arquitetura existente mapeada no onboarding; refatorações amplas viram história própria.",
    ]
    return render_constitution(
        name=name,
        mission=mission,
        stack=stack,
        allowed_libraries=sorted(set(allowed)),
        conventions=conventions,
        quality=quality,
        rules=rules,
    )
