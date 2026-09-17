from pathlib import Path

from typer.testing import CliRunner

from conftest import git
from loompa.cli.main import app

runner = CliRunner()


def test_meeting_run_inbox_dry_run(git_repo: Path, hub, monkeypatch):
    monkeypatch.chdir(git_repo)
    assert runner.invoke(app, ["init", ".", "--yes", "--name", "Ops"]).exit_code == 0
    r = runner.invoke(app, ["meeting", "Cadastro de clientes; Relatório mensal", "--dry-run"])
    assert r.exit_code == 0, r.stdout
    assert "S-001" in r.stdout and "S-002" in r.stdout
    r = runner.invoke(app, ["run", "--dry-run"])
    assert r.exit_code == 0, r.stdout
    assert "2 histórias processadas" in r.stdout
    r = runner.invoke(app, ["inbox", "list"])
    assert r.exit_code == 0 and "DELIVERY" in r.stdout and "S-001" in r.stdout
    msg_id = r.stdout.split("DELIVERY · ")[1].split(" ")[0]
    r = runner.invoke(app, ["inbox", "reply", msg_id, "--option", "approve", "--dry-run"])
    assert r.exit_code == 0 and "DONE" in r.stdout
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0 and "Kanban" in r.stdout
    r = runner.invoke(app, ["report"])
    assert r.exit_code == 0 and "Resumo do dia" in r.stdout
    r = runner.invoke(app, ["kaizen"])
    assert r.exit_code == 0
    r = runner.invoke(app, ["memory", "search", "constituição"])
    assert r.exit_code == 0
    assert git("log", "--oneline", cwd=git_repo).count("\n") >= 1
