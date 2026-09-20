"""Fase 4 (ADR-0009): the Analyst and the research route, end to end with scripted models and a
fake MCP server. Nothing here touches the network."""

from __future__ import annotations

import json
from typing import Any

import pytest

from loompa.agents.dryrun import dry_run_script, role_of
from loompa.comms import FounderAnswer
from loompa.engine import EngineContext, Scheduler, Stage, StoryKind, build_route, load_state
from loompa.engine.state import BlockedReason
from loompa.factory import Factory
from loompa.llm import Message, ToolCall
from loompa.mcp import McpHub
from test_engine import factory, make_ctx, seed_story, tool_results  # noqa: F401
from test_mcp import KEY, fake_tavily, in_process
from test_pipeline import scripted

VERIFIED = "https://example.com/a"  # what the fake search returns (normalized)
INVENTED = "https://invented.example/relatorio-secreto"


def classify_as_research(model: str, messages: list[Message]) -> Any:
    if "Classify the story" in messages[0].content:
        return json.dumps(
            {"kind": "research", "complexity": "STANDARD", "children": [], "reason": "t"}
        )
    return dry_run_script(model, messages, None)


def with_web(ctx: EngineContext, *, key: bool = True) -> EngineContext:
    """Give the context the fake Tavily (or the same server without a key)."""
    ctx.mcp = McpHub(
        ctx.config, {"TAVILY_API_KEY": KEY} if key else {}, connector=in_process(fake_tavily())
    )
    return ctx


def report(**over: Any) -> str:
    base = {
        "question": "Qual gateway de pagamento usar?",
        "summary": "Há três opções maduras; a A tem as menores taxas para Pix.",
        "findings": [
            {"text": "A cobra 0,99% por Pix.", "sources": [VERIFIED, INVENTED]},
            {"text": "O código já isola pagamentos em um módulo.", "sources": ["app/calc.py"]},
            {"text": "B parece mais rápido para integrar.", "sources": []},
        ],
        "recommendation": "Começar pela A e medir por um mês.",
        "limitations": ["Preços mudam com frequência."],
        "follow_ups": [
            {"title": "Integrar o gateway A", "description": "Criar a cobrança por Pix."}
        ],
        "needs_decision": False,
    }
    return json.dumps(base | over)


def searching_analyst(final: str, prompts: list[str] | None = None):
    """Searches the web once, then answers with `final`."""

    def analyst(model: str, messages: list[Message]) -> Any:
        if prompts is not None and not tool_results(messages):
            prompts.append(messages[-1].content)
        if not tool_results(messages):
            return [ToolCall("a1", "tavily_search", {"query": "gateways de pagamento Pix"})]
        return final

    return analyst


async def story_of(ctx: EngineContext, title: str = "Pesquisar gateways de pagamento") -> str:
    sid = seed_story(ctx, title, "Qual gateway de pagamento devemos usar para Pix?")
    await Scheduler(ctx).run()
    return sid


# ---------------------------------------------------------------------------- route


def test_research_route_has_no_code_phases():
    assert build_route(StoryKind.RESEARCH, "SIMPLE") == ["intake", "research", "research_review"]
    assert build_route("research", "COMPLEX") == ["intake", "research", "research_review"]
    assert "dev" in build_route("feature", "STANDARD")


async def test_research_story_ends_in_a_report_for_the_founder(factory: Factory):
    prompts: list[str] = []
    script = scripted(
        {"master": classify_as_research, "analyst": searching_analyst(report(), prompts)}
    )
    ctx = with_web(make_ctx(factory, script))
    sid = await story_of(ctx)
    state = load_state(ctx, sid)
    assert state.kind == StoryKind.RESEARCH
    assert state.route == ["intake", "research", "research_review"]
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == BlockedReason.DELIVERY
    assert not state.worktree and ctx.worktrees.list() == [] and not state.commits  # no code
    assert "web tools available: tavily_extract, tavily_search" in prompts[0]  # told what it has

    # the report keeps what was verified and drops what the model made up
    text = (factory.paths.specs / sid / "research.md").read_text(encoding="utf-8")
    assert VERIFIED in text and "app/calc.py" in text and INVENTED not in text
    assert "sem fonte verificada" in text  # the unsourced finding says so
    assert "não foram encontradas nas consultas" in text  # and the dropped citation is declared
    extra = state.extra["research"]
    assert extra["web_used"] and extra["sourced"] == 2 and extra["findings_total"] == 3
    assert extra["dropped_sources"] == [INVENTED]

    # the founder gets a plain-language delivery with the follow-up as its own decision
    msg = ctx.store.get_message(state.blocked_message_id)
    assert msg.kind == "delivery" and msg.executive_audit() == []
    assert msg.title.startswith("Pesquisa pronta") and "Começar pela A" in msg.context
    assert [o.key for o in msg.options] == ["approve", "changes"]
    assert [d.title for d in msg.decisions] == ["Achado: [Oportunidade] Integrar o gateway A"]
    assert VERIFIED not in msg.context and KEY not in msg.model_dump_json()

    types = [e["type"] for e in ctx.store.events_since(0, limit=10_000)]
    assert "research.written" in types and "research.approved" in types
    assert any(
        e["type"] == "tool.call" and e["payload"]["tool"] == "tavily_search"
        for e in ctx.store.events_since(0, limit=10_000)
    )

    # approving closes the story without any merge
    done = await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="approve"))
    assert done.stage == Stage.DONE and done.merged_sha is None
    await ctx.aclose()


async def test_the_report_is_findable_by_later_stories(factory: Factory):
    ctx = with_web(
        make_ctx(
            factory,
            scripted({"master": classify_as_research, "analyst": searching_analyst(report())}),
        )
    )
    sid = await story_of(ctx)
    hits = ctx.memory.recall("gateway de pagamento Pix taxas", top_k=5, kinds=("spec",))
    assert sid in hits or "gateway" in hits.lower()
    await ctx.aclose()


# ------------------------------------------------------------------ no web search


async def test_without_a_key_the_analyst_declares_the_limitation(factory: Factory):
    prompts: list[str] = []

    def analyst(model: str, messages: list[Message]) -> Any:
        prompts.append(messages[-1].content)
        return report(findings=[{"text": "O projeto não trata pagamentos.", "sources": ["app/"]}])

    ctx = with_web(
        make_ctx(factory, scripted({"master": classify_as_research, "analyst": analyst})),
        key=False,
    )
    sid = await story_of(ctx)
    state = load_state(ctx, sid)
    assert "web tools NOT available" in prompts[0] and "TAVILY_API_KEY" in prompts[0]
    extra = state.extra["research"]
    assert extra["web_available"] is False and extra["web_used"] is False
    assert any("falta configurar a chave do Tavily" in lim for lim in extra["limitations"])
    text = (factory.paths.specs / sid / "research.md").read_text(encoding="utf-8")
    assert "A busca na web não estava disponível" in text
    msg = ctx.store.get_message(state.blocked_message_id)
    assert "busca na web não estava disponível" in msg.context and msg.executive_audit() == []
    assert state.stage == Stage.AWAITING_FOUNDER  # a missing key never blocks the story
    await ctx.aclose()


async def test_the_model_cannot_hide_that_it_skipped_the_web(factory: Factory):
    """Tools were there, the model never used them: code adds the limitation anyway."""
    ctx = with_web(
        make_ctx(
            factory,
            scripted(
                {
                    "master": classify_as_research,
                    "analyst": lambda m, msgs: report(limitations=[], findings=[]),
                }
            ),
        )
    )
    sid = seed_story(ctx, "Pesquisar algo")
    await Scheduler(ctx).run()
    extra = load_state(ctx, sid).extra["research"]
    assert extra["web_available"] and not extra["web_used"]
    assert extra["limitations"][0].startswith("Nenhuma busca na web foi feita")
    await ctx.aclose()


# ---------------------------------------------------------------------- the review


async def test_product_owner_sends_the_report_back_once(factory: Factory):
    prompts: list[str] = []
    reviews = {"n": 0}

    def po(model: str, messages: list[Message]) -> Any:
        assert "Review it before it reaches the founder" in messages[0].content
        reviews["n"] += 1
        if reviews["n"] == 1:
            return json.dumps(
                {
                    "approved": False,
                    "unsupported": ["B parece mais rápido"],
                    "missing": [],
                    "notes": "sem base",
                }
            )
        return json.dumps({"approved": True, "unsupported": [], "missing": [], "notes": "ok"})

    ctx = with_web(
        make_ctx(
            factory,
            scripted(
                {
                    "master": classify_as_research,
                    "analyst": searching_analyst(report(), prompts),
                    "product_owner": po,
                }
            ),
        )
    )
    sid = await story_of(ctx)
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.research_review_rounds == 2
    assert len(prompts) == 2
    assert (
        "Review feedback (fix these first)" in prompts[1] and "B parece mais rápido" in prompts[1]
    )
    assert "Previous report" in prompts[1]  # the second run improves the first
    types = [e["type"] for e in ctx.store.events_since(0, limit=10_000)]
    assert types.count("research.rejected") == 1 and types.count("research.approved") == 1
    await ctx.aclose()


async def test_a_web_report_with_no_verified_source_cannot_pass_however_kind_the_reviewer(
    factory: Factory,
):
    only_invented = report(findings=[{"text": "Dado inventado.", "sources": [INVENTED]}])
    ctx = with_web(
        make_ctx(
            factory,
            scripted(
                {
                    "master": classify_as_research,
                    "analyst": searching_analyst(only_invented),
                    # the model reviewer approves everything: the code gate must not
                }
            ),
        )
    )
    sid = await story_of(ctx)
    state = load_state(ctx, sid)
    assert state.research_review_rounds == 2  # sent back once, then carried on with concerns
    msg = ctx.store.get_message(state.blocked_message_id)
    assert "Ressalvas da revisão" in msg.context and "fonte verificada" in msg.context
    assert state.review_notes and msg.executive_audit() == []
    await ctx.aclose()


# ---------------------------------------------------------------- founder answers


async def test_founder_can_ask_for_more_depth(factory: Factory):
    prompts: list[str] = []
    ctx = with_web(
        make_ctx(
            factory,
            scripted(
                {"master": classify_as_research, "analyst": searching_analyst(report(), prompts)}
            ),
        )
    )
    sid = await story_of(ctx)
    msg = ctx.store.get_message(load_state(ctx, sid).blocked_message_id)
    state = await Scheduler(ctx).aanswer(
        msg.id, FounderAnswer(option_key="changes", text="Compare também o custo de suporte")
    )
    assert state.phase == "research" and state.stage == Stage.SPEC
    assert state.research_review_rounds == 0
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    assert "Compare também o custo de suporte" in prompts[-1]
    assert len(ctx.worktrees.list()) == 0
    await ctx.aclose()


async def test_an_ambiguous_request_asks_the_founder_first(factory: Factory):
    calls = {"n": 0}

    def analyst(model: str, messages: list[Message]) -> Any:
        calls["n"] += 1
        if "Compare" not in messages[-1].content:
            return json.dumps(
                {
                    "needs_decision": True,
                    "clarification": "Pesquisar o quê, exatamente?",
                    "options": ["Meios de pagamento", "Provedores de e-mail"],
                }
            )
        return report(findings=[{"text": "Achado.", "sources": ["app/calc.py"]}])

    ctx = with_web(
        make_ctx(factory, scripted({"master": classify_as_research, "analyst": analyst}))
    )
    sid = await story_of(ctx)
    state = load_state(ctx, sid)
    assert state.blocked_reason == BlockedReason.QUESTION and state.resume_phase == "research"
    msg = ctx.store.get_message(state.blocked_message_id)
    assert msg.kind == "blocked" and msg.executive_audit() == []
    state = await Scheduler(ctx).aanswer(msg.id, FounderAnswer(text="Compare meios de pagamento"))
    assert state.phase == "research"
    await Scheduler(ctx).run()
    assert load_state(ctx, sid).blocked_reason == BlockedReason.DELIVERY
    await ctx.aclose()


# --------------------------------------------------------------------- dry run


async def test_dry_run_researches_without_touching_the_network(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    sid = seed_story(ctx, "Pesquisar gateways de pagamento")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.kind == StoryKind.RESEARCH and state.stage == Stage.AWAITING_FOUNDER
    assert ctx._mcp is None  # the hub was never even built: a simulation stays offline
    extra = state.extra["research"]
    assert extra["web_available"] is False and "simulação" in extra["limitations"][0]
    msg = ctx.store.get_message(state.blocked_message_id)
    assert msg.executive_audit() == [] and msg.title.startswith("Pesquisa pronta")
    await ctx.aclose()


# --------------------------------------------- Architect/Product explore the repository


async def test_architect_checks_the_repository_before_planning(factory: Factory):
    seen: list[list[Message]] = []

    def architect(model: str, messages: list[Message]) -> Any:
        if "Falha (filtrada)" in messages[-1].content:
            return dry_run_script(model, messages, None)
        seen.append(list(messages))
        if not tool_results(messages):
            return [ToolCall("r1", "search", {"pattern": "def add"})]
        return json.dumps(
            {
                "approach": "Estender calc",
                "files": ["app/calc.py", "tests/test_calc.py"],
                "contracts": "",
                "risks": [],
                "tasks": ["Adicionar subtract com testes"],
                "adr_proposal": "",
            }
        )

    ctx = make_ctx(factory, scripted({"architect": architect}), dry_run=True)
    sid = seed_story(ctx, "Subtração")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.allowed_paths[:2] == ["app/calc.py", "tests/test_calc.py"]
    assert any("app/calc.py" in r for r in tool_results(seen[-1]))  # it looked before it planned
    await ctx.aclose()


@pytest.mark.parametrize("role", ["architect", "product"])
async def test_planning_roles_are_offered_only_read_tools(factory: Factory, role: str):
    offered: list[set[str]] = []

    def spy(model: str, messages: list[Message], tools: list[dict[str, Any]] | None) -> Any:
        if role_of(messages) == role:
            offered.append({t["name"] for t in tools or []})
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, spy, dry_run=True)
    seed_story(ctx, "Qualquer coisa")
    await Scheduler(ctx).run()
    assert offered and offered[0] == {"read_file", "list_dir", "search", "find_symbol"}
    await ctx.aclose()
