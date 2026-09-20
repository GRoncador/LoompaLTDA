"""`loompa setup` (the onboarding wizard), `loompa doctor` and the services checklist behind them.
No network and no real `opencode`/`gh`: probes and binaries are replaced."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from loompa.cli.main import app
from loompa.config import Secrets, apply_preset, default_config
from loompa.config.secrets import hub_secrets_path, read_dotenv
from loompa.config.services import collect_services
from loompa.factory import Factory

runner = CliRunner()
GOOD_KEY = "sk-or-good-key-1234567890"
BAD_KEY = "sk-or-bad-key-1234567890"


def openrouter_config():
    config = default_config()
    apply_preset(config, "openrouter")
    return config


def by_id(services):
    return {s.id: s for s in services}


# ------------------------------------------------------------------------------ the checklist


def test_checklist_names_what_each_service_is_for_and_what_blocks():
    services = collect_services(
        openrouter_config(), Secrets(), which=lambda name: None, gh_ok=lambda: False
    )
    found = by_id(services)
    assert list(found) == ["openrouter", "tavily", "opencode", "github"]
    assert found["openrouter"].blocking and "openrouter.ai/keys" in found["openrouter"].fix
    assert not found["tavily"].blocking and found["tavily"].need == "optional"
    assert not found["opencode"].blocking  # the built-in Worker is in use
    assert not found["github"].blocking and "gh auth login" in found["github"].fix
    assert all(s.purpose for s in services)


def test_checklist_is_ready_with_a_key_and_never_carries_its_value():
    key = "sk-or-secret-value-1234567890"
    services = collect_services(
        openrouter_config(),
        Secrets({"OPENROUTER_API_KEY": key}),
        which=lambda name: None,
        gh_ok=lambda: False,
    )
    assert not any(s.blocking for s in services)
    assert key not in repr(services)


def test_opencode_blocks_only_when_it_is_the_backend_and_is_missing():
    config = openrouter_config()
    secrets = Secrets({"OPENROUTER_API_KEY": GOOD_KEY})
    kwargs = {"gh_ok": lambda: True}
    assert not by_id(collect_services(config, secrets, which=lambda n: None, **kwargs))[
        "opencode"
    ].blocking
    config.worker.backend = "opencode"
    missing = by_id(collect_services(config, secrets, which=lambda n: None, **kwargs))["opencode"]
    assert missing.blocking and "opencode.ai" in missing.fix
    present = by_id(collect_services(config, secrets, which=lambda n: "/bin/opencode", **kwargs))
    assert present["opencode"].ok and not present["opencode"].blocking


def test_coderabbit_and_extra_mcp_servers_appear_only_when_configured():
    config = openrouter_config()
    assert "coderabbit" not in by_id(collect_services(config, Secrets(), gh_ok=lambda: True))
    config.quality.coderabbit.enabled = True
    assert "coderabbit" in by_id(
        collect_services(config, Secrets(), which=lambda n: None, gh_ok=lambda: True)
    )


# --------------------------------------------------------------------------------- the wizard


@pytest.fixture
def probes(monkeypatch: pytest.MonkeyPatch):
    """Connection tests answer from a list (`True` = ok); binaries and GitHub are absent."""
    answers: list[bool] = []
    asked: list[str] = []

    async def fake_probe(f, names):
        asked.extend(names)
        return [
            (n, ok, "tudo certo" if ok else "chave recusada pelo provedor")
            for n in names
            for ok in [answers.pop(0) if answers else True]
        ]

    monkeypatch.setattr("loompa.cli.providers._probe_all", fake_probe)
    monkeypatch.setattr("loompa.cli.setup._probe_all", fake_probe)
    monkeypatch.setattr("loompa.cli.setup.gh_logged_in", lambda: False)
    monkeypatch.setattr("loompa.config.services.gh_logged_in", lambda which=None: False)
    monkeypatch.setattr(shutil, "which", lambda name, *a, **k: None)
    launched: list[str] = []
    monkeypatch.setattr(typer, "launch", lambda url, **k: launched.append(url) or 0)
    return type("Probes", (), {"answers": answers, "asked": asked, "launched": launched})


@pytest.fixture
def demo(hub, brownfield_repo: Path) -> Path:
    assert (
        runner.invoke(app, ["init", str(brownfield_repo), "--yes", "--name", "Demo"]).exit_code == 0
    )
    return brownfield_repo


def test_wizard_takes_the_key_tests_it_and_reports_what_is_left(demo: Path, probes):
    # preset given; open the page? no; the key; Tavily: no page, skip; Worker: 2 (not installed)
    result = runner.invoke(
        app,
        ["setup", "--factory", "demo", "--preset", "openrouter"],
        input=f"n\n{GOOD_KEY}\nn\n\n2\n",
    )
    out = result.stdout
    assert result.exit_code == 0, out
    for step in ("Passo 1/4", "Passo 2/4", "Passo 3/4", "Passo 4/4"):
        assert step in out
    assert probes.asked == ["openrouter"]  # tested right after it was typed
    assert read_dotenv(hub_secrets_path())["OPENROUTER_API_KEY"] == GOOD_KEY
    assert GOOD_KEY not in out  # the prompt is hidden and nothing echoes the value
    assert "https://opencode.ai" in out  # 2 chosen, but not installed: told how to get it
    assert Factory.open(demo).config.worker.backend == "aci"  # and it stays on the built-in one
    assert "Tudo o que é obrigatório está pronto" in out


def test_wizard_offers_to_open_the_page_where_the_key_is_created(demo: Path, probes):
    result = runner.invoke(
        app,
        ["setup", "--factory", "demo", "--preset", "openrouter"],
        input=f"y\n{GOOD_KEY}\nn\n\n1\n",
    )
    assert result.exit_code == 0, result.stdout
    assert probes.launched == ["https://openrouter.ai/keys"]


def test_a_rejected_key_is_wiped_and_asked_again(demo: Path, probes):
    probes.answers.extend([False, True])
    result = runner.invoke(
        app,
        ["setup", "--factory", "demo", "--preset", "openrouter"],
        input=f"n\n{BAD_KEY}\n{GOOD_KEY}\nn\n\n1\n",
    )
    assert result.exit_code == 0, result.stdout
    said = " ".join(result.stdout.split())  # the terminal wraps lines
    assert "recusada" in said and "tente de novo" in said
    assert read_dotenv(hub_secrets_path())["OPENROUTER_API_KEY"] == GOOD_KEY
    assert BAD_KEY not in result.stdout and GOOD_KEY not in result.stdout


def test_skipping_a_key_leaves_it_missing_and_says_how_to_finish(demo: Path, probes):
    result = runner.invoke(
        app, ["setup", "--factory", "demo", "--preset", "openrouter"], input="n\n\nn\n\n1\n"
    )
    assert result.exit_code == 0, result.stdout
    assert "OPENROUTER_API_KEY" not in read_dotenv(hub_secrets_path())
    assert "Falta:" in result.stdout and "loompa setup" in result.stdout


def test_the_wizard_keeps_a_key_that_is_already_there(demo: Path, probes):
    hub_secrets_path().parent.mkdir(parents=True, exist_ok=True)
    hub_secrets_path().write_text(f"OPENROUTER_API_KEY={GOOD_KEY}\n")
    result = runner.invoke(
        app, ["setup", "--factory", "demo", "--preset", "openrouter"], input="\n\n1\n"
    )
    assert result.exit_code == 0, result.stdout
    assert "chave já configurada" in result.stdout and probes.asked == []


# ----------------------------------------------------------------------------------- doctor


def test_doctor_fails_on_a_missing_key_and_passes_once_it_works(demo: Path, probes):
    runner.invoke(app, ["providers", "preset", "openrouter", "--no-keys", "--factory", "demo"])
    missing = runner.invoke(app, ["doctor", "--factory", "demo"])
    assert missing.exit_code == 1 and "loompa setup" in missing.stdout
    hub_secrets_path().parent.mkdir(parents=True, exist_ok=True)
    hub_secrets_path().write_text(f"OPENROUTER_API_KEY={GOOD_KEY}\n")
    ok = runner.invoke(app, ["doctor", "--factory", "demo"])
    assert ok.exit_code == 0 and "Tudo certo" in ok.stdout and probes.asked == ["openrouter"]
    assert GOOD_KEY not in ok.stdout


def test_doctor_fails_when_the_provider_refuses_the_key(demo: Path, probes):
    runner.invoke(app, ["providers", "preset", "openrouter", "--no-keys", "--factory", "demo"])
    hub_secrets_path().parent.mkdir(parents=True, exist_ok=True)
    hub_secrets_path().write_text(f"OPENROUTER_API_KEY={BAD_KEY}\n")
    probes.answers.append(False)
    refused = runner.invoke(app, ["doctor", "--factory", "demo"])
    assert refused.exit_code == 1 and "recusada" in refused.stdout
    assert runner.invoke(app, ["doctor", "--factory", "demo", "--no-test"]).exit_code == 0
