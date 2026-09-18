from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from loompa.config import ConfigStore


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run tests marked `live` against a real provider (needs GEMINI_API_KEY in the "
        "environment or in ~/.loompa/secrets.env). Never used in CI.",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="live provider test: pass --live (and configure a key)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@pytest.fixture
def hub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ConfigStore:
    """Isolated global hub so tests never touch ~/.loompa."""
    home = tmp_path / "hub"
    monkeypatch.setenv("LOOMPA_HOME", str(home))
    return ConfigStore(home)


def git(*args: str, cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git("init", "-q", "-b", "main", cwd=repo)
    git("config", "user.email", "test@loompa.local", cwd=repo)
    git("config", "user.name", "Loompa Test", cwd=repo)
    (repo / "README.md").write_text("# demo\n")
    git("add", ".", cwd=repo)
    git("commit", "-qm", "init", cwd=repo)
    return repo


@pytest.fixture
def brownfield_repo(git_repo: Path) -> Path:
    (git_repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["fastapi>=0.111", "sqlalchemy", "psycopg[binary]"]\n\n'
        '[dependency-groups]\ndev = ["pytest", "ruff", "mypy"]\n\n[tool.ruff]\nline-length = 100\n'
    )
    (git_repo / "uv.lock").write_text("")
    (git_repo / "src" / "demo").mkdir(parents=True)
    (git_repo / "src" / "demo" / "__init__.py").write_text("")
    (git_repo / "src" / "demo" / "main.py").write_text(
        "from fastapi import FastAPI\n\napp = FastAPI()\n"
    )
    (git_repo / "src" / "demo" / "models.py").write_text("class User: ...\n")
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_main.py").write_text("def test_ok():\n    assert True\n")
    (git_repo / "alembic").mkdir()
    (git_repo / "alembic" / "env.py").write_text("")
    (git_repo / ".github" / "workflows").mkdir(parents=True)
    (git_repo / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
    (git_repo / "Dockerfile").write_text("FROM python:3.12\n")
    git("add", ".", cwd=git_repo)
    git("commit", "-qm", "feat: app", cwd=git_repo)
    return git_repo
