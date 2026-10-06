"""The prompt bench (plan 14): prompts found in the code, cases from a real trace, checks in code,
and the A/B comparison with a blind judge — all on MockProvider, never the network."""

from __future__ import annotations

import json
from pathlib import Path

from loompa.agents.base import EXPLORE_HINT
from loompa.agents.deployer import SUMMARY_SYSTEM
from loompa.bench import cases, checks, registry
from loompa.bench.compare import Variant, compare, family, judge_spec, messages_for, pinned
from loompa.config import default_config
from loompa.llm.providers import Message, MockProvider, ToolCall
from loompa.llm.router import ModelRouter
from loompa.trace import Tracer


def test_every_agent_prompt_is_found_with_its_json_contract():
    table = registry.prompts()
    assert "deployer.SUMMARY_SYSTEM" in table and "worker.SYSTEM" in table
    assert table["deployer.SUMMARY_SYSTEM"].contract == {"summary": "str"}
    classify = registry.get("CLASSIFY")  # a unique part of the name is enough
    assert classify.contract["complexity"] == "enum:SIMPLE|STANDARD|COMPLEX"
    assert classify.contract["children"] == "list"
    assert registry.get("product_owner.REVIEW_SYSTEM").contract["approved"] == "bool"


def test_a_recorded_prompt_is_recognised_after_a_rewrite_and_keeps_its_tail():
    old = SUMMARY_SYSTEM.format(language="pt-BR").replace("2-4 short sentences", "two sentences")
    key, score = registry.identify(old)
    assert key == "deployer.SUMMARY_SYSTEM" and score > 0.9
    spec = registry.get("product.SYSTEM")
    recorded = spec.render() + EXPLORE_HINT
    assert registry.tail_after(spec.render(), recorded).strip() == EXPLORE_HINT.strip()
    assert registry.identify("You are a pirate. Say arr.")[0] == ""


def test_checks_catch_what_reached_the_founder_in_sp009():
    p = registry.get("master.SPRINT_REPORT_SYSTEM")
    nested = json.dumps({"summary": json.dumps({"summary": "O sprint entregou 4 histórias."})})
    got = checks.check(p, "one_shot", nested, [])
    assert got.json_ok and got.contract_ok and not got.founder_ok
    assert any(e.startswith("json_inside") for e in got.founder_errors)
    listing = json.dumps({"summary": "Tarefa 1: tarefa 1 concluído. Tarefa 2: ok."})
    assert "task_list:summary" in checks.check(p, "one_shot", listing, []).founder_errors
    english = json.dumps({"summary": "The sprint delivered the story and the tests were green."})
    assert "not_pt:summary" in checks.check(p, "one_shot", english, []).founder_errors
    good = json.dumps({"summary": "O sprint entregou as quatro histórias que tinha como meta."})
    assert checks.check(p, "one_shot", good, []).ok
    bad_enum = json.dumps({"kind": "epic", "complexity": "SIMPLE", "children": [], "reason": "x"})
    assert checks.check(registry.get("CLASSIFY"), "one_shot", bad_enum, []).contract_errors == [
        "enum:kind=epic"
    ]


async def _factory_with_trace(root: Path) -> None:
    """Two real calls through a traced router: a delivery summary and a tool loop's first call."""
    (root / ".loompa").mkdir(parents=True)

    def script(model, messages, tools):
        if tools:
            return [ToolCall(id="c1", name="read_file", arguments={"path": "src/a.js"})]
        return '{"summary": "O pet agora mostra a idade em dias."}'

    router = ModelRouter(
        pinned(default_config(), "mock:m"),
        providers={"mock": MockProvider("mock", script)},
        tracer=Tracer(root / ".loompa" / "traces"),
    )
    await router.complete(
        "deployer",
        [
            Message("system", SUMMARY_SYSTEM.format(language="pt-BR")),
            Message("user", "Story: idade\n\nWorker notes:\nT1: idade em dias"),
        ],
        story_id="S-001",
        json_mode=True,
        task="deployer.summary",
    )
    worker = registry.get("worker.SYSTEM").render() + "\n## Story S-001 — idade\n\n## Spec\nx"
    await router.complete(
        "worker",
        [Message("system", worker), Message("user", "# Task T1\n\n**do it**")],
        story_id="S-001",
        tools=[{"name": "read_file", "description": "", "parameters": {}}],
    )


async def test_cases_are_harvested_from_the_trace_and_saved_in_the_hub(tmp_path: Path):
    root = tmp_path / "fab"
    await _factory_with_trace(root)
    found = list(cases.harvest_factory(root, "fab"))
    by_prompt = {c.prompt: c for c in found}
    summary = by_prompt["deployer.SUMMARY_SYSTEM"]
    assert summary.mode == "one_shot" and summary.task == "deployer.summary"
    assert summary.response.startswith('{"summary"') and summary.role == "deployer"
    first = by_prompt["worker.SYSTEM"]
    assert first.mode == "first" and first.tools == ["read_file"]
    assert first.tool_calls[0]["name"] == "read_file"
    # the Worker's story block survives a replay with another prompt text
    replayed = messages_for(registry.get("worker.SYSTEM"), first, "NEW PROMPT {language}")
    assert replayed[0].content.startswith("NEW PROMPT pt-BR") and "## Story S-001" in (
        replayed[0].content
    )
    home = tmp_path / "hub"
    assert cases.save(found, "fab", home) == {"deployer.SUMMARY_SYSTEM": 1, "worker.SYSTEM": 1}
    assert [c.id for c in cases.load("deployer.SUMMARY_SYSTEM", home=home)] == [summary.id]


def test_sampling_spreads_over_stories_and_drops_repeated_inputs():
    def case(story: str, user: str, at: str) -> cases.Case:
        msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": user}]
        return cases.Case(
            id=f"f:{story}:{at}", factory="f", story=story, prompt="p", similarity=1.0,
            mode="one_shot", language="pt-BR", messages=msgs, at=at,
        )  # fmt: skip

    pool = [case("S-1", f"u{i}", f"2026-10-0{i}") for i in range(1, 6)]
    pool += [case("S-2", "v", "2026-10-01"), case("S-2", "v", "2026-10-02")]  # a retry
    picked = cases.sample(pool, per_prompt=3)
    assert len(picked) == 3 and {c.story for c in picked} == {"S-1", "S-2"}
    assert len(cases.sample(pool, per_prompt=10)) == 6


def test_the_judge_comes_from_a_maker_that_made_neither_variant():
    cfg = default_config()
    assert family("openrouter", "xiaomi/mimo-v2.6-flash") == family("xiaomi", "mimo-v2.6-pro")
    tier1 = cfg.models.tiers.get("tier1", [])
    if len(tier1) > 1:
        first = tier1[0]
        spec = judge_spec(cfg, [f"{first.provider}:{first.model}"])
        assert family(*parse(spec)) != family(first.provider, first.model)


def parse(spec: str) -> tuple[str, str]:
    provider, _, model = spec.partition(":")
    return provider, model


async def test_compare_replays_both_variants_and_the_blind_judge_picks(tmp_path: Path):
    root = tmp_path / "fab"
    await _factory_with_trace(root)
    picked = [c for c in cases.harvest_factory(root, "fab") if c.mode == "one_shot"]
    seen_systems: list[str] = []

    def script(model, messages, tools):
        system = messages[0].content
        if system.startswith("You are the reviewer of a prompt bench"):
            user = messages[-1].content
            first = user.split("## Answer 1", 1)[1].split("## Answer 2", 1)[0]
            better = "1" if "tela" in first else "2"  # the candidate's answer mentions the screen
            return json.dumps({"better": better, "score_1": 4, "score_2": 2, "reason": "r"})
        seen_systems.append(system)
        if system.startswith("CANDIDATE"):
            return '{"summary": "A idade do pet aparece na tela em dias."}'
        return '{"summary": "Feito."}'

    mock = MockProvider("mock", script)
    cfg = pinned(default_config(), "mock:m")
    report = await compare(
        cfg,
        {},
        registry.get("deployer.SUMMARY_SYSTEM"),
        picked,
        Variant("A"),
        Variant("B", 'CANDIDATE {language}\nRespond with JSON only: {{"summary": str}}'),
        judge_model="mock:judge",
        providers={"mock": mock},
    )
    summary = report.summary()
    assert summary["B"]["judge_wins"] == 1 and summary["A"]["judge_wins"] == 0
    assert summary["A"]["json_ok"] == summary["B"]["json_ok"] == 100.0
    assert any(s.startswith("<!-- role:deployer -->") for s in seen_systems)
    assert any(s.startswith("CANDIDATE pt-BR") for s in seen_systems)
    assert report.cases[0]["decision"] == {}  # a summary carries no decision field


async def test_a_judge_that_always_picks_the_first_answer_counts_as_a_tie(tmp_path: Path):
    """Position bias (Zheng et al. 2023): the judge reads each pair in both orders, and a winner
    that changes with the order is no winner."""
    root = tmp_path / "fab"
    await _factory_with_trace(root)
    picked = [c for c in cases.harvest_factory(root, "fab") if c.mode == "one_shot"]

    def script(model, messages, tools):
        if messages[0].content.startswith("You are the reviewer of a prompt bench"):
            return json.dumps({"better": "1", "score_1": 5, "score_2": 1, "reason": "first"})
        return '{"summary": "Feito."}'

    mock = MockProvider("mock", script)
    report = await compare(
        pinned(default_config(), "mock:m"),
        {},
        registry.get("deployer.SUMMARY_SYSTEM"),
        picked,
        Variant("A"),
        Variant("B", 'B {language}\nRespond with JSON only: {{"summary": str}}'),
        judge_model="mock:judge",
        providers={"mock": mock},
    )
    summary = report.summary()
    assert summary["A"]["judge_wins"] == summary["B"]["judge_wins"] == 0
    assert summary["A"]["judge_ties"] == summary["A"]["judge_flips"] == len(picked)
    assert summary["A"]["judge_score"] == summary["B"]["judge_score"] == 3.0


async def test_an_empty_answer_is_a_failed_call_and_never_judged(tmp_path: Path):
    root = tmp_path / "fab"
    await _factory_with_trace(root)
    picked = [c for c in cases.harvest_factory(root, "fab") if c.mode == "one_shot"]

    def script(model, messages, tools):
        if messages[0].content.startswith("You are the reviewer of a prompt bench"):
            return json.dumps({"better": "1", "score_1": 5, "score_2": 1, "reason": "r"})
        return "" if messages[0].content.startswith("EMPTY") else '{"summary": "Feito."}'

    report = await compare(
        pinned(default_config(), "mock:m"),
        {},
        registry.get("deployer.SUMMARY_SYSTEM"),
        picked,
        Variant("A"),
        Variant("B", 'EMPTY {language}\nRespond with JSON only: {{"summary": str}}'),
        judge_model="mock:judge",
        providers={"mock": MockProvider("mock", script)},
    )
    summary = report.summary()
    assert summary["B"]["errors"] == len(picked) and summary["A"]["errors"] == 0
    assert all(v.winner == "error" for v in report.verdicts)
