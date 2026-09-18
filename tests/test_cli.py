from pathlib import Path

from typer.testing import CliRunner

from loompa.cli.main import app

runner = CliRunner()


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0 and "loompa-core" in result.stdout


def test_init_and_status_and_factories(tmp_path: Path, hub, brownfield_repo: Path):
    result = runner.invoke(app, ["init", str(brownfield_repo), "--yes", "--name", "Demo App"])
    assert result.exit_code == 0, result.stdout
    assert "brownfield" in result.stdout and "FastAPI" in result.stdout
    result = runner.invoke(app, ["factories", "list"])
    assert "demo-app" in result.stdout
    result = runner.invoke(app, ["status", "--factory", "demo-app"])
    assert result.exit_code == 0 and "uv run pytest -q" in result.stdout
    result = runner.invoke(app, ["factories", "use", "nope"])
    assert result.exit_code == 1
    result = runner.invoke(app, ["factories", "remove", "demo-app"])
    assert result.exit_code == 0
    result = runner.invoke(app, ["status", "--factory", "demo-app"])
    assert result.exit_code == 2


def test_init_greenfield_with_stack(tmp_path: Path, hub):
    root = tmp_path / "fresh"
    result = runner.invoke(
        app, ["init", str(root), "--yes", "--stack", "python-fastapi", "--name", "Fresh"]
    )
    assert result.exit_code == 0, result.stdout
    assert (root / ".loompa" / "constitution.md").is_file()
    assert (root / "src" / "fresh" / "main.py").is_file()


def test_init_preset_and_providers_commands(git_repo, hub, monkeypatch):
    from typer.testing import CliRunner

    from loompa.cli.main import app

    runner = CliRunner()
    monkeypatch.chdir(git_repo)
    r = runner.invoke(app, ["init", ".", "--yes", "--name", "Chaves", "--preset", "gratuito"])
    assert r.exit_code == 0, r.stdout
    assert "preset gratuito aplicado" in r.stdout
    r = runner.invoke(app, ["providers", "list"])
    assert r.exit_code == 0 and "Google Gemini" in r.stdout and "não configurada" in r.stdout
    # hidden prompt writes to the hub secrets file, never to config.yaml or stdout
    key = "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE9999"
    r = runner.invoke(app, ["providers", "set-key", "gemini", "--no-test"], input=key + "\n")
    assert r.exit_code == 0, r.stdout
    assert key not in r.stdout and "guardada" in r.stdout
    assert key in (hub.home / "secrets.env").read_text()
    assert key not in (git_repo / ".loompa" / "config.yaml").read_text()
    r = runner.invoke(app, ["providers", "list"])
    assert "configurada (…9999)" in r.stdout and key not in r.stdout
    r = runner.invoke(app, ["providers", "set-key", "gemini", "--clear"])
    assert r.exit_code == 0 and key not in (hub.home / "secrets.env").read_text()
    r = runner.invoke(app, ["init", ".", "--yes", "--preset", "nope"])
    assert r.exit_code == 1
