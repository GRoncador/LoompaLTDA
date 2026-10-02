"""Fase 7: richer spec/plan (7.1), deeper spec review (7.3), autonomy modes (7.5) and the
brownfield risk pre-flight (7.6)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from loompa.agents.dryrun import dry_run_script, role_of
from loompa.comms import FounderAnswer
from loompa.engine import Scheduler, load_state
from loompa.engine.phases import autonomy_for, build_route
from loompa.engine.state import Autonomy, Complexity, StoryKind
from loompa.factory import Factory
from loompa.llm import Message
from loompa.risk import assess, declared_dependencies, kind_of, needs_preflight
from test_engine import factory, make_ctx, seed_story  # noqa: F401

PLAN = {
    "approach": "x",
    "files": ["app/", "tests/", "loompa_dryrun/"],
    "contracts": "",
    "impact": "add() é usada pela calculadora; a assinatura não muda",
    "rollback": "",
    "risks": [],
    "traceability": [{"criterion": 1, "test": "tests/test_calc.py::test_add"}],
    "constitution_check": [
        {"rule": "Sem dependência nova", "ok": True, "note": ""},
        {"rule": "Camadas separadas", "ok": False, "note": "atalho temporário"},
    ],
    "tasks": [{"task": "Ajustar app/calc.py", "verify": "pytest tests/test_calc.py"}],
    "adr_proposal": "",
}

SPEC = {
    "goal": "g",
    "in_scope": ["a"],
    "out_of_scope": [],
    "acceptance": ["Dado A, quando B, então C", "Dado D, quando E, então F"],
    "nfrs": ["responde em menos de 1 s"],
    "edge_cases": ["valor vazio"],
    "entities": ["Gasto: valor, data, categoria"],
    "assumptions": ["moeda sempre BRL"],
    "rules": [],
    "questions": [],
    "needs_decision": False,
}


def _script(overrides: dict[str, Any]):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        fn = overrides.get(role)
        out = fn(messages) if fn else None
        return out if out is not None else dry_run_script(model, messages, tools)

    return script


# ------------------------------------------------------------------ 7.1 and 7.3


async def test_spec_and_plan_carry_the_new_sections(factory: Factory):
    reviews: list[str] = []

    def architect(messages):
        if "Pre-flight" in messages[0].content or "## Founder's guidance" in messages[-1].content:
            return None
        return json.dumps(PLAN)

    def po(messages):
        reviews.append(messages[-1].content)
        return None

    ctx = make_ctx(
        factory,
        _script(
            {"product": lambda m: json.dumps(SPEC), "architect": architect, "product_owner": po}
        ),
    )
    (factory.root / "pyproject.toml").write_text(
        '[project]\nname="demo"\ndependencies=["typer>=0.12", "pydantic"]\n'
    )
    sid = seed_story(ctx, "Somar")
    await Scheduler(ctx).run()
    assert load_state(ctx, sid).blocked_reason == "delivery"
    spec = (factory.paths.specs / sid / "spec.md").read_text()
    for part in ("## Requisitos não-funcionais", "- valor vazio", "## Entidades principais"):
        assert part in spec
    assert "## Premissas\n- moeda sempre BRL" in spec and "2. Dado D" in spec
    plan = (factory.paths.specs / sid / "plan.md").read_text()
    assert "Critério 1 → tests/test_calc.py::test_add" in plan
    assert "Critério 2 → **sem teste planejado**" in plan  # the gap is visible, not hidden
    assert "Camadas separadas — **violada**: atalho temporário" in plan
    assert "Viola a constituição" in plan  # and it is carried into the risks
    assert "add() é usada pela calculadora" in plan and "_(sem dados persistidos" in plan
    tasks = (factory.paths.specs / sid / "tasks.md").read_text()
    assert "T1: Ajustar app/calc.py — verificação: pytest tests/test_calc.py" in tasks
    assert reviews and "## Declared dependencies\npython: typer, pydantic" in reviews[0]
    await ctx.aclose()


async def test_the_architect_sends_an_unbuildable_spec_back_once(factory: Factory):
    calls = {"architect": 0, "product": 0}

    def architect(messages):
        if "Pre-flight" in messages[0].content:
            return None
        calls["architect"] += 1
        return json.dumps({**PLAN, "blocker": "precisa de uma biblioteca de PDF não permitida"})

    def product(messages):
        calls["product"] += 1
        if calls["product"] == 2:
            assert "The Architect could not plan this spec" in messages[-1].content
        return json.dumps(SPEC)

    ctx = make_ctx(factory, _script({"architect": architect, "product": product}))
    sid = seed_story(ctx, "Exportar PDF")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert calls == {"architect": 2, "product": 2}  # bounced once, then the plan went ahead
    assert state.blocked_reason == "delivery"
    await ctx.aclose()


# ------------------------------------------------------------------------ 7.5


def test_autonomy_follows_the_complexity_unless_the_factory_says_otherwise():
    assert autonomy_for(Complexity.SIMPLE) == Autonomy.YOLO
    assert autonomy_for(Complexity.STANDARD) == Autonomy.STANDARD
    assert autonomy_for(Complexity.COMPLEX) == Autonomy.PREFLIGHT
    assert autonomy_for(Complexity.COMPLEX, "standard") == Autonomy.STANDARD
    route = build_route(StoryKind.FEATURE, Complexity.COMPLEX, Autonomy.PREFLIGHT)
    assert route[route.index("plan") + 1] == "preflight"
    assert "preflight" not in build_route(StoryKind.FEATURE, Complexity.STANDARD)


async def test_a_yolo_story_skips_the_models_self_check(factory: Factory):
    dod = {"n": 0}

    def master(messages):
        if "Classify the story" in messages[0].content:
            return json.dumps({"kind": "feature", "complexity": "SIMPLE", "children": []})
        return None

    def count(messages):
        dod["n"] += 1
        return None

    ctx = make_ctx(factory, _script({"master": master, "dod": count}))
    sid = seed_story(ctx, "Trocar um texto")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.autonomy == Autonomy.YOLO and state.blocked_reason == "delivery"
    assert dod["n"] == 0
    await ctx.aclose()


# ------------------------------------------------------------------------ 7.6


def test_risk_facts_are_measured_in_the_repository(factory: Factory):
    root = factory.root
    (root / "app" / "db").mkdir()
    (root / "app" / "db" / "schema.sql").write_text("create table t (id int);\n")
    for i in range(5):
        (root / "app" / f"use{i}.py").write_text("from app.calc import add\n")
    (root / "app" / "lonely.py").write_text("X = 1\n")
    for i in range(3):
        (root / "app" / f"u{i}.py").write_text("import lonely\n")
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "more"], cwd=root, check=True)
    facts = {
        f.path: f for f in assess(root, ["app/calc.py", "app/lonely.py", "app/db/", "app/new.py"])
    }
    assert facts["app/calc.py"].tested and facts["app/calc.py"].dependents == 5
    assert facts["app/calc.py"].level == "medium"  # many users, but a test covers it
    assert not facts["app/lonely.py"].tested and facts["app/lonely.py"].level == "medium"
    assert (
        facts["app/db/schema.sql"].level == "high" and facts["app/db/schema.sql"].kind == "schema"
    )
    assert facts["app/new.py"].level == "low" and not facts["app/new.py"].exists
    assert (
        kind_of("migrations/0002_add.py") == "migration"
        and kind_of("api/openapi.yaml") == "contract"
    )
    assert needs_preflight(["src/", "migrations/"]) and not needs_preflight(["src/app.py"])


def test_declared_dependencies_are_read_from_the_manifests(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies=["typer>=0.12"]\n[dependency-groups]\ndev=["pytest"]\n'
    )
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^19"}}')
    text = declared_dependencies(tmp_path)
    assert "python: typer" in text and "pytest" in text and "node (dependencies): react" in text


def _schema_plan(extra: dict[str, Any] | None = None):
    def architect(messages):
        if "Pre-flight" in messages[0].content:
            return json.dumps(
                {
                    "risks": [
                        {
                            "area": "banco",
                            "risk": "coluna nova quebra leituras antigas",
                            "probability": "medium",
                            "impact": "high",
                            "mitigation": "valor padrão",
                        }
                    ],
                    "regression_checks": ["listar gastos antigos continua funcionando"],
                    "extra_tasks": ["Escrever teste de caracterização de app/calc.py"],
                    **(extra or {}),
                }
            )
        if "## Founder's guidance" in messages[-1].content:
            return None
        return json.dumps(
            {**PLAN, "files": ["app/", "migrations/", "loompa_dryrun/"], "traceability": []}
        )

    return architect


async def test_a_plan_touching_a_migration_gets_a_preflight(factory: Factory):
    ctx = make_ctx(factory, _script({"architect": _schema_plan()}))
    sid = seed_story(ctx, "Nova coluna")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.autonomy == Autonomy.PREFLIGHT and "preflight" in state.route
    assert state.blocked_reason == "delivery"
    risk = (factory.paths.specs / sid / "risk.md").read_text()
    assert "| app/calc.py |" in risk and "[medium/high] banco" in risk
    assert "listar gastos antigos" in risk
    tasks = (factory.paths.specs / sid / "tasks.md").read_text()
    assert "[x] T1: Escrever teste de caracterização" in tasks and "[x] T2: Ajustar" in tasks
    assert any(p.startswith("tests/") for p in state.allowed_paths)  # its tests are writable
    await ctx.aclose()


async def test_a_preflight_task_that_writes_no_file_is_left_out(factory: Factory):
    """`contas` S-031: "run the full suite and record a green baseline before the first commit"
    became a Worker task, twice. The engine measures the baseline and runs the suite itself."""
    architect = _schema_plan(
        {
            "extra_tasks": [
                "Rodar a suíte completa (pytest + ruff check) e registrar o baseline verde",
                "Escrever teste de caracterização de app/calc.py",
            ]
        }
    )
    ctx = make_ctx(factory, _script({"architect": architect}))
    sid = seed_story(ctx, "Nova coluna")
    await Scheduler(ctx).run()
    tasks = (factory.paths.specs / sid / "tasks.md").read_text()
    assert "baseline" not in tasks and "T1: Escrever teste de caracterização" in tasks
    await ctx.aclose()


async def test_a_test_file_a_preflight_task_names_joins_the_fence(factory: Factory):
    """contas Sprint 2, S-045: the plan already allowed one test file, so the pre-flight's
    `tests/test_caracterizacao_s045.py` stayed outside the fence and the Worker asked the founder."""

    def architect(messages):
        if "Pre-flight" in messages[0].content:
            return json.dumps(
                {
                    "risks": [],
                    "extra_tasks": [
                        "Criar tests/test_caracterizacao_s045.py fixando o total de app/calc.py"
                    ],
                }
            )
        if "## Founder's guidance" in messages[-1].content:
            return None
        files = ["app/", "migrations/", "tests/test_cli.py", "loompa_dryrun/"]
        return json.dumps({**PLAN, "files": files, "traceability": []})

    ctx = make_ctx(factory, _script({"architect": architect}))
    sid = seed_story(ctx, "Nova coluna")
    await Scheduler(ctx).run()
    fence = load_state(ctx, sid).allowed_paths
    assert "tests/test_caracterizacao_s045.py" in fence and "tests/test_cli.py" in fence
    assert "tests/" not in fence and "app/calc.py" not in fence  # only the named test file joins
    await ctx.aclose()


async def test_an_irreversible_change_asks_the_founder_first(factory: Factory):
    architect = _schema_plan(
        {
            "irreversible": True,
            "question": "Posso apagar a coluna antiga?",
            "options": ["Sim", "Não"],
        }
    )
    ctx = make_ctx(factory, _script({"architect": architect}))
    sid = seed_story(ctx, "Remover coluna")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "question" and state.resume_phase == "dev"
    await Scheduler(ctx).aanswer(state.blocked_message_id, FounderAnswer(text="Pode apagar"))
    await Scheduler(ctx).run()
    assert load_state(ctx, sid).blocked_reason == "delivery"
    await ctx.aclose()
