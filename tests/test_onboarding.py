from pathlib import Path

from loompa.factory import bootstrap_factory
from loompa.onboarding import (
    BrownfieldScanner,
    GreenfieldInitializer,
    detect_mode,
    executive_onboarding_summary,
)


def test_detect_mode(tmp_path: Path, git_repo: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    assert detect_mode(empty) == "greenfield"
    (empty / "README.md").write_text("# hi")
    assert detect_mode(empty) == "greenfield"  # a README alone is not a project
    (empty / "main.py").write_text("print(1)")
    assert detect_mode(empty) == "brownfield"
    assert detect_mode(git_repo) == "brownfield"
    assert detect_mode(tmp_path / "missing") == "greenfield"


def test_brownfield_scanner_detects_stack(brownfield_repo: Path):
    audit = BrownfieldScanner(brownfield_repo).scan()
    assert audit.primary_language == "Python"
    assert "FastAPI" in audit.stack.frameworks and "SQLAlchemy" in audit.stack.frameworks
    assert "PostgreSQL" in audit.stack.databases
    assert "pytest" in audit.stack.test_frameworks
    assert {"ruff", "mypy"} <= set(audit.stack.linters)
    assert audit.stack.package_managers == ["uv"]
    assert audit.suggested_commands["test"] == "uv run pytest -q"
    assert audit.suggested_commands["lint"] == "uv run ruff check ."
    assert audit.suggested_commands["typecheck"] == "uv run mypy ."
    assert audit.test_file_count == 1 and audit.test_dirs == ["tests"]
    assert audit.ci_files and "GitHub Actions" in audit.stack.ci
    assert audit.docker == ["Dockerfile"]
    assert any("alembic" in m for m in audit.migrations)
    assert any("models.py" in s for s in audit.schemas)
    assert audit.git.is_repo and audit.git.commits == 2 and audit.git.default_branch == "main"
    summary = executive_onboarding_summary(audit, "Demo")
    assert "Relatório de Onboarding" in summary and "FastAPI" in summary


def test_scanner_handles_package_json(tmp_path: Path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "vitest run", "lint": "eslint ."}, "dependencies": {"react": "18"}, "devDependencies": {"vitest": "2", "typescript": "5", "eslint": "9"}}'
    )
    (tmp_path / "pnpm-lock.yaml").write_text("")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.test.tsx").write_text("")
    audit = BrownfieldScanner(tmp_path).scan()
    assert "React" in audit.stack.frameworks and "Vitest" in audit.stack.test_frameworks
    assert audit.suggested_commands == {
        "test": "pnpm run test",
        "lint": "pnpm run lint",
        "typecheck": "npx tsc --noEmit",
    }
    assert audit.test_file_count == 1


def test_greenfield_initializer_writes_skeleton(tmp_path: Path):
    init = GreenfieldInitializer(
        tmp_path, name="Bolos", slug="bolos", preset="python-fastapi", mission="vender bolos"
    )
    assert init.ensure_git() is True
    assert init.ensure_git() is False
    files = init.write_skeleton()
    assert (tmp_path / "src" / "bolos" / "main.py").is_file()
    assert (tmp_path / "tests" / "test_health.py").read_text().startswith("from fastapi.testclient")
    assert len(files) == 5
    assert init.write_skeleton() == []  # idempotent
    assert "FastAPI" in init.constitution() and "vender bolos" in init.constitution()


def test_bootstrap_greenfield_creates_loompa_dir_and_registers(tmp_path: Path, hub):
    root = tmp_path / "novo"
    result = bootstrap_factory(
        root, name="Novo Produto", preset="python-cli", mission="m", store=hub
    )
    assert result.mode == "greenfield" and result.created_git
    p = result.factory.paths
    assert (
        p.config.is_file()
        and p.constitution.is_file()
        and p.learnings.is_file()
        and p.onboarding_report.is_file()
    )
    assert result.factory.config.quality.test_command == "uv run pytest -q"
    assert result.factory.config.stack.frameworks == ["Typer"]
    assert hub.load().get("novo-produto").path == root.resolve()
    assert ".loompa/worktrees/" in (root / ".gitignore").read_text()


def test_bootstrap_brownfield_uses_audit(brownfield_repo: Path, hub):
    (brownfield_repo / ".gitignore").write_text("*.pyc\n")
    result = bootstrap_factory(brownfield_repo, store=hub)
    assert result.mode == "brownfield" and result.audit is not None and not result.created_git
    cfg = result.factory.config
    assert cfg.quality.test_command == "uv run pytest -q"
    assert "FastAPI" in cfg.stack.frameworks
    assert "- fastapi" in result.factory.constitution_text()
    assert result.factory.paths.audit_json.is_file()
    gi = (brownfield_repo / ".gitignore").read_text()
    assert gi.startswith("*.pyc") and ".loompa/worktrees/" in gi
    # re-running keeps the hand-edited constitution
    result.factory.paths.constitution.write_text("# edited\n")
    bootstrap_factory(brownfield_repo, store=hub)
    assert result.factory.constitution_text() == "# edited\n"
