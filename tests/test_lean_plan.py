"""ADR-0028: a SIMPLE story is planned with the short form, and goes back for the long one when
the work turns out to need it."""

from __future__ import annotations

import json
from typing import Any

from loompa.agents.architect import FULL_PLAN_KEY, SIMPLE_SYSTEM, SYSTEM
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.engine import Scheduler, Stage, load_state
from loompa.factory import Factory
from loompa.llm import Message
from test_engine import factory, make_ctx, seed_story  # noqa: F401
from test_preflight import SPEC

LEAN_MARK = "does not pay for the long form"  # only in SIMPLE_SYSTEM
# the dry-run Worker writes under `loompa_dryrun/`, so every plan fences it there too
LEAN_PLAN = {
    "approach": "Trocar o rótulo na tela de resumo.",
    "files": ["loompa_dryrun/", "tests/"],
    "risks": [],
    "traceability": [
        {"criterion": 1, "test": "tests/test_resumo.py::test_rotulo"},
        {"criterion": 2, "test": "tests/test_resumo.py::test_rotulo_vazio"},
    ],
    "tasks": [
        {
            "task": "Registrar o rótulo em loompa_dryrun/resumo.md",
            "verify": "pytest tests/test_resumo.py",
        }
    ],
    "not_simple": False,
    "blocker": "",
}


def test_the_two_forms_ask_for_different_things():
    """The short form keeps what the engine reads and drops what a one-file change has nothing to
    put in."""
    for field in ("files", "tasks", "traceability", "verify", "blocker"):
        assert field in SIMPLE_SYSTEM and field in SYSTEM
    for field in (
        "alternatives_considered",
        "impact",
        "rollback",
        "constitution_check",
        "adr_proposal",
    ):
        assert field in SYSTEM and field not in SIMPLE_SYSTEM
    assert LEAN_MARK in SIMPLE_SYSTEM and LEAN_MARK not in SYSTEM
    assert SIMPLE_SYSTEM.startswith("<!-- role:architect -->")
    assert "Respond with JSON only" in SIMPLE_SYSTEM and "{language}" in SIMPLE_SYSTEM


def _script(complexity: str, plan: dict[str, Any] | None = None, plans: list[dict] | None = None):
    seen: dict[str, list[str]] = {"forms": []}
    queue = list(plans or [])

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role == "master" and "Classify the story" in messages[0].content:
            return json.dumps({"kind": "feature", "complexity": complexity, "children": []})
        if role == "product":
            return json.dumps(SPEC)
        if role == "architect" and "## Repository outline" in messages[1].content:
            seen["forms"].append("lean" if LEAN_MARK in messages[0].content else "full")
            if queue:
                return json.dumps(queue.pop(0))
            return json.dumps(plan or LEAN_PLAN)
        return dry_run_script(model, messages, tools)

    script.seen = seen  # type: ignore[attr-defined]
    return script


async def test_a_simple_story_is_planned_with_the_short_form(factory: Factory):
    script = _script("SIMPLE")
    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Trocar um rótulo")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert script.seen["forms"] == ["lean"]  # planned once, in the short form
    assert state.blocked_reason == "delivery"
    # the artifacts the engine depends on are all there
    assert state.allowed_paths[:2] == ["loompa_dryrun/", "tests/"] and state.tasks_total == 1
    plan = (factory.paths.specs / sid / "plan.md").read_text()
    assert "Critério 1 → tests/test_resumo.py::test_rotulo" in plan
    assert "Critério 2 → tests/test_resumo.py::test_rotulo_vazio" in plan
    assert "**sem teste planejado**" not in plan  # so the gate let it through
    assert "_(nenhum)_" in plan  # the long form's sections render empty, not broken
    await ctx.aclose()


async def test_a_standard_story_still_gets_the_long_form(factory: Factory):
    from test_preflight import PLAN

    script = _script("STANDARD", plan={**PLAN, "files": ["loompa_dryrun/", "tests/"]})
    ctx = make_ctx(factory, script)
    seed_story(ctx, "História padrão")
    await Scheduler(ctx).run()
    assert script.seen["forms"][0] == "full"
    await ctx.aclose()


async def test_the_architect_can_ask_for_the_long_form_itself(factory: Factory):
    """`not_simple`: the escape hatch for a story whose label was wrong. The second plan is the
    full one, over the same spec."""
    script = _script("SIMPLE", plans=[{**LEAN_PLAN, "not_simple": True}, LEAN_PLAN])
    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Parecia simples")
    await Scheduler(ctx).run()
    assert script.seen["forms"] == ["lean", "full"]  # and it does not loop
    state = load_state(ctx, sid)
    assert state.extra[FULL_PLAN_KEY] == "o Arquiteto pediu o plano completo"
    assert state.stage != Stage.BACKLOG and state.blocked_reason == "delivery"
    await ctx.aclose()


async def test_a_short_plan_that_touches_a_schema_is_planned_again_in_full(factory: Factory):
    """The risk the short form cannot describe: `node_plan` lifts such a plan to the pre-flight,
    which reads the impact and the rollback the short form never asked for."""
    # `models.py` is a schema for `risk.kind_of`, and the fence still lets the Worker work
    risky = {**LEAN_PLAN, "files": ["loompa_dryrun/", "loompa_dryrun/models.py", "tests/"]}
    script = _script("SIMPLE", plans=[risky, risky])
    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Mexe no schema")
    await Scheduler(ctx).run()
    assert script.seen["forms"] == ["lean", "full"]
    state = load_state(ctx, sid)
    assert state.extra[FULL_PLAN_KEY] == "o plano toca schema, migração ou contrato"
    assert state.autonomy == "preflight" and "preflight" in state.route
    whys = [
        e["payload"]["why"]
        for e in ctx.store.events_since(0, limit=10_000)
        if e["type"] == "plan.not_simple"
    ]
    assert whys == ["o plano toca schema, migração ou contrato"]
    await ctx.aclose()
