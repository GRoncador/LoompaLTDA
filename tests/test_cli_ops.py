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
    assert "S-001" in r.stdout and "S-002" in r.stdout and "loompa sprint start" in r.stdout
    r = runner.invoke(app, ["run", "--dry-run"])  # the backlog waits for a sprint
    assert r.exit_code == 0 and "0 histórias processadas" in r.stdout, r.stdout
    r = runner.invoke(app, ["sprint", "start", "--dry-run", "--goal", "Primeira entrega"])
    assert r.exit_code == 0 and "SP-001 iniciado" in r.stdout, r.stdout
    r = runner.invoke(app, ["sprint", "start", "--dry-run"])  # nothing left to start
    assert r.exit_code == 1 and "não há histórias" in r.stdout
    r = runner.invoke(app, ["run", "--dry-run"])
    assert r.exit_code == 0, r.stdout
    assert "2 histórias processadas" in r.stdout
    r = runner.invoke(app, ["sprint", "status"])
    assert r.exit_code == 0 and "SP-001" in r.stdout and "rodando" in r.stdout
    assert "aguardando você" in r.stdout and "Primeira entrega" in r.stdout
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


def test_ask_support_agents(git_repo: Path, hub, monkeypatch, tmp_path: Path):
    monkeypatch.chdir(git_repo)
    assert runner.invoke(app, ["init", ".", "--yes", "--name", "Ask"]).exit_code == 0
    out = tmp_path / "policy.md"
    r = runner.invoke(
        app,
        [
            "ask",
            "compliance",
            "política de privacidade para app de finanças",
            "--dry-run",
            "--out",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.stdout
    assert out.read_text().startswith("# compliance:") and "Resposta simulada" in out.read_text()
    r = runner.invoke(app, ["ask", "metrics", "churn mensal", "--dry-run"])
    assert r.exit_code == 0 and "SELECT COUNT" in r.stdout
    assert runner.invoke(app, ["ask", "hacker", "x", "--dry-run"]).exit_code == 1
    r = runner.invoke(app, ["inbox", "list"])
    assert "INFO" in r.stdout and "Compliance Loompa" in r.stdout
