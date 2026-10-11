"""Plan 16.1: a factory is personal or commercial, asked at the opening and changeable later."""

from pathlib import Path

import yaml
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from loompa.cli.main import app
from loompa.config import load_config
from loompa.config.settings import SettingsPatch, apply_settings
from loompa.dashboard.app import create_app
from loompa.factory import bootstrap_factory
from loompa.factory_kind import parse_kind, profile

runner = CliRunner()


def test_a_factory_made_before_the_question_loads_as_personal(git_repo: Path, hub):
    bootstrap_factory(git_repo, name="Antiga", store=hub)
    cfg_path = git_repo / ".loompa" / "config.yaml"
    raw = yaml.safe_load(cfg_path.read_text())
    raw["factory"].pop("kind", None)
    cfg_path.write_text(yaml.safe_dump(raw))
    cfg = load_config(git_repo)
    assert cfg.factory.kind == "personal"
    p = profile(cfg)
    assert p.phases == ("in_use",) and not p.market_discovery and not p.growth
    assert p.design_gate == "light"


def test_the_kind_is_asked_at_init_and_kept_on_a_second_init(tmp_path: Path, hub):
    root = tmp_path / "loja"
    r = runner.invoke(app, ["init", str(root), "--yes", "--name", "Loja", "--kind", "comercial"])
    assert r.exit_code == 0, r.stdout
    assert load_config(root).factory.kind == "commercial"
    assert profile(load_config(root)).design_gate == "full"
    r = runner.invoke(app, ["init", str(root), "--yes", "--name", "Loja"])
    assert r.exit_code == 0 and load_config(root).factory.kind == "commercial"
    r = runner.invoke(app, ["init", str(root), "--yes", "--kind", "startup"])
    assert r.exit_code != 0


def test_the_kind_is_changed_in_the_settings(git_repo: Path, hub):
    cfg = bootstrap_factory(git_repo, name="Casa", store=hub).factory.config
    notes = apply_settings(git_repo, cfg, SettingsPatch(factory_kind="commercial"))
    assert cfg.factory.kind == "commercial" and notes == ["fábrica comercial"]
    assert apply_settings(git_repo, cfg, SettingsPatch(factory_kind="commercial")) == []
    try:
        apply_settings(git_repo, cfg, SettingsPatch(factory_kind="ong"))
    except ValueError as exc:
        assert "pessoal ou comercial" in str(exc)
    else:
        raise AssertionError("an unknown kind must be refused")


def test_parse_kind_takes_both_languages():
    assert parse_kind("Comercial") == "commercial"
    assert parse_kind(" personal ") == "personal"
    assert parse_kind("pessoal") == "personal"
    assert parse_kind("") is None and parse_kind(None) is None


def test_the_dashboard_creates_and_switches_the_kind(git_repo: Path, tmp_path: Path, hub):
    bootstrap_factory(git_repo, name="Demo", store=hub)
    with TestClient(create_app(dry_run=True, run_engine=False, store=hub)) as c:
        folder = tmp_path / "nova"
        r = c.post(
            "/api/factories", json={"path": str(folder), "name": "Nova", "kind": "commercial"}
        )
        assert r.status_code == 200
        slug = r.json()["slug"]
        view = c.get(f"/api/factories/{slug}/settings").json()["factory"]
        assert view["kind"] == "commercial" and {k["key"] for k in view["kinds"]} == {
            "personal",
            "commercial",
        }
        r = c.put(f"/api/factories/{slug}/settings", json={"factory_kind": "personal"})
        assert r.status_code == 200 and r.json()["changes"] == ["fábrica pessoal"]
        assert load_config(folder).factory.kind == "personal"
        r = c.post("/api/factories", json={"path": str(tmp_path / "x"), "kind": "ong"})
        assert r.status_code == 400
    assert not (tmp_path / "x").exists()  # refused before anything is created
