"""Live tests: one real cycle against a real provider (default: Gemini 3.5 Flash-Lite, free).

    uv run pytest --live -m live -q

The terminal asks for provider, model and API key (hidden input). Nothing is written to disk:
the key lives in this process's environment only, and the factory under test is a throw-away
one in pytest's tmp dir. Scripted form for a shell session that already exports the key::

    GEMINI_API_KEY=... uv run pytest --live -m live -q --live-provider gemini --live-model gemini-3.5-flash-lite

GitHub CI never passes ``--live``; everything else in the suite is scripted (MockProvider)."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from conftest import LiveCredentials, git
from loompa.agents import Conversations, MasterAgent
from loompa.comms import audit_executive_text
from loompa.config import default_config
from loompa.config.schema import ModelCandidate
from loompa.conversations import ConversationKind
from loompa.engine import EngineContext, Scheduler, Stage, load_state
from loompa.factory import Factory, bootstrap_factory
from loompa.llm import ProbeResult, probe_provider

pytestmark = pytest.mark.live


def why(ctx: EngineContext, turn) -> str:
    """A failed chat turn tells the founder only "try again"; the reason is in the event log."""
    errors = [e for e in ctx.store.events_since(0, limit=5000) if e["type"] == "conversation.error"]
    return (
        f"{turn}\n{errors[-1]['payload']['error'] if errors else '(no conversation.error event)'}"
    )


PYTEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider'


@pytest.fixture(scope="session")
def live_reachable(live_credentials: LiveCredentials) -> ProbeResult:
    """One tiny call, once per session: does the chosen model still answer? A model that was
    retired (Google closed gemini-2.5-flash-lite to new accounts on 2026-09-19) otherwise shows
    up as every story failing in `spec` after the full retry loop, with no usable message."""
    creds = live_credentials
    return asyncio.run(
        probe_provider(
            default_config(),
            creds.provider,
            model=creds.model,
            secrets={creds.api_key_env: creds.api_key} if creds.api_key_env else None,
        )
    )


@pytest.fixture
def live_factory(
    git_repo: Path, hub, live_credentials: LiveCredentials, monkeypatch: pytest.MonkeyPatch
) -> Factory:
    creds = live_credentials
    if creds.api_key_env:
        monkeypatch.setenv(creds.api_key_env, creds.api_key)  # process env only, never a file
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
    # Every role on the same cheap model: one key, one rate limit, cheapest possible cycle.
    only = [ModelCandidate(provider=creds.provider, model=creds.model)]
    f.config.models.tiers = {"tier1": list(only), "tier2": list(only)}
    f.config.quality.test_command = PYTEST_CMD
    f.config.quality.lint_command = ""
    f.config.quality.typecheck_command = ""
    f.config.schedule.max_parallel = 1
    f.config.schedule.ops_retry_base_s = 5
    f.save()
    return Factory.open(git_repo)


def test_live_probe(live_reachable: ProbeResult, live_credentials: LiveCredentials):
    assert live_reachable.ok, live_reachable.detail
    assert live_reachable.model == live_credentials.model


async def test_live_first_real_cycle(
    live_reachable: ProbeResult, live_factory: Factory, live_credentials: LiveCredentials
):
    """Meeting → spec → spec review → plan → dev → test → review → delivery message."""
    if not live_reachable.ok:  # the probe already said why; don't burn retries on a dead model
        pytest.skip(f"provedor indisponível: {live_reachable.detail}")
    ctx = EngineContext.build(live_factory)  # real router; key from the process environment
    try:
        master = MasterAgent(ctx)
        result = await master.meeting(
            "Adicionar a função subtract(a, b) em app/calc.py com teste em tests/test_calc.py"
        )
        assert result["stories"], result
        master.start_sprint()
        done = await Scheduler(ctx).run()
        assert done
        state = load_state(ctx, done[0])
        assert state.stage == Stage.AWAITING_FOUNDER, state.model_dump()
        msgs = ctx.store.list_messages(live_factory.slug, status="pending")
        assert msgs and all(m.executive_audit() == [] for m in msgs)
        usage = ctx.store.usage_totals(live_factory.slug)
        assert usage["calls"] >= 3 and usage["input_tokens"] > 0
        assert all(
            r["key"] == live_credentials.model
            for r in ctx.store.usage_by("model", live_factory.slug)
        )
        # the throw-away factory is the only place the key was ever used
        assert (
            live_credentials.api_key
            not in (live_factory.root / ".loompa" / "config.yaml").read_text()
        )
    finally:
        await ctx.aclose()


async def test_live_sprint_meeting_conversation(live_reachable: ProbeResult, live_factory: Factory):
    """Two chat turns build a draft without touching the backlog; committing fills it."""
    if not live_reachable.ok:
        pytest.skip(f"provedor indisponível: {live_reachable.detail}")
    ctx = EngineContext.build(live_factory)
    try:
        chats = Conversations(ctx)
        conv = chats.open(ConversationKind.MEETING)
        turn = await chats.say(
            conv.id,
            "Quero duas coisas em app/calc.py: a função subtract(a, b) e a função multiply(a, b).",
        )
        assert not turn.failed and turn.reply, why(ctx, turn)
        assert audit_executive_text(turn.reply) == []
        assert chats.board.require(conv.id).draft.items, turn
        assert ctx.store.list_stories(live_factory.slug) == []  # a draft is not the backlog
        turn = await chats.say(
            conv.id, "Marque a primeira para o sprint e defina a meta como 'Calculadora completa'."
        )
        assert not turn.failed and turn.reply, why(ctx, turn)
        result = await chats.commit(conv.id)
        assert result.created, result
        assert ctx.store.list_stories(live_factory.slug)
    finally:
        await ctx.aclose()


async def test_live_brainstorm_conversation(live_reachable: ProbeResult, live_factory: Factory):
    """The Analyst answers without web tools (no Tavily key here) and says so; the Product Owner
    decides on the ideas it proposes."""
    if not live_reachable.ok:
        pytest.skip(f"provedor indisponível: {live_reachable.detail}")
    ctx = EngineContext.build(live_factory)
    try:
        chats = Conversations(ctx)
        conv = chats.open(ConversationKind.BRAINSTORM)
        turn = await chats.say(
            conv.id, "Que operações matemáticas simples valeria acrescentar à calculadora?"
        )
        assert not turn.failed and turn.reply, why(ctx, turn)
        assert audit_executive_text(turn.reply) == []
        conv = chats.board.require(conv.id)
        assert conv.limits  # the missing web search is declared by code
        if conv.draft.items:
            result = await chats.commit(conv.id)
            assert result.created or result.held, result
    finally:
        await ctx.aclose()
