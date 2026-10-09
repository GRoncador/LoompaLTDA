"""ADR-0026: the plan's own gate — checks in code between `plan` and `dev`, a review call only
when the plan asks for one, and the plan promoting a story intake under-labelled."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loompa.agents.dryrun import dry_run_script, role_of
from loompa.engine import Scheduler, load_state
from loompa.engine.phases import build_route
from loompa.engine.state import Autonomy, Complexity, StoryKind
from loompa.factory import Factory
from loompa.llm import Message
from loompa.plan_check import check_plan
from test_engine import factory, make_ctx, seed_story  # noqa: F401
from test_preflight import PLAN, SPEC

# ------------------------------------------------------------------- the checks in code


def test_a_criterion_with_no_planned_test_is_a_problem(tmp_path: Path):
    plan = "## Rastreabilidade\n- Critério 1 → tests/t.py::test_a\n- Critério 2 → **sem teste planejado**\n"
    report = check_plan(
        tmp_path,
        acceptance=["a", "b"],
        files=["app/"],
        tasks=["Criar app/x.py"],
        plan_md=plan,
    )
    assert report.problems == ["acceptance criterion 2 has no test planned for it"]
    assert not report.ok


def test_a_criterion_the_traceability_skipped_is_a_problem(tmp_path: Path):
    report = check_plan(
        tmp_path,
        acceptance=["a", "b", "c"],
        files=["app/"],
        tasks=["Criar app/x.py"],
        plan_md="- Critério 1 → tests/t.py::test_a\n",
    )
    assert report.problems == [
        "acceptance criterion 2 is missing from the plan's traceability",
        "acceptance criterion 3 is missing from the plan's traceability",
    ]


def test_a_change_the_program_never_runs_is_traced_to_the_review(tmp_path: Path):
    """tamagotchi S-051 (plan 12.1): four stale comments, and 185 lines of tests that read the
    test file as text, because the gate wanted a test per criterion."""
    plan = (
        "- Critério 1 → review: os comentários citam y 61..120\n"
        "- Critério 2 → tests/consequencias.test.js (a suíte continua verde)\n"
    )

    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "consequencias.test.js").write_text("// y 56..66\n")

    def problems(files: list[str]) -> list[str]:
        return check_plan(
            tmp_path,
            acceptance=["a", "b"],
            files=files,
            tasks=["Editar os comentários de tests/consequencias.test.js"],
            plan_md=plan,
        ).problems

    assert problems(["tests/consequencias.test.js"]) == []
    assert problems(["README.md", "tests/"]) == []
    assert problems(["tests/consequencias.test.js", "src/sprites.js"]) == [
        "acceptance criterion 1 is traced to the review, but the story changes product code: "
        "plan a test for it"
    ]
    from loompa.agents.architect import SIMPLE_SYSTEM, SYSTEM

    for prompt in (SYSTEM, SIMPLE_SYSTEM):
        assert "review: <what the diff must show>" in " ".join(prompt.split())


def test_a_task_that_names_no_file_is_a_problem(tmp_path: Path):
    report = check_plan(
        tmp_path,
        acceptance=[],
        files=["app/"],
        tasks=["Criar app/x.py com o cálculo", "Rodar a suíte e conferir o resultado"],
        plan_md="",
    )
    assert report.problems == ["task 2: names no file (Rodar a suíte e conferir o resultado)"]


def test_changing_a_file_that_does_not_exist_is_a_problem_but_creating_one_is_not(tmp_path: Path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "calc.py").write_text("x = 1\n")
    report = check_plan(
        tmp_path,
        acceptance=[],
        files=["app/"],
        tasks=[
            "Ajustar app/calc.py para somar",  # exists
            "Criar app/novo.py com o parser",  # new file: not a problem
            "Atualizar app/antigo.py para usar o parser",  # changes what is not there
            "Sem tocar em app/legado.py, escrever app/outro.py",  # explicitly left alone
        ],
        plan_md="",
    )
    assert report.problems == ["task 3 changes app/antigo.py, which is not in the repository"]


def test_new_tests_appended_to_a_long_test_file_are_a_problem(tmp_path: Path):
    """ADR-0030: four stories appended a describe block each to tamagotchi's casca.test.js and
    sprites.test.js (up to 2,100 lines), so every pair of parallel stories conflicted at merge and
    the Worker re-read the file whole on each task. New tests go in a file of their own."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "casca.test.js").write_text("// x\n" * 900)
    (tmp_path / "tests" / "curto.test.js").write_text("// x\n" * 40)
    report = check_plan(
        tmp_path,
        acceptance=[],
        files=["tests/"],
        tasks=[
            "Em tests/casca.test.js criar o describe('TestCascaCarcaOvo') com DOM falso",  # appends
            "Em tests/casca.test.js ajustar o teste do critério 3",  # changes what is there
            "Criar tests/casca-retorno.test.js com os testes da história",  # its own file
            "Em tests/curto.test.js acrescentar o teste do critério 2",  # short file: fine
        ],
        plan_md="",
    )
    assert report.problems == [
        "task 1 adds tests to tests/casca.test.js, already 900 lines long: put this story's new "
        "tests in a new test file of its own, next to it and named so the test runner finds it"
    ]


def test_the_plan_prompts_ask_for_a_test_file_per_story_and_proportional_tests():
    from loompa.agents.architect import SIMPLE_SYSTEM, SYSTEM
    from loompa.agents.worker import SYSTEM as WORKER

    for prompt in (SYSTEM, SIMPLE_SYSTEM):
        text = " ".join(prompt.split())
        assert "new test file of its own" in text and "390 px" in text
        # edge cases stay (limits and error paths); what goes is what pins the implementation
        assert (
            "the limits and the error paths" in text and "an implementation detail pinned" in text
        )
        assert "Respond with JSON only" in text
    worker = " ".join(WORKER.split())
    assert "limits and error paths" in worker and "an implementation detail pinned" in worker


def test_a_plan_bigger_than_its_label_raises_a_signal(tmp_path: Path):
    small = check_plan(
        tmp_path, acceptance=[], files=["app/"], tasks=["Criar app/x.py"], plan_md=""
    )
    assert small.ok and not small.signals
    big = check_plan(
        tmp_path,
        acceptance=[],
        files=[f"app/m{i}.py" for i in range(9)] + [".loompa/specs/S-001/"],
        tasks=[f"Criar app/m{i}.py" for i in range(7)],
        plan_md="",
    )
    assert big.ok  # nothing is wrong with it: it is simply not a STANDARD story
    assert big.signals == ["7 tasks (over 6)", "9 paths in the fence (over 8)"]


def test_the_gate_sits_between_the_plan_and_the_work():
    route = build_route(StoryKind.FEATURE, Complexity.STANDARD)
    assert route[route.index("plan") + 1] == "plan_review"
    assert route[route.index("plan_review") + 1] == "dev"
    # even a SIMPLE story keeps it: the checks cost nothing and the review call is by evidence
    assert "plan_review" in build_route(StoryKind.FEATURE, Complexity.SIMPLE)
    assert "plan_review" in build_route(StoryKind.BUGFIX, Complexity.STANDARD)
    # and the pre-flight comes after it, so a refused plan is not pre-flighted twice
    deep = build_route(StoryKind.FEATURE, Complexity.COMPLEX, Autonomy.PREFLIGHT)
    assert deep.index("plan_review") < deep.index("preflight") < deep.index("dev")


# ------------------------------------------------------------------- the gate in the engine


def _gate_script(plan: dict[str, Any], *, complexity: str = "STANDARD") -> Any:
    seen: dict[str, list[str]] = {"plan_review": [], "plans": []}

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role == "master" and "Classify the story" in messages[0].content:
            return json.dumps({"kind": "feature", "complexity": complexity, "children": []})
        if role == "product":
            return json.dumps(SPEC)
        if role == "architect" and "## Repository outline" in messages[1].content:
            seen["plans"].append(messages[-1].content)
            return json.dumps(plan)
        if role == "product_owner" and "Review the plan and task list" in messages[0].content:
            seen["plan_review"].append(messages[-1].content)
            return json.dumps({"approved": True, "unsupported": [], "missing": [], "notes": "ok"})
        return dry_run_script(model, messages, tools)

    script.seen = seen  # type: ignore[attr-defined]
    return script


async def test_a_standard_plan_that_passes_the_checks_is_not_reviewed_by_a_model(factory: Factory):
    plan = {
        **PLAN,
        "files": ["loompa_dryrun/", "tests/"],
        "traceability": [
            {"criterion": 1, "test": "tests/test_x.py::test_a"},
            {"criterion": 2, "test": "tests/test_x.py::test_b"},
        ],
        "tasks": [{"task": "Criar loompa_dryrun/x.md", "verify": "pytest"}],
        "constitution_check": [],
    }
    script = _gate_script(plan)
    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "História padrão")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.plan_review_rounds == 1 and script.seen["plan_review"] == []
    assert len(script.seen["plans"]) == 1  # planned once: nothing sent it back
    assert state.blocked_reason == "delivery"
    await ctx.aclose()


async def test_a_hole_in_the_traceability_sends_the_plan_back_once(factory: Factory):
    # PLAN covers criterion 1 only, and SPEC has two
    script = _gate_script({**PLAN, "files": ["loompa_dryrun/", "tests/"]})
    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Critério sem teste")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert len(script.seen["plans"]) == 2  # the gate asked for a second plan
    assert "fix exactly these" in script.seen["plans"][1].lower()
    assert "criterion 2 has no test planned" in script.seen["plans"][1]
    # the second plan has the same hole: the story goes on and carries it
    assert state.plan_review_rounds == 2
    assert any("Plan checks still failing" in f for f in state.failure_history)
    # and the review was called this time, with the measured problems as facts
    assert script.seen["plan_review"] and "Automatic checks" in script.seen["plan_review"][0]
    await ctx.aclose()


async def test_a_complex_story_always_pays_for_the_review(factory: Factory):
    plan = {
        **PLAN,
        "files": ["loompa_dryrun/", "tests/"],
        "traceability": [
            {"criterion": 1, "test": "tests/test_x.py::test_a"},
            {"criterion": 2, "test": "tests/test_x.py::test_b"},
        ],
        "tasks": [{"task": "Criar loompa_dryrun/x.md", "verify": "pytest"}],
        "constitution_check": [],
    }
    script = _gate_script(plan, complexity="COMPLEX")
    ctx = make_ctx(factory, script)
    seed_story(ctx, "História complexa")
    await Scheduler(ctx).run()
    assert len(script.seen["plan_review"]) == 1
    assert "## Acceptance criteria\n1. " in script.seen["plan_review"][0]
    await ctx.aclose()


async def test_a_plan_bigger_than_its_label_promotes_the_story(factory: Factory):
    plan = {
        **PLAN,
        "files": ["loompa_dryrun/", "tests/"],
        "traceability": [
            {"criterion": 1, "test": "tests/test_x.py::test_a"},
            {"criterion": 2, "test": "tests/test_x.py::test_b"},
        ],
        "tasks": [
            {"task": f"Criar loompa_dryrun/parte{i}.md", "verify": "pytest"} for i in range(7)
        ],
        "constitution_check": [],
    }
    script = _gate_script(plan, complexity="STANDARD")
    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Maior do que parecia")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    # the plan is better evidence of size than the guess made before the code was read
    assert state.complexity == Complexity.COMPLEX
    assert state.autonomy == Autonomy.PREFLIGHT and "preflight" in state.route
    assert state.route.index("plan_review") < state.route.index("preflight")
    assert script.seen["plan_review"]  # and it earned the review call
    await ctx.aclose()


def test_test_files_do_not_make_a_plan_big(tmp_path: Path):
    """12.1, S-061: three source files plus six old test files was promoted to COMPLEX."""
    files = ["src/a.js", "src/b.js", "src/c.js", *(f"tests/t{i}.test.js" for i in range(6))]
    files += [".loompa/specs/S-061/"]
    report = check_plan(tmp_path, acceptance=[], files=files, tasks=["Criar src/a.js"], plan_md="")
    assert report.signals == []


def test_a_clause_that_creates_a_file_and_adjusts_others_is_not_a_missing_file(tmp_path: Path):
    """12.1, S-061: "create tests/novo_ovo_lcd.test.js and adjust the old tests" was read as
    changing a missing file; carried into the history, the Worker made an empty stub."""
    report = check_plan(
        tmp_path,
        acceptance=[],
        files=["tests/"],
        tasks=["Criar tests/novo_ovo_lcd.test.js com os testes novos e ajustar os testes antigos"],
        plan_md="",
    )
    assert report.problems == []


def test_tasks_that_write_or_test_the_factorys_records_are_refused(tmp_path: Path):
    """ADR-0036 §5, tamagotchi S-077: two tasks wrote ADRs under .loompa/decisions and two tests
    read them; the merge left .loompa/ out of the base as it always does, and the base went red.
    S-068/S-069/S-070/S-076/S-089 each spent a Worker task copying their checklist into
    specs/S-0xx/tasks.md in the product."""
    from loompa.plan_check import factory_records_in_tasks

    tasks = [
        "docs: cria .loompa/decisions/0003-s-077-pwa.md (registra a inversão da S-015)",
        "test: cria o arquivo novo tests/decisoes_s077.test.js, cobrindo CA1 (o texto de "
        ".loompa/decisions/0003-s-077-pwa.md contém 'S-015')",
        "Cria `specs/S-070/tasks.md` (arquivo novo) com a matriz critério → tarefa → teste",
        "Ajusta src/link_pet.js seguindo .loompa/decisions/0002-stack.md (não altere o ADR)",
        "Cria src/modo_exibicao.js (arquivo novo, módulo ES puro)",
    ]
    found = factory_records_in_tasks(tasks)
    assert [p.split(":")[0] for p in found] == [
        "task 1 writes .loompa/decisions/0003-s-077-pwa.md",
        "task 2 has a test read .loompa/decisions/0003-s-077-pwa.md",
        "task 3 writes specs/S-070/tasks.md, a copy of the factory's own records in the product",
    ]


def test_a_criterion_about_a_record_may_be_traced_to_the_review_in_a_code_story():
    from loompa.plan_check import criteria_without_test

    plan = (
        "- Critério 1 → review: a decisão nomeia a S-015 e o escopo novo\n"
        "- Critério 2 → review: o service worker guarda os arquivos\n"
    )
    criteria = [
        "A decisão registra a inversão da nota da S-015",
        "O service worker guarda os arquivos da página",
    ]
    assert criteria_without_test(plan, criteria, ["src/sw.js"]) == [
        "acceptance criterion 2 is traced to the review, but the story changes product code: "
        "plan a test for it"
    ]
