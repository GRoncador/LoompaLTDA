"""Uses of what a change touches (contas Sprint 2, S-047), the fence that follows the plan's own
tasks, and the Worker's refused file reviewed by the Architect before the founder hears of it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from loompa.agents.architect import files_named
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.agents.kaizen import without_label
from loompa.callers import callers_of_diff, callers_of_plan, changed_names
from loompa.engine import Scheduler, load_state
from loompa.factory import Factory
from loompa.llm import Message, ToolCall
from test_engine import factory, git, make_ctx, seed_story, tool_results  # noqa: F401

MODELS = """class Gasto(BaseModel):
    descricao: str
    valor: int
"""
CLI = """from contas.models import Gasto


def resumo(gastos):
    total = 0
    for gasto in gastos:
        total += gasto.valor
    return f"R$ {total:.2f}"


def nome(gasto):
    return gasto.descricao
"""
DIFF = """diff --git a/src/contas/models.py b/src/contas/models.py
--- a/src/contas/models.py
+++ b/src/contas/models.py
@@ -1,3 +1,3 @@
 class Gasto(BaseModel):
     descricao: str
-    valor: Decimal
+    valor: int
diff --git a/tests/test_models.py b/tests/test_models.py
--- a/tests/test_models.py
+++ b/tests/test_models.py
@@ -1,2 +1,2 @@
-def test_valor_decimal():
+def test_valor_centavos():
"""


def _repo(tmp_path: Path) -> Path:
    (tmp_path / "src" / "contas").mkdir(parents=True)
    (tmp_path / "src" / "contas" / "models.py").write_text(MODELS)
    (tmp_path / "src" / "contas" / "cli.py").write_text(CLI)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cli.py").write_text("def test_x(gasto):\n    gasto.valor\n")
    return tmp_path


def test_the_uses_of_a_changed_field_outside_the_diff_are_listed(tmp_path: Path):
    root = _repo(tmp_path)
    assert changed_names(DIFF) == ["valor", "Gasto"]  # product code only, not the test file
    out = callers_of_diff(root, DIFF)
    assert "src/contas/cli.py:7: total += gasto.valor" in out  # the stale use S-047 shipped
    assert "src/contas/cli.py:1: from contas.models import Gasto" in out
    assert "valor: int" not in out  # the diff's own line is already in front of the reader
    assert "tests/" not in out and "descricao" not in out


def test_the_preflight_sees_uses_outside_the_plan(tmp_path: Path):
    root = _repo(tmp_path)
    plan = "Trocar `valor` para centavos inteiros em Gasto."
    out = callers_of_plan(root, plan, ["src/contas/models.py"])
    assert "src/contas/cli.py:7 (outside the plan): total += gasto.valor" in out
    assert callers_of_plan(root, "Nada a ver", ["src/contas/models.py"]) == ""


def test_a_task_that_asks_to_change_a_file_opens_it(tmp_path: Path):
    root = _repo(tmp_path)
    tasks = [
        "Em src/contas/cli.py, ajustar apenas formatar_reais para aceitar centavos",
        "Em src/contas/models.py: trocar valor para int. Não altere src/contas/cli.py.",
        "Ler src/contas/cli.py para entender o fluxo",
        "Criar src/contas/novo.py com a função x",
        "Atualizar src/naoexiste/x.py",
        "Add a test in tests/test_novo.py",
    ]
    assert files_named(tasks, ["src/contas/models.py"], root) == [
        "src/contas/cli.py",
        "src/contas/novo.py",
    ]
    assert files_named(tasks[1:3], ["src/contas/models.py"], root) == []
    assert files_named(tasks, ["src/"], root) == []  # already covered


def test_a_finding_title_carries_its_label_once():
    title = "[Inconsistência de arquitetura] [Inconsistência de arquitetura] Self-check de higiene"
    assert without_label(title) == "Self-check de higiene"
    assert without_label("[architecture] x") == "x"
    assert without_label("[S-047] cli.py fora do plano") == "[S-047] cli.py fora do plano"


async def test_the_inspector_sees_the_uses_the_diff_left_behind(factory: Factory):
    root = factory.root
    (root / "app" / "ui.py").write_text(
        "from app.calc import add\n\n\ndef total(x):\n    return add(x, 1)\n"
    )
    git("add", ".", cwd=root)
    git("commit", "-qm", "feat: ui", cwd=root)
    judged: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role == "inspector" and "## Diff" in messages[-1].content:
            judged.append(messages[-1].content)
        if role == "architect" and "Pre-flight" not in messages[0].content:
            plan = json.loads(dry_run_script(model, messages, tools))
            if "files" in plan and "approach" in plan:  # the plan itself: allow the change
                return json.dumps({**plan, "files": ["app/calc.py", "tests/"]})
        if role == "worker" and not tool_results(messages):
            return [
                ToolCall("r", "read_file", {"path": "app/calc.py"}),
            ]
        if role == "worker" and len(tool_results(messages)) == 1:
            return [
                ToolCall(
                    "e",
                    "edit_file",
                    {"path": "app/calc.py", "old": "return a + b", "new": "return b + a"},
                )
            ]
        if role == "worker":
            return [ToolCall("d", "done", {"summary": "centavos"})]
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    seed_story(ctx, "Somar em centavos")
    await Scheduler(ctx).run()
    assert judged, "the Inspector never judged"
    assert "## Uses outside the diff" in judged[-1]
    assert "app/ui.py:5: return add(x, 1)" in judged[-1]
    await ctx.aclose()


async def test_a_refused_file_goes_to_the_architect_before_the_founder(factory: Factory):
    """S-047: task 4 needed cli.py, the plan left it out, and the founder got a finding about it.
    The Architect now reviews the plan once with the Worker's reason, and the Worker goes on."""
    (factory.root / "app" / "ui.py").write_text("X = 1\n")
    git("add", ".", cwd=factory.root)
    git("commit", "-qm", "feat: ui", cwd=factory.root)
    amends: list[str] = []
    runs: list[int] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        last = messages[-1].content
        if role == "architect" and "## The Worker's request" in last:
            amends.append(last)
            return json.dumps({"files": ["app/ui.py"], "tasks": [], "reason": "a tarefa precisa"})
        if role == "worker":
            results = tool_results(messages)
            if not results:
                runs.append(1)
                return [ToolCall("r", "read_file", {"path": "app/ui.py"})]
            if len(results) == 1:
                return [ToolCall("w", "write_file", {"path": "app/ui.py", "content": "X = 2\n"})]
            if "outside the plan's paths" in results[-1]:
                return [ToolCall("b", "blocked", {"reason": "preciso mudar app/ui.py"})]
            return [ToolCall("d", "done", {"summary": "ui ajustada"})]
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Ajustar UI")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert len(amends) == 1 and "app/ui.py" in amends[0]
    assert "app/ui.py" in state.allowed_paths and len(runs) == 2
    assert state.blocked_reason == "delivery", state.failure_history
    assert (Path(state.worktree) / "app" / "ui.py").read_text() == "X = 2\n"
    events = [
        e for e in ctx.store.events_since(0, limit=10_000) if e["type"] == "plan.fence_review"
    ]
    assert events and events[0]["payload"]["granted"] == ["app/ui.py"]
    await ctx.aclose()
