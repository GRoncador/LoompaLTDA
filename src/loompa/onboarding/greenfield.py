"""Greenfield Initializer: bootstrap an empty directory into a factory-ready repo."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loompa.config.schema import StackProfile
from loompa.speckit import render_constitution


@dataclass
class StackPreset:
    key: str
    label: str
    profile: StackProfile
    allowed_libraries: list[str]
    commands: dict[str, str]
    files: dict[str, str] = field(default_factory=dict)


def _py_gitignore() -> str:
    return "__pycache__/\n*.py[cod]\n.venv/\n.pytest_cache/\n.ruff_cache/\n.env\n.loompa/worktrees/\n.loompa/*.db*\n"


def _node_gitignore() -> str:
    return "node_modules/\ndist/\n.env\n.loompa/worktrees/\n.loompa/*.db*\n"


STACK_PRESETS: dict[str, StackPreset] = {
    "python-fastapi": StackPreset(
        key="python-fastapi",
        label="Python + FastAPI + PostgreSQL",
        profile=StackProfile(
            languages=["Python"],
            frameworks=["FastAPI", "Pydantic", "SQLAlchemy"],
            package_managers=["uv"],
            test_frameworks=["pytest"],
            linters=["ruff", "mypy"],
            databases=["PostgreSQL"],
        ),
        allowed_libraries=[
            "fastapi",
            "uvicorn",
            "pydantic",
            "sqlalchemy",
            "alembic",
            "httpx",
            "pytest",
            "ruff",
            "mypy",
        ],
        commands={
            "test": "uv run pytest -q",
            "lint": "uv run ruff check .",
            "format": "uv run ruff format .",
            "typecheck": "uv run mypy src",
        },
        files={
            ".gitignore": _py_gitignore(),
            "pyproject.toml": '[project]\nname = "{slug}"\nversion = "0.1.0"\nrequires-python = ">=3.11"\ndependencies = ["fastapi", "uvicorn[standard]", "pydantic", "sqlalchemy", "alembic"]\n\n[dependency-groups]\ndev = ["pytest", "httpx", "ruff", "mypy"]\n\n[tool.pytest.ini_options]\ntestpaths = ["tests"]\n',
            "src/{pkg}/__init__.py": "",
            "src/{pkg}/main.py": 'from fastapi import FastAPI\n\napp = FastAPI(title="{name}")\n\n\n@app.get("/health")\ndef health() -> dict[str, str]:\n    return {{"status": "ok"}}\n',
            "tests/test_health.py": 'from fastapi.testclient import TestClient\n\nfrom {pkg}.main import app\n\n\ndef test_health() -> None:\n    assert TestClient(app).get("/health").json() == {{"status": "ok"}}\n',
        },
    ),
    "python-cli": StackPreset(
        key="python-cli",
        label="Python CLI (Typer)",
        profile=StackProfile(
            languages=["Python"],
            frameworks=["Typer"],
            package_managers=["uv"],
            test_frameworks=["pytest"],
            linters=["ruff"],
        ),
        allowed_libraries=["typer", "rich", "pydantic", "pytest", "ruff"],
        commands={
            "test": "uv run pytest -q",
            "lint": "uv run ruff check .",
            "format": "uv run ruff format .",
        },
        files={
            ".gitignore": _py_gitignore(),
            "pyproject.toml": '[project]\nname = "{slug}"\nversion = "0.1.0"\nrequires-python = ">=3.11"\ndependencies = ["typer", "rich"]\n\n[project.scripts]\n{slug} = "{pkg}.cli:app"\n\n[dependency-groups]\ndev = ["pytest", "ruff"]\n',
            "src/{pkg}/__init__.py": "",
            "src/{pkg}/cli.py": 'import typer\n\napp = typer.Typer(help="{name}")\n\n\n@app.command()\ndef hello(name: str = "world") -> None:\n    typer.echo(f"hello {{name}}")\n',
            "tests/test_cli.py": 'from typer.testing import CliRunner\n\nfrom {pkg}.cli import app\n\n\ndef test_hello() -> None:\n    assert "hello world" in CliRunner().invoke(app, ["hello"]).stdout\n',
        },
    ),
    "node-react": StackPreset(
        key="node-react",
        label="TypeScript + React (Vite) + Tailwind",
        profile=StackProfile(
            languages=["TypeScript"],
            frameworks=["React", "Vite", "Tailwind CSS"],
            package_managers=["npm"],
            test_frameworks=["Vitest"],
            linters=["ESLint", "Prettier"],
        ),
        allowed_libraries=[
            "react",
            "react-dom",
            "vite",
            "tailwindcss",
            "vitest",
            "eslint",
            "prettier",
            "typescript",
        ],
        commands={"test": "npm test", "lint": "npm run lint", "typecheck": "npx tsc --noEmit"},
        files={
            ".gitignore": _node_gitignore(),
            "package.json": '{{\n  "name": "{slug}",\n  "private": true,\n  "version": "0.1.0",\n  "type": "module",\n  "scripts": {{ "dev": "vite", "build": "tsc && vite build", "test": "vitest run", "lint": "eslint ." }},\n  "dependencies": {{ "react": "^18.3.0", "react-dom": "^18.3.0" }},\n  "devDependencies": {{ "typescript": "^5.5.0", "vite": "^5.4.0", "vitest": "^2.0.0", "eslint": "^9.0.0", "tailwindcss": "^3.4.0" }}\n}}\n',
            "src/App.tsx": "export default function App() {{\n  return <h1>{name}</h1>;\n}}\n",
            "README.md": "# {name}\n",
        },
    ),
    "custom": StackPreset(
        key="custom",
        label="Custom (define in constitution)",
        profile=StackProfile(),
        allowed_libraries=[],
        commands={},
        files={".gitignore": ".env\n.loompa/worktrees/\n.loompa/*.db*\n"},
    ),
}


class GreenfieldInitializer:
    def __init__(
        self, root: Path, *, name: str, slug: str, preset: str = "custom", mission: str = ""
    ):
        self.root = Path(root)
        self.name = name
        self.slug = slug
        self.pkg = slug.replace("-", "_")
        self.preset = STACK_PRESETS.get(preset, STACK_PRESETS["custom"])
        self.mission = mission

    def write_skeleton(self, *, overwrite: bool = False) -> list[Path]:
        written: list[Path] = []
        for rel, content in self.preset.files.items():
            path = self.root / rel.format(pkg=self.pkg, slug=self.slug)
            if path.exists() and not overwrite:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                content.format(pkg=self.pkg, slug=self.slug, name=self.name), encoding="utf-8"
            )
            written.append(path)
        return written

    def constitution(self) -> str:
        p = self.preset.profile
        stack = [f"Linguagem: {lang}" for lang in p.languages]
        stack += [f"Framework: {f}" for f in p.frameworks]
        stack += [f"Banco de dados: {d}" for d in p.databases]
        stack += [f"Gerenciador de pacotes: {pm}" for pm in p.package_managers]
        quality = [f"`{k}`: `{v}`" for k, v in self.preset.commands.items()]
        return render_constitution(
            name=self.name,
            mission=self.mission,
            stack=stack or ["_(a definir)_"],
            allowed_libraries=self.preset.allowed_libraries,
            conventions=[
                "Estrutura `src/` + `tests/`",
                "Commits semânticos",
                "Branch protegido: `main`",
            ],
            quality=quality or ["_(definir comandos de teste e lint)_"],
            rules=[],
        )

    def ensure_git(self, default_branch: str = "main") -> bool:
        """Initialise git if needed. Returns True when a new repo was created."""
        if (self.root / ".git").exists():
            return False
        subprocess.run(
            ["git", "init", "-q", "-b", default_branch, str(self.root)], check=True, timeout=30
        )
        return True
