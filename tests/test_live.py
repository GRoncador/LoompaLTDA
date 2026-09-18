"""Live tests: one real cycle against Gemini 2.5 Flash-Lite (free tier).

Run locally with::

    loompa providers set-key gemini          # once; writes ~/.loompa/secrets.env
    uv run pytest --live -m live -q

GitHub CI never passes ``--live``; everything else in the suite is scripted (MockProvider)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from conftest import git
from loompa.agents import MasterAgent
from loompa.config import Secrets, apply_preset
from loompa.config.schema import ModelCandidate
from loompa.engine import EngineContext, Scheduler, Stage, load_state
from loompa.factory import Factory, bootstrap_factory
from loompa.llm import probe_provider

pytestmark = pytest.mark.live
LIVE_MODEL = "gemini-2.5-flash-lite"
PYTEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider'


def _key_available() -> bool:
    # The real hub file (~/.loompa/secrets.env) or the environment; never a value in test output.
    return bool(Secrets.load(None).get("GEMINI_API_KEY"))


@pytest.fixture
def live_factory(git_repo: Path, hub) -> Factory:
    if not _key_available():
        pytest.skip("GEMINI_API_KEY não configurada (loompa providers set-key gemini)")
    (git_repo / "app").mkdir()
    (git_repo / "app" / "__init__.py").write_text("")
    (git_repo / "app" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_calc.py").write_text(
        "from app.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    git("add", ".", cwd=git_repo)
    git("commit", "-qm", "feat: calc", cwd=git_repo)
    f = bootstrap_factory(git_repo, name="Live", store=hub).factory
    apply_preset(f.config, "gratuito")
    # Every role on the same free model: one key, one rate limit, cheapest possible cycle.
    only = [ModelCandidate(provider="gemini", model=LIVE_MODEL)]
    f.config.models.tiers = {"tier1": list(only), "tier2": list(only)}
    f.config.quality.test_command = PYTEST_CMD
    f.config.quality.lint_command = ""
    f.config.quality.typecheck_command = ""
    f.config.schedule.max_parallel = 1
    f.config.schedule.ops_retry_base_s = 5
    f.save()
    return Factory.open(git_repo)


async def test_live_probe_gemini(live_factory: Factory):
    r = await probe_provider(live_factory.config, "gemini", secrets=Secrets.load(live_factory.root))
    assert r.ok, r.detail
    assert r.model == LIVE_MODEL


async def test_live_first_real_cycle(live_factory: Factory):
    """Meeting → spec → plan → dev → test → review → delivery message, all on flash-lite."""
    ctx = EngineContext.build(live_factory)  # real router, key from the secrets file
    try:
        result = await MasterAgent(ctx).meeting(
            "Adicionar a função subtract(a, b) em app/calc.py com teste em tests/test_calc.py"
        )
        assert result["stories"], result
        done = await Scheduler(ctx).run()
        assert done
        state = load_state(ctx, done[0])
        assert state.stage == Stage.AWAITING_FOUNDER, state.model_dump()
        msgs = ctx.store.list_messages(live_factory.slug, status="pending")
        assert msgs and all(m.executive_audit() == [] for m in msgs)
        usage = ctx.store.usage_totals(live_factory.slug)
        assert usage["calls"] >= 3 and usage["input_tokens"] > 0
        assert all(r["key"] == LIVE_MODEL for r in ctx.store.usage_by("model", live_factory.slug))
    finally:
        await ctx.aclose()
