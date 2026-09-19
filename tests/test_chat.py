"""Conversations through the dashboard API and `loompa chat` (dry-run, no network)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from typer.testing import CliRunner

from loompa.cli.main import app
from test_dashboard import client  # noqa: F401

BASE = "/api/factories/demo-hq"
runner = CliRunner()


def column(overview: dict, key: str) -> list:
    return next(c for c in overview["columns"] if c["key"] == key)["stories"]


# ------------------------------------------------------------------------------- API


def test_a_sprint_meeting_over_the_api(client: TestClient):
    r = client.post(
        f"{BASE}/conversations", json={"kind": "meeting", "text": "Tela de login; Exportar CSV"}
    )
    assert r.status_code == 200
    body = r.json()
    cid = body["conversation"]["id"]
    assert cid == "C-001" and body["turn"]["reply"] and len(body["turn"]["changes"]) == 2
    assert [i["key"] for i in body["conversation"]["draft"]["items"]] == ["D1", "D2"]

    ov = client.get(f"{BASE}/overview").json()  # the drafts live in the session, not the board
    assert all(c["stories"] == [] for c in ov["columns"]) and ov["sprint"] is None
    assert [c["id"] for c in ov["conversations"]] == [cid] and ov["conversations"][0]["cards"] == 2

    r = client.post(
        f"{BASE}/conversations/{cid}/draft",
        json={
            "ops": [
                {"op": "update", "ref": "D2", "in_sprint": False},
                {"op": "goal", "text": "Meta"},
            ]
        },
    )
    draft = r.json()["conversation"]["draft"]
    assert r.json()["report"]["changes"] and draft["goal"] == "Meta"
    assert [i["in_sprint"] for i in draft["items"]] == [True, False]

    r = client.post(f"{BASE}/conversations/{cid}/messages", json={"text": "Relatório mensal"})
    assert r.status_code == 200 and len(r.json()["conversation"]["draft"]["items"]) == 3
    assert [t["who"] for t in r.json()["conversation"]["turns"]] == ["founder", "agent"] * 2

    r = client.post(f"{BASE}/conversations/{cid}/commit", json={"start_sprint": True, "run": False})
    assert r.status_code == 200
    result = r.json()["result"]
    assert result["created"] == ["S-001", "S-002", "S-003"] and result["sprint_id"] == "SP-001"
    assert r.json()["conversation"]["status"] == "committed"
    ov = client.get(f"{BASE}/overview").json()
    assert ov["sprint"]["id"] == "SP-001" and ov["sprint"]["goal"] == "Meta"
    assert ov["sprint"]["story_ids"] == ["S-001", "S-003"]  # D2 stayed out of the sprint
    assert [s["id"] for s in column(ov, "BACKLOG")] == ["S-002"] and ov["conversations"] == []

    assert (
        client.post(f"{BASE}/conversations/{cid}/messages", json={"text": "x"}).status_code == 409
    )
    assert client.get(f"{BASE}/conversations/{cid}").json()["conversation"]["result"]["created"]
    assert client.get(f"{BASE}/conversations?status=open").json() == []
    assert [c["id"] for c in client.get(f"{BASE}/conversations").json()] == [cid]


def test_a_brainstorm_over_the_api_ends_in_the_backlog(client: TestClient):
    r = client.post(
        f"{BASE}/conversations", json={"kind": "brainstorm", "text": "Ideias de onboarding"}
    )
    conv = r.json()["conversation"]
    assert conv["kind"] == "brainstorm" and conv["limits"]  # web search declared missing
    assert conv["turns"][1]["name"] == "Analyst Loompa"
    r = client.post(f"{BASE}/conversations/{conv['id']}/commit", json={})
    assert r.status_code == 200 and r.json()["result"]["created"] == ["S-001"]
    assert r.json()["conversation"]["status"] == "committed"
    ov = client.get(f"{BASE}/overview").json()
    assert [s["origin"] for s in column(ov, "BACKLOG")] == ["brainstorm"]
    # a brainstorm cannot start a sprint
    other = client.post(f"{BASE}/conversations", json={"kind": "brainstorm", "text": "outra"})
    r = client.post(
        f"{BASE}/conversations/{other.json()['conversation']['id']}/commit",
        json={"start_sprint": True},
    )
    assert r.status_code == 409 and "só uma reunião" in r.json()["detail"]


def test_conversation_errors_are_plain_http_errors(client: TestClient):
    assert client.get(f"{BASE}/conversations/C-404").status_code == 404
    assert (
        client.post(f"{BASE}/conversations/C-404/messages", json={"text": "oi"}).status_code == 404
    )
    assert client.post(f"{BASE}/conversations", json={"kind": "outra"}).status_code == 422
    cid = client.post(f"{BASE}/conversations", json={"kind": "meeting"}).json()["conversation"][
        "id"
    ]
    assert (
        client.post(f"{BASE}/conversations/{cid}/messages", json={"text": "  "}).status_code == 409
    )
    r = client.post(f"{BASE}/conversations/{cid}/commit", json={})
    assert r.status_code == 409 and "vazio" in r.json()["detail"]
    r = client.post(f"{BASE}/conversations/{cid}/discard")
    assert r.status_code == 200 and r.json()["conversation"]["status"] == "discarded"
    assert client.post(f"{BASE}/conversations/{cid}/discard").status_code == 409


def test_the_morning_meeting_endpoint_keeps_its_shape(client: TestClient):
    r = client.post(f"{BASE}/meeting", json={"goals": "Tela de login; Exportar CSV"})
    assert [s["id"] for s in r.json()["stories"]] == ["S-001", "S-002"]
    history = client.get(f"{BASE}/conversations").json()
    assert [(c["id"], c["status"]) for c in history] == [("C-001", "committed")]


# ------------------------------------------------------------------------------- CLI


def init_factory(git_repo: Path, monkeypatch) -> None:
    monkeypatch.chdir(git_repo)
    assert runner.invoke(app, ["init", ".", "--yes", "--name", "Chat"]).exit_code == 0


def test_chat_meeting_starts_a_sprint_from_the_terminal(git_repo: Path, hub, monkeypatch):
    init_factory(git_repo, monkeypatch)
    r = runner.invoke(
        app,
        ["chat", "meeting", "Cadastro de clientes; Relatório mensal", "--dry-run"],
        input="/tirar D2\n/meta Primeira entrega\n/rascunho\n/sprint\n",
    )
    assert r.exit_code == 0, r.stdout
    assert "Master Loompa" in r.stdout and "Cadastro de clientes" in r.stdout
    assert "tirei “Relatório mensal” do rascunho" in r.stdout
    assert "SP-001 iniciado" in r.stdout and "loompa run" in r.stdout
    r = runner.invoke(app, ["sprint", "status"])
    assert "SP-001" in r.stdout and "Primeira entrega" in r.stdout and "S-001" in r.stdout
    assert "S-002" not in r.stdout  # the dropped card never became a story
    r = runner.invoke(app, ["chat", "list", "--all"])
    assert "salva" in r.stdout and "C-001" in r.stdout


def test_a_chat_can_be_left_and_resumed(git_repo: Path, hub, monkeypatch):
    init_factory(git_repo, monkeypatch)
    r = runner.invoke(app, ["chat", "meeting", "Login; CSV", "--dry-run"], input="/sair\n")
    assert r.exit_code == 0 and "loompa chat resume C-001" in r.stdout, r.stdout
    r = runner.invoke(app, ["chat", "list"])
    assert "C-001" in r.stdout and "aberta" in r.stdout
    r = runner.invoke(
        app, ["chat", "resume", "C-001", "--dry-run"], input="/incluir D1\n/xyz\n/backlog\n"
    )
    assert r.exit_code == 0, r.stdout
    assert "Login" in r.stdout and "Comando desconhecido" in r.stdout
    assert "backlog atualizado (S-001, S-002)" in r.stdout
    r = runner.invoke(app, ["chat", "resume", "C-001", "--dry-run"])
    assert r.exit_code == 1 and "já foi encerrada" in r.stdout
    r = runner.invoke(app, ["chat", "list"])
    assert "Nenhuma conversa em aberto" in r.stdout


def test_ending_the_input_keeps_the_session(git_repo: Path, hub, monkeypatch):
    init_factory(git_repo, monkeypatch)
    r = runner.invoke(app, ["chat", "meeting", "Login", "--dry-run"])  # no more input: Ctrl-D
    assert r.exit_code == 0 and "Conversa guardada" in r.stdout, r.stdout
    assert "C-001" in runner.invoke(app, ["chat", "list"]).stdout


def test_chat_brainstorm_sends_ideas_through_the_product_owner(git_repo: Path, hub, monkeypatch):
    init_factory(git_repo, monkeypatch)
    r = runner.invoke(
        app,
        ["chat", "brainstorm", "Como melhorar o onboarding?", "--dry-run"],
        input="/sprint\n/backlog\n",
    )
    assert r.exit_code == 0, r.stdout
    assert "Analyst Loompa" in r.stdout and "simulação" in r.stdout  # the missing web is declared
    assert "Comando desconhecido" in r.stdout  # a brainstorm has no /sprint
    assert "backlog atualizado (S-001)" in r.stdout
    r = runner.invoke(app, ["status"])
    assert r.exit_code == 0 and "Kanban" in r.stdout


def test_discarding_saves_nothing(git_repo: Path, hub, monkeypatch):
    init_factory(git_repo, monkeypatch)
    r = runner.invoke(app, ["chat", "meeting", "Login", "--dry-run"], input="/descartar\n")
    assert r.exit_code == 0 and "nada foi salvo" in r.stdout
    assert "Nenhuma conversa" in runner.invoke(app, ["chat", "list"]).stdout
    assert "Nenhum sprint" in runner.invoke(app, ["sprint", "status"]).stdout
