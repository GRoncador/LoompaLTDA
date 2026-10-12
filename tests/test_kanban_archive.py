"""The Concluído column keeps only the cards of the sprint that started last (archive rule)."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import git
from loompa.dashboard.app import create_app
from loompa.factory import bootstrap_factory
from loompa.sprints import Sprint, SprintBoard, SprintStatus


@pytest.fixture
def client(git_repo: Path, hub):
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    git("add", ".", cwd=git_repo)
    git("commit", "-qm", "test: base", cwd=git_repo)
    bootstrap_factory(git_repo, name="Demo HQ", store=hub)
    app = create_app(dry_run=True, run_engine=False, store=hub)
    with TestClient(app) as c:
        yield c


def _store(client: TestClient):
    return client.app.state.hub.get("demo-hq").ctx.store


def _done(client: TestClient, sid: str, title: str) -> None:
    _store(client).upsert_story(
        {"id": sid, "factory": "demo-hq", "title": title, "stage": "DONE", "state": {}}
    )


def _sprint(client: TestClient, sid: str, status: SprintStatus, stories: list[str], started: str | None) -> None:
    _store(client).put_sprint(
        Sprint(
            id=sid,
            factory="demo-hq",
            status=status,
            story_ids=stories,
            started_at=started,
        ).model_dump()
    )


def _column_cards(client: TestClient) -> dict[str, dict[str, bool]]:
    ov = client.get("/api/factories/demo-hq/overview").json()
    return {
        c["key"]: {s["id"]: s["archived"] for s in c["stories"]} for c in ov["columns"]
    }


def test_a_sprint_start_archives_every_card_finished_before_it(client: TestClient):
    assert SprintBoard(_store(client), "demo-hq").latest_started() is None
    _done(client, "S-001", "Entregue antes")
    # no sprint has started yet: the finished card is shown, not archived
    assert _column_cards(client)["DONE"] == {"S-001": False}

    _done(client, "S-002", "Entregue no SP-001")
    _sprint(client, "SP-001", SprintStatus.CLOSED, ["S-001", "S-002"], "2026-10-01T09:00:00+00:00")
    _done(client, "S-003", "Sem sprint")
    # the sprint that started last stays visible after it closes, until the next one starts;
    # a finished card in no sprint is not in it, so it is archived
    assert SprintBoard(_store(client), "demo-hq").latest_started().id == "SP-001"
    assert _column_cards(client)["DONE"] == {"S-001": False, "S-002": False, "S-003": True}

    _sprint(client, "SP-002", SprintStatus.RUNNING, ["S-004"], "2026-10-05T09:00:00+00:00")
    _done(client, "S-004", "Entregue no SP-002")
    cards = _column_cards(client)["DONE"]
    assert cards == {"S-001": True, "S-002": True, "S-003": True, "S-004": False}
