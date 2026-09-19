"""Fase 0b: keys live in secrets files, never in config.yaml, logs, events or API replies."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest
import respx
from fastapi.testclient import TestClient

from conftest import git
from loompa.config import (
    SecretInConfigError,
    Secrets,
    apply_preset,
    default_config,
    looks_like_secret,
    mask,
    save_config,
)
from loompa.config.secrets import (
    install_log_redaction,
    parse_dotenv,
    write_dotenv_value,
)
from loompa.config.settings import SettingsPatch, apply_settings, describe_settings, store_key
from loompa.dashboard.app import create_app
from loompa.factory import bootstrap_factory
from loompa.llm import probe_provider

FAKE_GEMINI = "AIzaSyFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE1234"
FAKE_TAVILY = "tvly-dev-fakefakefakefakefakefake5678"


def test_looks_like_secret_and_mask():
    assert (
        looks_like_secret(FAKE_GEMINI)
        and looks_like_secret("sk-ant-abc")
        and looks_like_secret(FAKE_TAVILY)
    )
    assert (
        not looks_like_secret("GEMINI_API_KEY")
        and not looks_like_secret("tier2")
        and not looks_like_secret("")
    )
    assert mask(FAKE_GEMINI) == "configurada (…1234)" and mask("") == "não configurada"
    assert FAKE_GEMINI not in mask(FAKE_GEMINI)


def test_dotenv_roundtrip_and_permissions(tmp_path: Path):
    path = tmp_path / "secrets.env"
    write_dotenv_value(path, "GEMINI_API_KEY", FAKE_GEMINI)
    write_dotenv_value(path, "TAVILY_API_KEY", FAKE_TAVILY)
    assert parse_dotenv(path.read_text()) == {
        "GEMINI_API_KEY": FAKE_GEMINI,
        "TAVILY_API_KEY": FAKE_TAVILY,
    }
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    write_dotenv_value(path, "GEMINI_API_KEY", None)
    assert parse_dotenv(path.read_text()) == {"TAVILY_API_KEY": FAKE_TAVILY}
    assert parse_dotenv('export A="x y" # c\n# comment\nB=1 # c\nbad line\n') == {
        "A": "x y",
        "B": "1",
    }
    with pytest.raises(ValueError):
        write_dotenv_value(path, "not a name", "x")


def test_secrets_layering_factory_overrides_hub(tmp_path: Path, hub, monkeypatch):
    root = tmp_path / "repo"
    (root / ".loompa").mkdir(parents=True)
    write_dotenv_value(hub.home / "secrets.env", "GEMINI_API_KEY", "hub-key-0000000000")
    write_dotenv_value(hub.home / "secrets.env", "DEEPSEEK_API_KEY", "hub-deepseek-000000")
    write_dotenv_value(root / ".loompa" / ".env", "GEMINI_API_KEY", "factory-key-11111111")
    monkeypatch.setenv("GROQ_API_KEY", "env-groq-2222222222")
    s = Secrets.load(root)
    assert s["GEMINI_API_KEY"] == "factory-key-11111111" and s.source("GEMINI_API_KEY") == "factory"
    assert s["DEEPSEEK_API_KEY"].startswith("hub-") and s.source("DEEPSEEK_API_KEY") == "hub"
    assert s.get("GROQ_API_KEY") == "env-groq-2222222222" and s.source("GROQ_API_KEY") == "env"
    assert s.get("NOPE") is None and s.status("NOPE")["configured"] is False


def test_save_config_refuses_secret_values(tmp_path: Path):
    cfg = default_config()
    cfg.providers["gemini"].extra_headers["x-goog-api-key"] = FAKE_GEMINI
    with pytest.raises(SecretInConfigError, match="extra_headers"):
        save_config(tmp_path, cfg)
    with pytest.raises(ValueError):
        cfg.providers["gemini"].api_key_env = FAKE_GEMINI
    cfg.providers["gemini"].extra_headers.clear()
    save_config(tmp_path, cfg)
    assert "AIza" not in (tmp_path / ".loompa" / "config.yaml").read_text()


def test_log_redaction_masks_values(caplog):
    install_log_redaction([FAKE_GEMINI])
    try:
        with caplog.at_level(logging.INFO):
            logging.getLogger("loompa.test").info("bearer %s failed", FAKE_GEMINI)
        assert FAKE_GEMINI not in caplog.text and "…1234" in caplog.text
    finally:
        install_log_redaction([])


def test_presets_and_settings_patch(tmp_path: Path, hub):
    root = tmp_path / "repo"
    (root / ".loompa").mkdir(parents=True)
    cfg = default_config()
    preset = apply_preset(cfg, "gratuito")
    assert cfg.models.tiers["tier2"][0].model == "gemini-3.5-flash-lite" and preset.providers == (
        "gemini",
    )
    notes = apply_settings(
        root,
        cfg,
        SettingsPatch.model_validate(
            {
                "providers": {"gemini": {"api_key": FAKE_GEMINI, "scope": "factory"}},
                "roles": {"analyst": "tier1"},
                "budget": {"monthly_cap_usd": 12},
                "tools": {"tavily": {"api_key": FAKE_TAVILY}},
            }
        ),
    )
    assert any("gemini" in n for n in notes)
    secrets = Secrets.load(root, home=hub.home)
    assert (
        secrets.source("GEMINI_API_KEY") == "factory" and secrets.source("TAVILY_API_KEY") == "hub"
    )
    assert cfg.models.tier_for("analyst") == "tier1" and cfg.budget.monthly_cap_usd == 12
    view = describe_settings(cfg, secrets)
    text = repr(view)
    assert FAKE_GEMINI not in text and FAKE_TAVILY not in text
    gem = next(p for p in view["providers"] if p["name"] == "gemini")
    assert gem["key"]["label"] == "configurada (…1234)" and gem["key"]["source"] == "factory"
    assert view["tools"]["tavily"]["key"]["configured"] is True
    with pytest.raises(ValueError):
        apply_settings(root, cfg, SettingsPatch.model_validate({"roles": {"worker": "tier9"}}))
    with pytest.raises(ValueError):
        apply_settings(
            root,
            cfg,
            SettingsPatch.model_validate({"providers": {"x": {"api_key_env": FAKE_GEMINI}}}),
        )
    # config.yaml written from this config still has no key in it
    save_config(root, cfg)
    assert "AIza" not in (root / ".loompa" / "config.yaml").read_text()
    store_key(root, "GEMINI_API_KEY", None, scope="factory")
    assert Secrets.load(root, home=hub.home).source("GEMINI_API_KEY") is None


@respx.mock
async def test_probe_provider_reads_key_from_secrets(tmp_path: Path, hub):
    cfg = default_config()
    secrets = Secrets({"GEMINI_API_KEY": FAKE_GEMINI}, {"GEMINI_API_KEY": "hub"})
    route = respx.post(
        "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
    ).respond(
        200,
        json={
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 1},
        },
    )
    r = await probe_provider(cfg, "gemini", secrets=secrets)
    assert r.ok and r.model == "gemini-3.5-flash-lite" and "ok" in r.detail
    assert route.calls[0].request.headers["authorization"] == f"Bearer {FAKE_GEMINI}"
    assert FAKE_GEMINI not in repr(r.as_dict())
    r2 = await probe_provider(cfg, "gemini", secrets=Secrets())
    assert not r2.ok and "não configurada" in r2.detail
    respx.post("https://api.deepseek.com/v1/chat/completions").respond(401, json={"error": "bad"})
    r3 = await probe_provider(cfg, "deepseek", secrets=Secrets({"DEEPSEEK_API_KEY": "sk-bad"}))
    assert not r3.ok and "recusada" in r3.detail


@pytest.fixture
def client(git_repo: Path, hub):
    git("commit", "-qm", "base", "--allow-empty", cwd=git_repo)
    bootstrap_factory(git_repo, name="Keys", store=hub)
    app = create_app(dry_run=True, run_engine=False, store=hub)
    with TestClient(app) as c:
        yield c, git_repo


def test_settings_api_never_returns_keys(client, hub):
    c, root = client
    r = c.put(
        "/api/factories/keys/settings",
        json={
            "preset": "economico",
            "providers": {"deepseek": {"api_key": "sk-fakefakefakefakefake0001"}},
            "tools": {"tavily": {"api_key": FAKE_TAVILY, "scope": "factory"}},
            "roles": {"product_owner": "tier1"},
        },
    )
    assert r.status_code == 200, r.text
    body = r.text
    assert "sk-fake" not in body and FAKE_TAVILY not in body
    st = r.json()["settings"]
    assert st["preset"] == "economico" and st["roles"]["product_owner"] == "tier1"
    ds = next(p for p in st["providers"] if p["name"] == "deepseek")
    assert ds["key"]["configured"] and ds["key"]["label"].endswith("(…0001)")
    assert st["tools"]["tavily"]["key"]["source"] == "factory"
    # persisted where it should be, and nowhere else
    assert "sk-fake" not in (root / ".loompa" / "config.yaml").read_text()
    assert "sk-fake" in (hub.home / "secrets.env").read_text()
    assert FAKE_TAVILY in (root / ".loompa" / ".env").read_text()
    assert ".loompa/.env" in (root / ".gitignore").read_text()
    events = c.get("/api/factories/keys/events?after=0").text
    assert "sk-fake" not in events and FAKE_TAVILY not in events
    # the first preset is the one onboarding offers by default
    assert c.get("/api/factories/keys/settings").json()["presets"][0]["key"] == "openrouter"
    assert (
        c.put("/api/factories/keys/settings", json={"roles": {"worker": "nope"}}).status_code == 400
    )
    # provider test without network: unknown provider / missing key answers in plain language
    r = c.post("/api/factories/keys/settings/providers/gemini/test")
    assert r.status_code == 200 and r.json()["ok"] is False and "chave" in r.json()["detail"]
    agent = c.get("/api/factories/keys/agents/Product Owner Loompa").json()
    assert agent["tier"] == "tier1" and agent["candidates"]  # role set to tier1 above
    agent = c.get("/api/factories/keys/agents/Novo Loompa").json()
    assert agent["tier"] == "tier2"  # unknown agent names still map to tier2
    agent = c.get("/api/factories/keys/agents/Master Loompa").json()
    assert agent["tier"] == "tier1" and agent["candidates"][0]["model"] == "deepseek-reasoner"


def test_repo_never_ships_secrets():
    """The committed sources, defaults and the dashboard bundle contain no key-shaped values."""
    root = Path(__file__).resolve().parents[1]
    pattern = re.compile(
        r"(sk-(ant-|or-)?[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_\-]{30,}|tvly-[A-Za-z0-9\-]{20,}|gsk_[A-Za-z0-9]{20,})"
    )
    files = list((root / "src" / "loompa").rglob("*.yaml")) + list(
        (root / "src" / "loompa" / "dashboard" / "static").rglob("*.js")
    )
    files += [p for p in (root / "src" / "loompa").rglob("*.py")]
    for path in files:
        assert not pattern.search(path.read_text(encoding="utf-8", errors="ignore")), path
