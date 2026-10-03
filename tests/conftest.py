from __future__ import annotations

import getpass
import os
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from loompa.config import ConfigStore, default_config

# Cheapest sensible model per provider for the live smoke test.
LIVE_DEFAULT_MODEL = {
    "gemini": "gemini-3.5-flash-lite",
    "deepseek": "deepseek-chat",
    "openrouter": "~z-ai/glm-flash-latest",
    "anthropic": "claude-haiku-4-5",
    "openai": "gpt-4.1-mini",
    "xai": "grok-4-fast",
}


def _key_env_names() -> tuple[str, ...]:
    cfg = default_config()
    named = [p.api_key_env for p in cfg.providers.values()]
    named += [s.api_key_env for s in cfg.tools.servers().values()]
    return tuple(sorted({n for n in named if n}))


_KEY_ENV_NAMES = _key_env_names()


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="run tests marked `live` against a real provider. Provider, model and API key are "
        "asked in the terminal (hidden input) unless --live-provider/--live-model and the "
        "provider's env var are given. Nothing is written to disk. Never used in CI.",
    )
    parser.addoption("--live-provider", default=None, help="provider name (default: gemini)")
    parser.addoption("--live-model", default=None, help="model id (default: cheapest of provider)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--live"):
        return
    skip = pytest.mark.skip(reason="live provider test: pass --live (and configure a key)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


@dataclass
class LiveCredentials:
    provider: str
    model: str
    api_key_env: str
    api_key: str = field(repr=False)  # never in reprs, assertion headers, logs or files


def _ask_tty(prompt: str, *, hidden: bool = False) -> str:
    """Prompt on the controlling terminal, bypassing pytest's stdin capture. Returns "" when
    there is no terminal (CI, piped stdin), so the live tests simply skip there."""
    try:
        with open("/dev/tty", "r+") as tty:
            if hidden:
                return getpass.getpass(prompt, stream=tty).strip()
            tty.write(prompt)
            tty.flush()
            return tty.readline().strip()
    except (OSError, EOFError):
        return ""


@pytest.fixture(scope="session")
def live_credentials(request: pytest.FixtureRequest) -> LiveCredentials:
    """Provider + model + key for the `live` tests, asked interactively in the terminal so no
    developer ever has to store a key to run them. `--live-provider/--live-model` plus the
    provider's env var (e.g. GEMINI_API_KEY) skip the prompts for scripted runs."""
    if not request.config.getoption("--live"):
        pytest.skip("live provider test: pass --live")
    cfg = default_config()
    provider = request.config.getoption("--live-provider") or "gemini"
    model = request.config.getoption("--live-model") or ""
    interactive = not (request.config.getoption("--live-provider") and model)
    if interactive:
        _ask_tty("\n[live] Teste com modelo real. Nada do que você digitar é gravado.\n")
        provider = _ask_tty(f"[live] Provedor {sorted(cfg.providers)} [{provider}]: ") or provider
        default_model = LIVE_DEFAULT_MODEL.get(provider, "")
        model = _ask_tty(f"[live] Modelo [{default_model}]: ") or default_model
    if provider not in cfg.providers:
        pytest.skip(f"provedor desconhecido: {provider}")
    model = model or LIVE_DEFAULT_MODEL.get(provider, "")
    if not model:
        pytest.skip("informe --live-model")
    env_name = cfg.providers[provider].api_key_env
    key = os.environ.get(env_name, "") if env_name else ""
    if env_name and not key:
        key = _ask_tty(f"[live] Chave de API de {provider} ({env_name}, oculta): ", hidden=True)
    if env_name and not key:
        pytest.skip("sem chave: teste live pulado")
    return LiveCredentials(provider, model, env_name, key)


@pytest.fixture(autouse=True)
def no_ambient_keys(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Hide real API keys from every test but the live ones.

    `resolve_key` falls back to the process environment, so a developer who exports
    GEMINI_API_KEY to run the live tests would fail the suite: the tests that assert "no key
    configured" would find the real one. The live tests are the exception and keep it."""
    if "live" in request.keywords:
        return
    for env_name in _KEY_ENV_NAMES:
        monkeypatch.delenv(env_name, raising=False)


@pytest.fixture(autouse=True)
def isolated_loompa_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Story worktrees live under the hub (`~/.loompa/worktrees`): no test may create them in
    the real one. The `hub` fixture narrows this further to its own folder."""
    monkeypatch.setenv("LOOMPA_HOME", str(tmp_path / "loompa-home"))


@pytest.fixture(autouse=True)
def lean_inner_pytest(monkeypatch: pytest.MonkeyPatch):
    """The quality gate runs a real `python -m pytest` in each story's worktree, with a fresh
    bytecode cache every time (`run_command`), so every plugin installed here (langsmith, cov,
    xdist…) was recompiled on each call: ~2s instead of ~0.6s. The test repos need none."""
    monkeypatch.setenv("PYTEST_DISABLE_PLUGIN_AUTOLOAD", "1")


@pytest.fixture(autouse=True)
def close_leaked_contexts(monkeypatch: pytest.MonkeyPatch):
    """A test that fails before `await ctx.aclose()` would leave the aiosqlite checkpointer
    thread alive and pytest would never exit. Track every EngineContext and close it."""
    from loompa.engine.context import EngineContext

    built: list[EngineContext] = []
    original = EngineContext.build.__func__  # type: ignore[attr-defined]

    def tracking_build(cls, *args, **kwargs):
        ctx = original(cls, *args, **kwargs)
        built.append(ctx)
        return ctx

    monkeypatch.setattr(EngineContext, "build", classmethod(tracking_build))
    yield
    for ctx in built:
        if not ctx.closed:
            try:
                ctx.close()
            except Exception:  # noqa: BLE001 - best effort at teardown
                pass


_EXIT_STATUS = 0


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    global _EXIT_STATUS
    _EXIT_STATUS = int(exitstatus)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """The suite used to hang forever *after* printing its result: interpreter shutdown joins
    non-daemon threads (aiosqlite's worker, Starlette's TestClient portal) that never got their
    stop sentinel. The verdict is already out, so leave with it instead of waiting, and name
    the threads so the leak stays visible."""
    leftovers = [
        t
        for t in threading.enumerate()
        if t is not threading.main_thread() and not t.daemon and t.is_alive()
    ]
    if not leftovers:
        return
    print(
        f"\n[conftest] saindo sem esperar {len(leftovers)} thread(s) presas: "
        + ", ".join(t.name for t in leftovers),
        file=sys.stderr,
    )
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(_EXIT_STATUS)


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


# The matrix `apply_preset(config, "openrouter")` used to install. Several tests need a factory
# whose models all come from OpenRouter; presets are gone, so the shape lives here instead.
OPENROUTER_ALIASES = {
    "tier1": ["~z-ai/glm-latest", "~x-ai/grok-latest", "~openai/gpt-sol-latest"],
    "tier2": ["~z-ai/glm-flash-latest", "~openai/gpt-luna-latest", "~google/gemini-flash-latest"],
    "tier3": ["openrouter/free"],
}


def use_openrouter(config, *, tiers: dict[str, list[str]] | None = None) -> None:
    """Point every cluster and tier of `config` at OpenRouter ids, in place."""
    from loompa.config.schema import ALL_CLUSTERS, ModelCandidate

    picked = tiers or OPENROUTER_ALIASES
    config.models.tiers = {
        t: [ModelCandidate(provider="openrouter", model=m) for m in ms] for t, ms in picked.items()
    }
    config.models.matrix = {
        cluster: {
            t: [ModelCandidate(provider="openrouter", model=m) for m in ms]
            for t, ms in picked.items()
        }
        for cluster in ALL_CLUSTERS
    }
