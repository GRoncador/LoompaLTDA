"""ADR-0037 §5: the factory's records never become a story's work, in code at every step.

tamagotchi SP-013: S-077 and S-087 wrote tests that read `.loompa/`, the Inspector passed them
(the files were in the worktree) and the base went red after each merge; S-094's spec then asked
to create the records, and its plan kept the tasks that wrote them after the gate refused them."""

from __future__ import annotations

import json
from typing import Any

from conftest import git
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.engine import Scheduler, Stage, load_state
from loompa.factory import Factory
from loompa.hygiene import scan_diff
from loompa.llm import Message
from loompa.plan_check import record_tasks, records_in_criteria
from test_engine import factory, make_ctx, seed_story  # noqa: F401

RECORD = ".loompa/decisions/0003-s-077-pwa.md"


def test_criteria_that_name_a_record_are_found_and_a_product_one_is_not():
    criteria = [
        f"Dado que `{RECORD}` não existe hoje, quando a suíte roda, então passa.",
        "Dado um pedido a `.loompa/config.yaml`, o servidor responde 404.",
        "O texto de .loompa/specs/S-077/plan.md cita a S-087.",
        "A tela mostra o pet.",
    ]
    assert records_in_criteria(criteria) == [(1, RECORD), (3, ".loompa/specs/S-077/plan.md")]
    problems = [
        f"task 2 writes {RECORD}: the factory keeps its records under .loompa/ itself",
        "task 3 has a test read .loompa/decisions: the factory's records are not part",
        "task 4 writes specs/S-1/tasks.md, a copy of the factory's own records in the product",
        "task 5 changes tests/x.js, which is not in the repository",
    ]
    assert record_tasks(problems) == [2, 3, 4]


def test_a_test_that_opens_a_record_is_a_blocking_hygiene_issue():
    diff = (
        "diff --git a/tests/casca_body.test.js b/tests/casca_body.test.js\n"
        "new file mode 100644\n--- /dev/null\n+++ b/tests/casca_body.test.js\n"
        "@@ -0,0 +1,3 @@\n"
        "+function lerSpec(nome) {\n"
        "+  return readFileSync(join(RAIZ, '.loompa', 'specs', 'S-077', nome), 'utf8');\n"
        "+}\n"
        "diff --git a/tests/servidor.test.js b/tests/servidor.test.js\n"
        "new file mode 100644\n--- /dev/null\n+++ b/tests/servidor.test.js\n"
        "@@ -0,0 +1 @@\n"
        "+  const res = await fetch(base + '/.loompa/config.yaml');\n"
    )
    issues = scan_diff(diff)
    assert [(i.path, i.rule, i.blocking) for i in issues] == [
        ("tests/casca_body.test.js", "reads_records", True)
    ]


def test_the_inspector_judges_the_tree_that_will_land(factory: Factory):  # noqa: F811
    """S-087 edited `.loompa/specs/S-077/plan.md` in its worktree and a new test read it: green
    there, red on the base after the merge (AC6)."""
    ctx = make_ctx(factory)
    (factory.root / ".loompa" / "specs" / "S-077").mkdir(parents=True, exist_ok=True)
    (factory.root / ".loompa" / "specs" / "S-077" / "plan.md").write_text("plano\n")
    git("add", "-f", ".loompa/specs/S-077/plan.md", cwd=factory.root)
    git("commit", "-qm", "docs: plano", cwd=factory.root)
    wt = ctx.worktrees.create("S-1", title="x")
    plan = wt.path / ".loompa" / "specs" / "S-077" / "plan.md"
    plan.write_text("plano citando S-087\n")
    (wt.path / ".loompa" / "decisions").mkdir(parents=True, exist_ok=True)
    (wt.path / RECORD).write_text("decisão\n")
    deployer = ctx.worktrees.as_role("deployer")
    undone = deployer.reset_factory_records(wt)
    assert sorted(undone) == [RECORD, ".loompa/specs/S-077/plan.md"]
    assert plan.read_text() == "plano\n" and not (wt.path / RECORD).exists()
    assert deployer.reset_factory_records(wt) == []


async def test_criteria_asking_for_records_go_back_to_the_spec_once(
    factory: Factory,  # noqa: F811
):
    specs: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "product" and "Spec Loompa" in messages[0].content:
            specs.append(messages[-1].content)
            first = len(specs) == 1
            return json.dumps(
                {
                    "goal": "Consertar a suíte",
                    "in_scope": ["a suíte"],
                    "out_of_scope": [],
                    "acceptance": [
                        f"Dado que `{RECORD}` não existe, quando a suíte roda, então passa."
                        if first
                        else "Dado o teste reescrito, quando a suíte roda, então passa.",
                    ],
                    "rules": [],
                    "questions": [],
                    "needs_decision": False,
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Consertar a versão principal")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert len(specs) == 2 and "The factory's checks refused these criteria" in specs[1]
    assert f"criterion 1 names {RECORD}" in specs[1]
    assert state.acceptance == ["Dado o teste reescrito, quando a suíte roda, então passa."]
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    assert not any(p.startswith(".loompa/") for p in state.allowed_paths)
    await ctx.aclose()


async def test_a_spec_that_is_only_records_after_its_rewrite_closes_as_resolved(
    factory: Factory,  # noqa: F811
):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "product" and "Spec Loompa" in messages[0].content:
            return json.dumps(
                {
                    "goal": "x",
                    "acceptance": [f"Dado `{RECORD}` criado, então o teste passa."],
                    "needs_decision": False,
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Restaurar os documentos de decisão")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.CANCELLED and state.extra["outcome"]["kind"] == "resolved"
    events = [e["type"] for e in ctx.store.events_since(0, limit=10_000) if e["story_id"] == sid]
    assert events.count("spec.records") == 1 and "plan.checked" not in events
    await ctx.aclose()


async def test_a_plan_that_keeps_writing_records_sends_the_spec_back_then_drops_those_tasks(
    factory: Factory,  # noqa: F811
):
    """S-094's plan wrote `.loompa/decisions/…` in both rounds of the gate and went to the Worker
    anyway, who asked the founder about it eight times."""
    from loompa.speckit import story_dir

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "architect" and "Founder's guidance" not in messages[-1].content:
            plan = json.loads(dry_run_script(model, messages, tools))
            plan["files"] = ["loompa_dryrun/", "tests/"]
            plan["tasks"] = [
                f"Criar {RECORD} com a decisão da S-077",
                "Registrar a entrega em loompa_dryrun/x.md (simulação)",
            ]
            return json.dumps(plan)
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Consertar a versão principal")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    events = [e["type"] for e in ctx.store.events_since(0, limit=10_000) if e["story_id"] == sid]
    assert events.count("plan.records_to_spec") == 1
    assert events.count("plan.records_withdrawn") == 1
    assert state.extra["tasks_withdrawn"] == [1]
    tasks = story_dir(factory.root, sid).tasks.read_text()
    assert "[x] T1" in tasks and "retirada pelo ajuste" in tasks
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    await ctx.aclose()


def test_a_story_has_one_proposed_decision_rewritten_by_its_replan(factory: Factory):  # noqa: F811
    """ADR-0037 §9: S-094's two plans left `0004-s-094-…` and `0005-s-094-…`, and the Architect
    read both back as precedents of itself."""
    from loompa.agents import ArchitectAgent

    ctx = make_ctx(factory)
    arch = ArchitectAgent(ctx)
    first = arch.write_adr("S-094: Consertar", "primeira", status="proposed", story_id="S-094")
    again = arch.write_adr("S-094: Consertar", "segunda", status="proposed", story_id="S-094")
    other = arch.write_adr("S-095: Outra", "x", status="proposed", story_id="S-095")
    assert first == again and other != first
    from pathlib import Path

    assert "segunda" in Path(again).read_text() and "primeira" not in Path(again).read_text()
    assert len(list(factory.paths.decisions.glob("*.md"))) == 2


# The tasks the gate refused in tamagotchi S-101 and S-106 (11/10), word for word: each names the
# record only to say the copy leaves it out, the read is removed, or how the suite fails today.
# The gate withdrew every one of them; S-101 closed as "resolved" over a red base.
S101_S106_TASKS = [
    "Hoje ele falha: o AC6 executa readFileSync de .loompa/specs/S-077/plan.md, inexistente na "
    "árvore, e o sub-processo retorna status diferente de 0. Arquivo: tests/casca_body_scripts.test.js",
    "Criar tests/remocao_ac6_casca_body_scripts.test.js (arquivo novo, só testes): um it() que "
    "executa 'node --test tests/casca_body_scripts.test.js' como sub-processo via spawnSync — é o "
    "reprodutor do bug: hoje o sub-processo falha porque o AC6 lança ENOENT em "
    ".loompa/specs/S-077/plan.md, então este teste novo está VERMELHO no código atual.",
    "(a) tests/casca_body_scripts_autossuficiencia.test.js — reprodutor do bug: copia a árvore do "
    "repositório para um diretório temporário excluindo entradas ocultas (sem .loompa/), com "
    "preâmbulos assertando que a cópia tem tests/casca_body_scripts.test.js e não tem .loompa/;",
    "Em tests/casca_body_scripts.test.js remover o it do AC6 (que lia .loompa/specs/S-077/plan.md "
    "e tasks.md), a função lerSpecS077 e a função normalizarEspacos (ficam sem uso);",
    "Reescrever o AC6 em tests/casca_body_scripts.test.js: remover lerSpecS077, normalizarEspacos "
    "e todo readFileSync de .loompa/specs/S-077 (plan.md/tasks.md) e o comentário de cabeçalho "
    "que descreve o reprodutor antigo;",
    "Verificação final da suíte: rodar `npm test` e confirmar exit 0; confirmar por busca que "
    "nenhum arquivo de tests/ lê caminho sob .loompa/ (só restam as strings de afirmação de "
    "ausência em tests/decisoes_s077_autossuficiencia.test.js) e que nada em tests/ escreve em "
    ".loompa/ (critério 6);",
    "(c) atualizar o cabeçalho de comentários de tests/casca_body_scripts.test.js para descrever "
    "os testes atuais sem citar a leitura de .loompa/specs/S-077/plan.md e tasks.md;",
]


def test_a_task_that_takes_a_records_read_out_of_a_test_is_not_one_that_reads_it():
    from loompa.plan_check import factory_records_in_tasks

    assert factory_records_in_tasks(S101_S106_TASKS) == []
    # what the gate is for still counts (S-077: a new test asserting what a decision says)
    found = factory_records_in_tasks(
        [
            "test: cria o arquivo novo tests/decisoes_s077.test.js, cobrindo CA1 (o texto de "
            f"{RECORD} contém 'S-015')"
        ]
    )
    assert [p.split(":")[0] for p in found] == [f"task 1 has a test read {RECORD}"]


async def test_a_plan_left_without_tasks_asks_the_founder_instead_of_closing(
    factory: Factory,  # noqa: F811
):
    """tamagotchi S-101: its plan's tasks were all withdrawn for the records, `resolve` found no
    line failures to run and closed it as resolved; the base stayed red."""

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "architect" and "Founder's guidance" not in messages[-1].content:
            plan = json.loads(dry_run_script(model, messages, tools))
            plan["files"] = ["tests/"]
            plan["tasks"] = [
                f"Criar tests/decisoes.test.js que confere o texto de {RECORD}",
                f"Ajustar tests/casca.test.js para comparar com {RECORD}",
            ]
            return json.dumps(plan)
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Trocar o critério AC6 por teste de comportamento")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    events = [e["type"] for e in ctx.store.events_since(0, limit=10_000) if e["story_id"] == sid]
    assert "plan.records_withdrawn" in events and "story.resolved" not in events
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "question"
    assert state.resume_phase == "plan"
    msg = ctx.store.get_message(state.blocked_message_id)
    assert msg.title.endswith("precisa de um plano novo")
    assert "não foi encerrada" in msg.impact and msg.executive_audit() == []
    assert [o.key for o in msg.options] == ["opt1", "skip", "drop"]
    await ctx.aclose()


def test_a_test_that_checks_the_records_are_absent_does_not_read_them():
    """tamagotchi S-106: the copy test asserted the records folder is not in its copy."""
    lines = [
        "+        !existsSync(join(destino, '.loompa')),",
        "+    assert.equal(existsSync(join(destino, '.loompa')), false);",
        "+    assert not (copia / '.loompa').exists()",
    ]
    diff = (
        "diff --git a/tests/suite_sem_registros.test.js b/tests/suite_sem_registros.test.js\n"
        "new file mode 100644\n--- /dev/null\n+++ b/tests/suite_sem_registros.test.js\n"
        "@@ -0,0 +1,3 @@\n" + "\n".join(lines) + "\n"
    )
    assert scan_diff(diff) == []
    reads = diff.replace("!existsSync(join(destino", "readFileSync(join(RAIZ")
    assert [i.rule for i in scan_diff(reads)] == ["reads_records"]
