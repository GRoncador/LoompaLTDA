"""End-to-end engine tests driven by scripted providers (no network)."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from conftest import git
from loompa.agents import MasterAgent, ProductOwnerAgent
from loompa.agents.dryrun import dry_run_script, role_of
from loompa.comms import FounderAnswer, MessageStatus
from loompa.engine import EngineContext, Scheduler, Stage, load_state
from loompa.factory import Factory, bootstrap_factory
from loompa.llm import LLMError, Message, MockProvider, ModelRouter, ToolCall
from loompa.store import Store

PYTEST_CMD = f'"{sys.executable}" -m pytest -q -p no:cacheprovider'


@pytest.fixture
def factory(git_repo: Path, hub) -> Factory:
    (git_repo / "app").mkdir()
    (git_repo / "app" / "__init__.py").write_text("")
    (git_repo / "app" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_calc.py").write_text(
        "from app.calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n"
    )
    git("add", ".", cwd=git_repo)
    git("commit", "-qm", "feat: calc", cwd=git_repo)
    result = bootstrap_factory(git_repo, name="Demo", store=hub)
    f = result.factory
    f.config.quality.test_command = PYTEST_CMD
    f.config.quality.lint_command = ""
    f.config.quality.typecheck_command = ""
    f.config.schedule.max_parallel = 2
    f.config.schedule.ops_retry_base_s = 0.01
    f.save()
    return Factory.open(git_repo)


def make_ctx(
    factory: Factory, script: Callable[..., Any] | None = None, *, dry_run: bool = False
) -> EngineContext:
    provider = MockProvider("mock", script=script or dry_run_script)
    router = ModelRouter(
        factory.config, providers=dict.fromkeys(factory.config.providers, provider)
    )
    return EngineContext.build(factory, router=router, dry_run=dry_run)


def with_worker(worker_fn: Callable[[str, list[Message]], Any]) -> Callable[..., Any]:
    def script(model: str, messages: list[Message], tools: list[dict[str, Any]] | None) -> Any:
        role = role_of(messages)
        if role == "worker":
            return worker_fn(model, messages)
        return dry_run_script(model, messages, tools)

    return script


def seed_story(ctx: EngineContext, title: str, description: str = "") -> str:
    """A story already cleared to run: the Product Owner adds the card and admits it."""
    po = ProductOwnerAgent(ctx)
    sid = po.add_item(title, description).story_id
    po.admit(sid)
    return sid


def tool_results(messages: list[Message]) -> list[str]:
    return [m.content for m in messages if m.role == "tool"]


# ------------------------------------------------------------------------ happy path


async def test_dry_run_pipeline_delivers_and_founder_approves(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    master = MasterAgent(ctx)
    result = await master.meeting("Página de login; Exportar relatório em CSV")
    assert [s["title"] for s in result["stories"]] == [
        "Página de login",
        "Exportar relatório em CSV",
    ]
    assert await Scheduler(ctx).run() == []  # nothing runs until a sprint starts
    master.start_sprint()
    done = await Scheduler(ctx).run()
    assert sorted(done) == ["S-001", "S-002"]
    for sid in ("S-001", "S-002"):
        state = load_state(ctx, sid)
        assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
        assert (
            state.spec_ready
            and state.plan_ready
            and state.tasks_done == [1]
            and len(state.commits) == 1
        )
        specs = factory.paths.specs / sid
        assert (
            (specs / "spec.md").is_file()
            and (specs / "plan.md").is_file()
            and "[x] T1" in (specs / "tasks.md").read_text()
        )
        assert (Path(state.worktree) / "loompa_dryrun").is_dir()
    msgs = [
        m for m in ctx.store.list_messages(factory.slug, status="pending") if m.kind == "delivery"
    ]
    assert len(msgs) == 2 and all(m.executive_audit() == [] for m in msgs)
    # stories are isolated: two worktrees, two branches, root untouched
    assert len(ctx.worktrees.list()) == 2 and not (factory.root / "loompa_dryrun").exists()
    # founder approves one delivery in the evening review
    approve = next(m for m in msgs if m.story_id == "S-001")
    state = await Scheduler(ctx).aanswer(approve.id, FounderAnswer(option_key="approve"))
    assert state.stage == Stage.DONE and state.merged_sha
    assert (factory.root / "loompa_dryrun").is_dir()
    assert "feat(s-001)" in git("log", "--oneline", "-1", cwd=factory.root)
    assert ctx.worktrees.get("S-001") is None and ctx.worktrees.get("S-002") is not None
    # and asks for changes on the other
    changes = next(m for m in msgs if m.story_id == "S-002")
    state = await Scheduler(ctx).aanswer(
        changes.id, FounderAnswer(option_key="changes", text="quero também o formato Excel")
    )
    assert state.stage == Stage.DEV and "Excel" in state.founder_notes[0]
    assert ctx.store.checkpoints("S-002")[-1]["node"] == "founder_answer"
    # usage was metered per story
    assert ctx.store.usage_totals(factory.slug, story_id="S-001")["calls"] >= 3
    await ctx.aclose()


async def test_a_repo_without_commits_gets_its_first_one_from_the_deployer(tmp_path: Path, hub):
    """The `contas` factory: files on disk, `git init` done, nothing ever committed. Two stories
    dispatched together must not bounce off 'faça o primeiro commit' — the Deployer makes it,
    once, and the founder gets one calm note instead of a question."""
    repo = tmp_path / "contas"
    (repo / "app").mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=repo)
    (repo / "app" / "__init__.py").write_text("")
    (repo / "app" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    f = bootstrap_factory(repo, name="Contas", store=hub).factory
    f.config.quality.test_command = ""
    f.config.quality.lint_command = ""
    f.config.quality.typecheck_command = ""
    f.config.schedule.max_parallel = 2
    f.save()
    ctx = make_ctx(Factory.open(repo), dry_run=True)
    assert not ctx.worktrees.head_is_valid()
    a, b = seed_story(ctx, "Registrar gasto"), seed_story(ctx, "Listar gastos")
    await Scheduler(ctx).run()
    for sid in (a, b):
        state = load_state(ctx, sid)
        assert state.blocked_reason == "delivery", state.blocked_reason
    events = [
        e for e in ctx.store.events_since(0, limit=10_000) if e["type"] == "repo.bootstrapped"
    ]
    assert len(events) == 1
    notes = [m for m in ctx.store.list_messages(f.slug) if m.title.startswith("Fiz o registro")]
    assert len(notes) == 1 and notes[0].kind == "info" and notes[0].executive_audit() == []
    assert "app/calc.py" in git("ls-files", cwd=repo)
    await ctx.aclose()


# ------------------------------------------------------------------------- escalation


async def test_escalation_ladder_tier2_to_tier1_and_constitution_lesson(factory: Factory):
    seen_models: list[str] = []
    # whatever this factory ships with: only tier 1 writes a passing test
    tier1_model = factory.config.models.candidates_for("worker", "tier1")[0].model

    def worker(model: str, messages: list[Message]) -> Any:
        seen_models.append(model)
        results = tool_results(messages)
        if not results:  # read before write: a retry overwrites the file the first attempt made
            return [ToolCall("r1", "read_file", {"path": "tests/test_new.py"})]
        if len(results) == 1:
            content = (
                "def test_new():\n    assert 1 == 1\n"
                if model == tier1_model
                else "def test_new():\n    assert 1 == 2\n"
            )
            args = {"path": "tests/test_new.py", "content": content}
            args["reason"] = "the assertion compared two different numbers"  # a retry: diagnosis
            return [ToolCall("w1", "write_file", args)]
        return [ToolCall("w2", "done", {"summary": f"escrevi teste com {model}"})]

    ctx = make_ctx(factory, with_worker(worker))
    sid = seed_story(ctx, "Novo teste")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    assert state.attempts_tier2 == 2 and state.current_tier == "tier1" and state.attempts_tier1 == 0
    # tier2 twice (initial + one retry), then tier1 fixes it
    tiers = [r["key"] for r in ctx.store.usage_by("tier", factory.slug)]
    assert set(tiers) == {"tier1", "tier2"}
    tier2_model = factory.config.models.candidates_for("worker", "tier2")[0].model
    assert seen_models.count(tier2_model) == 6 and seen_models.count(tier1_model) == 3
    types = [e["type"] for e in ctx.store.events_since(0, limit=10_000)]
    assert types.count("story.retry") == 1 and types.count("story.escalated") == 1
    # escalating re-plans once: two failures may mean the plan fences the Worker off the cause
    assert types.count("story.replanned") == 1
    assert [c["node"] for c in ctx.store.checkpoints(sid)].count("node_plan") == 2
    assert len(state.failure_history) == 2 and "assert 1 == 2" in state.failure_history[0]
    assert (
        "[pytest] FAIL" in state.failure_history[0] and "Traceback" not in state.failure_history[0]
    )
    # Kaizen: lesson added to the constitution and resolution stored in memory
    constitution = factory.paths.constitution.read_text()
    assert f"{sid}] Sempre rodar a suíte completa" in constitution
    assert "constitution.lesson" in types
    assert ctx.memory.search("suíte completa", kinds=("constitution",))
    assert any(row["kind"] == "resolution" for row in ctx.store.list_learnings())
    await ctx.aclose()


async def test_a_fix_merged_meanwhile_reaches_the_stories_going_back_to_work(factory: Factory):
    """`contas`: the skeleton could not import its own package, so every story's new tests
    failed for a reason none of them caused. The fix lands on main while they are in flight;
    a story going back to `dev` is rebased onto it and its baseline is measured again."""
    root = factory.root
    (root / "app" / "__init__.py").write_text("raise ImportError('pacote quebrado na base')\n")
    git("add", ".", cwd=root)
    git("commit", "-qm", "chore: base quebrada", cwd=root)
    rounds: list[int] = []

    def worker(model: str, messages: list[Message]) -> Any:
        if not tool_results(messages):
            rounds.append(1)
            test = "from app.calc import add\n\n\ndef test_soma():\n    assert add(2, 2) == 4\n"
            return [ToolCall("w1", "write_file", {"path": "tests/test_soma.py", "content": test})]
        if len(rounds) == 1:  # meanwhile, the fix merges on main (S-005 in `contas`)
            (root / "app" / "__init__.py").write_text("")
            git("add", ".", cwd=root)
            git("commit", "-qm", "fix: base volta a importar", cwd=root)
        return [ToolCall("w2", "done", {"summary": "teste de soma"})]

    ctx = make_ctx(factory, with_worker(worker))
    sid = seed_story(ctx, "Somar")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery", state.failure_history
    assert len(rounds) == 2 and state.attempts_tier2 == 1  # failed once, then passed
    synced = [e for e in ctx.store.events_since(0, limit=10_000) if e["type"] == "worktree.synced"]
    assert len(synced) == 1 and synced[0]["payload"]["conflicts"] == []
    assert state.extra["baseline"]["tests_ok"] is True  # measured again, on the fixed base
    assert "fix: base volta a importar" in git("log", "--oneline", cwd=Path(state.worktree))
    assert not [p for p in ctx.worktrees.dir.iterdir() if p.name.startswith("_base-")]
    await ctx.aclose()


async def test_the_founders_changes_can_widen_the_plan_they_did_not_foresee(factory: Factory):
    """`contas` S-005: the founder asked, on a delivery, for a file the plan never listed; the
    Worker was fenced off it and could only ask again. The Architect now amends the plan (paths
    and tasks) and the Worker does the new task alone, not the whole story again."""
    worker_tasks: list[str] = []
    judged: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        last = messages[-1].content
        if role == "inspector" and "## Diff" in last:
            judged.append(last)
        if role == "architect" and "## Founder's guidance" in last:
            return json.dumps(
                {"files": ["docs/"], "tasks": ["Escrever docs/NOTA.md"], "reason": "pedido"}
            )
        if role == "worker" and not tool_results(messages):
            task = next(m.content for m in messages if m.role == "user")
            worker_tasks.append(task)
            if "docs/NOTA.md" in task:
                return [ToolCall("w1", "write_file", {"path": "docs/NOTA.md", "content": "ok\n"})]
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Registrar")
    await Scheduler(ctx).run()
    delivery = ctx.store.get_message(load_state(ctx, sid).blocked_message_id)
    assert delivery.kind == "delivery" and len(worker_tasks) == 1
    await Scheduler(ctx).aanswer(
        delivery.id, FounderAnswer(option_key="changes", text="quero também uma nota em docs")
    )
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery", state.failure_history
    assert "docs/" in state.allowed_paths and state.tasks_total == 2 and state.tasks_done == [1, 2]
    assert len(worker_tasks) == 2 and "docs/NOTA.md" in worker_tasks[1]  # only the new task
    assert (Path(state.worktree) / "docs" / "NOTA.md").is_file()
    tasks_md = (factory.paths.specs / sid / "tasks.md").read_text()
    assert "[x] T2: Escrever docs/NOTA.md" in tasks_md
    assert any(e["type"] == "plan.amended" for e in ctx.store.events_since(0, limit=10_000))
    # the Inspector judges scope against the amended fence, not a guess from the spec
    assert "## Paths the plan allows" in judged[-1] and "- docs/" in judged[-1]
    await ctx.aclose()


async def test_a_conflict_with_the_base_is_resolved_by_the_worker(factory: Factory):
    """`contas` S-001: every story had worked around the broken base its own way, so the fix
    merged on main conflicted with each of them (pyproject, the test, uv.lock) and the rebase
    was simply aborted, forever. Now the base is merged in, lockfiles take the base's copy and
    the Worker resolves the rest; the Deployer commits only when no marker is left."""
    root = factory.root
    rounds: list[str] = []

    def worker(model: str, messages: list[Message]) -> Any:
        task = next(m.content for m in messages if m.role == "user")
        if "You resolve git merge conflicts" in messages[0].content:
            rounds.append("resolve")
            assert "<<<<<<<" in task and "## app/calc.py" in task  # the conflict is in the prompt
            merged = (
                "def add(a, b):\n    return b + a  # main\n\n\ndef sub(a, b):\n    return a - b\n"
            )
            return json.dumps({"files": {"app/calc.py": merged, "README.md": "nope"}})
        if tool_results(messages):
            return [ToolCall("d", "done", {"summary": "ok"})]
        rounds.append("work")
        if len(rounds) == 1:
            story = (
                "def add(a, b):\n    return a + b  # story\n\n\ndef sub(a, b):\n    return a - b\n"
            )
            (root / "app" / "calc.py").write_text("def add(a, b):\n    return b + a  # main\n")
            (root / "uv.lock").write_text("main\n")
            git("add", ".", cwd=root)
            git("commit", "-qm", "fix: main mexe na mesma linha", cwd=root)
            return [
                ToolCall("r1", "read_file", {"path": "app/calc.py"}),  # read before write
                ToolCall("w1", "write_file", {"path": "app/calc.py", "content": story}),
                ToolCall("w2", "write_file", {"path": "uv.lock", "content": "story\n"}),
                ToolCall(
                    "w3",
                    "write_file",
                    {
                        "path": "tests/test_sub.py",
                        "content": "from app.calc import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 3\n",
                    },
                ),
            ]
        fixed = "from app.calc import sub\n\n\ndef test_sub():\n    assert sub(3, 1) == 2\n"
        why = "the test expected 3 - 1 to be 3"  # a fix pass states its diagnosis
        return [
            ToolCall("r4", "read_file", {"path": "tests/test_sub.py"}),
            ToolCall(
                "w4", "write_file", {"path": "tests/test_sub.py", "content": fixed, "reason": why}
            ),
        ]

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "architect" and "## Founder's guidance" not in messages[-1].content:
            return json.dumps(
                {
                    "approach": "sub",
                    "files": ["app/", "tests/", "uv.lock"],
                    "contracts": "",
                    "risks": [],
                    "tasks": ["Criar sub"],
                    "adr_proposal": "",
                }
            )
        if role_of(messages) == "worker":
            return worker(model, messages)
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Subtrair")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery", state.failure_history
    assert rounds == ["work", "resolve", "work"]
    events = ctx.store.events_since(0, limit=10_000)
    synced = [e["payload"] for e in events if e["type"] == "worktree.synced"]
    assert synced == [{"conflicts": ["app/calc.py"]}]  # uv.lock settled on the base's copy
    assert any(e["type"] == "worktree.merged_base" for e in events)
    wt = Path(state.worktree)
    calc = (wt / "app" / "calc.py").read_text()
    assert "# main" in calc and "def sub" in calc and "<<<<<<<" not in calc
    assert (wt / "uv.lock").read_text() == "main\n"
    assert (wt / "README.md").read_text() == "# demo\n"  # outside the conflict: never written
    await ctx.aclose()


async def test_a_conflict_found_at_delivery_is_reintegrated_before_asking(factory: Factory):
    """`contas` S-002: main moved (another story merged) while it was in test, so the
    delivery's rebase conflicted and the founder was asked. Merging the base and resolving is
    now automatic, once; the founder hears only if that fails."""
    root = factory.root
    moved: list[bool] = []
    story = "def add(a, b):\n    return a + b  # story\n"
    both = "def add(a, b):\n    return a + b  # story + main\n"

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role == "inspector" and "## Diff" in messages[-1].content and not moved:
            moved.append(True)  # another delivery merges on main meanwhile
            (root / "app" / "calc.py").write_text("def add(a, b):\n    return a + b  # main\n")
            git("add", ".", cwd=root)
            git("commit", "-qm", "feat: outra entrega", cwd=root)
        if role == "worker":
            if "You resolve git merge conflicts" in messages[0].content:
                return json.dumps({"files": {"app/calc.py": both}})
            if not tool_results(messages):
                return [
                    ToolCall("r", "read_file", {"path": "app/calc.py"}),
                    ToolCall("w", "write_file", {"path": "app/calc.py", "content": story}),
                ]
            return [ToolCall("d", "done", {"summary": "ok"})]
        if role == "architect" and "## Founder's guidance" not in messages[-1].content:
            return json.dumps(
                {
                    "approach": "x",
                    "files": ["app/"],
                    "contracts": "",
                    "risks": [],
                    "tasks": ["Marcar add"],
                    "adr_proposal": "",
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Registrar")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery", state.failure_history
    types = [e["type"] for e in ctx.store.events_since(0, limit=10_000)]
    assert types.count("story.reintegrating") == 1 and "worktree.merged_base" in types
    assert not [m for m in ctx.store.list_messages(factory.slug) if "conflita" in m.title]
    assert (Path(state.worktree) / "app" / "calc.py").read_text() == both
    await ctx.aclose()


async def test_persistent_failure_blocks_only_that_story(factory: Factory):
    def worker(model: str, messages: list[Message]) -> Any:
        if "história boa" in messages[0].content.lower():
            return (
                [ToolCall("w2", "done", {"summary": "nada a fazer"})]
                if tool_results(messages)
                else [
                    ToolCall(
                        "w1",
                        "write_file",
                        {
                            "path": "tests/test_ok.py",
                            "content": "def test_ok():\n    assert True\n",
                        },
                    )
                ]
            )
        if not tool_results(messages):
            return [
                ToolCall(
                    "w1",
                    "write_file",
                    {
                        "path": "tests/test_bad.py",
                        "content": "def test_bad():\n    assert False, 'quebrado de propósito'\n",
                    },
                )
            ]
        return [ToolCall("w2", "done", {"summary": "tentei"})]

    ctx = make_ctx(factory, with_worker(worker))
    bad = seed_story(ctx, "História ruim")
    good = seed_story(ctx, "História boa")
    await Scheduler(ctx).run()
    bad_state, good_state = load_state(ctx, bad), load_state(ctx, good)
    assert good_state.stage == Stage.AWAITING_FOUNDER and good_state.blocked_reason == "delivery"
    assert (
        bad_state.stage == Stage.AWAITING_FOUNDER
        and bad_state.blocked_reason == "persistent_failure"
    )
    assert bad_state.attempts_tier2 == 2 and bad_state.attempts_tier1 == 1
    msg = ctx.store.get_message(bad_state.blocked_message_id)
    assert msg.kind == "blocked" and msg.requires_action and msg.executive_audit() == []
    assert "quebrado de propósito" not in msg.context and msg.technical_ref.endswith(f"{bad}.log")
    assert (factory.paths.logs / f"{bad}.log").read_text().count("quebrado de propósito") >= 1
    # founder: retry with guidance -> attempts reset, resumes at DEV
    state = await Scheduler(ctx).aanswer(
        msg.id, FounderAnswer(option_key="retry", text="pode remover esse teste")
    )
    assert state.stage == Stage.DEV and state.attempts_tier2 == 0 and state.current_tier == "tier2"
    # founder: skip -> backlog with low priority; drop -> cancelled and worktree removed
    ctx.store.update_story(
        bad,
        stage="AWAITING_FOUNDER",
        state={
            **state.model_dump(mode="json"),
            "stage": "AWAITING_FOUNDER",
            "blocked_reason": "persistent_failure",
        },
    )
    ctx.store.put_message(msg.model_copy(update={"status": MessageStatus.PENDING, "answer": None}))
    state = await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="skip"))
    assert state.stage == Stage.BACKLOG and ctx.store.get_story(bad)["priority"] == 900
    ctx.store.update_story(
        bad,
        stage="AWAITING_FOUNDER",
        state={
            **state.model_dump(mode="json"),
            "stage": "AWAITING_FOUNDER",
            "blocked_reason": "persistent_failure",
        },
    )
    ctx.store.put_message(msg.model_copy(update={"status": MessageStatus.PENDING, "answer": None}))
    state = await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="drop"))
    assert state.stage == Stage.CANCELLED and ctx.worktrees.get(bad) is None
    await ctx.aclose()


# ------------------------------------------------------------------- questions/kaizen


async def test_worker_question_pauses_and_resumes_with_guidance(factory: Factory):
    def worker(model: str, messages: list[Message]) -> Any:
        notes = "Orientações do Founder" in messages[1].content
        if not notes:
            return [
                ToolCall(
                    "q",
                    "blocked",
                    {
                        "reason": "Devo usar e-mail ou SMS para o código?",
                        "options": ["E-mail", "SMS"],
                    },
                )
            ]
        if not tool_results(messages):
            return [
                ToolCall(
                    "l",
                    "note_learning",
                    {
                        "title": "Função de envio duplicada em dois módulos",
                        "kind": "tech_debt",
                        "detail": "consolidar",
                    },
                ),
                ToolCall(
                    "w",
                    "write_file",
                    {
                        "path": "tests/test_reset.py",
                        "content": "def test_reset():\n    assert True\n",
                    },
                ),
            ]
        return [
            ToolCall(
                "d",
                "done",
                {"summary": "usei " + ("SMS" if "SMS" in messages[1].content else "e-mail")},
            )
        ]

    ctx = make_ctx(factory, with_worker(worker))
    sid = seed_story(ctx, "Recuperar senha")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert (
        state.stage == Stage.AWAITING_FOUNDER
        and state.blocked_reason == "question"
        and state.resume_stage == Stage.DEV
    )
    msg = ctx.store.get_message(state.blocked_message_id)
    assert (
        msg.executive_audit() == []
        and [o.label for o in msg.options][:2] == ["E-mail", "SMS"]
        or msg.options
    )
    state = await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key=msg.options[1].key))
    assert state.stage == Stage.DEV and state.founder_notes
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    # Kaizen captured the discovery: learnings.md, a backlog card, a store row and the memory index
    learnings = factory.paths.learnings.read_text()
    assert "Função de envio duplicada" in learnings and "Débito técnico" in learnings
    cards = [s for s in ctx.store.list_stories(factory.slug) if s["origin"] == "kaizen"]
    assert (
        len(cards) == 1
        and cards[0]["stage"] == "BACKLOG"
        and cards[0]["priority"] == 500
        and cards[0]["title"].startswith("[Débito técnico]")
    )
    # kaizen cards wait for the founder; once promoted they run like any story
    assert Scheduler(ctx).runnable() == []
    Scheduler(ctx).promote(cards[0]["id"])
    await Scheduler(ctx).run()
    assert load_state(ctx, cards[0]["id"]).stage == Stage.AWAITING_FOUNDER
    assert ctx.store.list_learnings()[0]["created_story_id"] == cards[0]["id"]
    assert ctx.memory.search("envio duplicado", kinds=("learning",))
    await ctx.aclose()


async def test_product_decision_blocks_at_spec(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "product" and "Orientações do Founder" not in messages[1].content:
            return json.dumps(
                {
                    "needs_decision": True,
                    "question": "Cobrar por assento ou por uso?",
                    "context": "Os dois modelos mudam o desenho da cobrança.",
                    "options": ["Por assento", "Por uso"],
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Cobrança")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert (
        state.stage == Stage.AWAITING_FOUNDER
        and state.blocked_reason == "question"
        and state.resume_stage == Stage.SPEC
    )
    msg = ctx.store.get_message(state.blocked_message_id)
    assert "assento" in msg.title.lower() or "assento" in msg.context.lower()
    state = await Scheduler(ctx).aanswer(msg.id, FounderAnswer(text="por uso, com franquia"))
    assert state.stage == Stage.SPEC and "por uso, com franquia" in state.founder_notes[0]
    await Scheduler(ctx).run()
    assert (
        load_state(ctx, sid).stage == Stage.AWAITING_FOUNDER
        and load_state(ctx, sid).blocked_reason == "delivery"
    )
    await ctx.aclose()


# --------------------------------------------------------------------- robustness


async def test_node_crash_isolates_story_and_scheduler_survives(factory: Factory):
    calls = {"n": 0}

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "architect":
            calls["n"] += 1
            raise LLMError("provedor fora do ar")
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    ctx.router.max_retries = 0
    sid = seed_story(ctx, "Qualquer coisa")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert (
        state.stage == Stage.AWAITING_FOUNDER
        and state.blocked_reason == "persistent_failure"
        and state.resume_stage == Stage.PLAN
    )
    msg = ctx.store.get_message(state.blocked_message_id)
    assert msg.executive_audit() == [] and "LLMError" not in msg.context
    assert any(e["type"] == "story.error" for e in ctx.store.events_since(0))
    await ctx.aclose()


async def test_ops_loompa_retries_transient_failure_and_tells_founder(factory: Factory):
    calls = {"n": 0}

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "architect":
            calls["n"] += 1
            if calls["n"] <= 2:
                raise LLMError("gemini/x: cota/limite (429)", retryable=True)
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    ctx.router.max_retries = 0
    tier = ctx.config.models.tier_for("architect")  # single-candidate tier, like a free-tier setup
    ctx.config.models.tiers[tier] = ctx.config.models.tiers[tier][:1]
    sid = seed_story(ctx, "Recupera sozinho")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    # the story went past PLAN by itself and reached the delivery gate
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    assert calls["n"] == 3 and "ops_incident" not in state.extra
    types = [e["type"] for e in ctx.store.events_since(0, limit=10_000)]
    assert types.count("story.retry") == 2 and "story.recovered" in types
    notes = [m for m in ctx.store.list_messages(ctx.slug) if m.sender == "Ops Loompa"]
    assert len(notes) == 1 and notes[0].kind == "info" and not notes[0].requires_action
    assert notes[0].executive_audit() == [] and "limite de uso" in notes[0].context
    await ctx.aclose()


async def test_ops_loompa_escalates_in_plain_language_after_max_recoveries(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "architect":
            raise LLMError("gemini/x: em cooldown", retryable=True)
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    ctx.router.max_retries = 0
    sid = seed_story(ctx, "Não recupera")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "persistent_failure"
    types = [e["type"] for e in ctx.store.events_since(0, limit=10_000)]
    assert types.count("story.retry") == factory.config.schedule.ops_max_recoveries
    msg = ctx.store.get_message(state.blocked_message_id)
    assert msg.executive_audit() == [] and "Ops Loompa tentou" in msg.context
    assert "cooldown" not in msg.context and "LLMError" not in msg.context
    await ctx.aclose()


async def test_the_same_block_twice_stops_recommending_try_again(factory: Factory):
    """`contas`: three "tentar de novo" in a row, three identical blocks, and the founder's
    written guidance thrown away each time. Now the guidance reaches the agents, and a block
    that comes back unchanged says so and recommends a way out instead of a fourth retry."""
    seen: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "architect":
            seen.append("\n".join(m.content for m in messages))
            raise LLMError("resposta cortada: mock/x: 32768 tokens (resposta cortada no limite)")
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Listar gastos")
    await Scheduler(ctx).run()
    first = ctx.store.get_message(load_state(ctx, sid).blocked_message_id)
    assert "mesmo problema" not in first.title
    await Scheduler(ctx).aanswer(
        first.id, FounderAnswer(option_key="retry", text="faça um plano mais curto")
    )
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "persistent_failure"
    assert "faça um plano mais curto" in seen[-1]  # the guidance went with the retry
    again = ctx.store.get_message(state.blocked_message_id)
    assert again.title.startswith("O mesmo problema voltou") and "2ª vez" in again.context
    assert [o.key for o in again.options if o.recommended] == ["skip"]
    assert again.executive_audit() == []
    state = await Scheduler(ctx).aanswer(again.id, FounderAnswer(option_key="skip"))
    assert state.stage == Stage.BACKLOG
    await ctx.aclose()


async def test_a_rewritten_block_keeps_the_option_keys_the_engine_acts_on(factory: Factory):
    """`contas`: the Master's rewrite offered "Deixar para depois" under the key `later`,
    which the engine does not know and would have treated as a retry."""

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        role = role_of(messages)
        if role == "architect":
            raise LLMError("resposta cortada: mock/x (resposta cortada no limite)")
        if role == "master" and "Problem:" in messages[-1].content:
            return json.dumps(
                {
                    "title": "Uma etapa parou",
                    "context": "Estávamos planejando a entrega e a etapa parou.",
                    "impact": "As outras entregas seguem.",
                    "options": [{"key": "later", "label": "Deixar para depois"}],
                }
            )
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(factory, script)
    sid = seed_story(ctx, "Listar gastos")
    await Scheduler(ctx).run()
    msg = ctx.store.get_message(load_state(ctx, sid).blocked_message_id)
    assert msg.title == "Uma etapa parou"  # the rewrite is used for the words…
    assert [o.key for o in msg.options] == ["retry", "skip", "drop"]  # …not for the keys
    state = await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="skip"))
    assert state.stage == Stage.BACKLOG
    await ctx.aclose()


async def test_budget_exhaustion_pauses_dispatch(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    ctx.config.budget.cap_usd = 0.000001
    ctx.store.record_usage(
        factory=factory.slug,
        agent="x",
        role="worker",
        provider="p",
        model="m",
        tier="tier2",
        input_tokens=1,
        output_tokens=1,
        cost_usd=0.01,
    )
    sid = seed_story(ctx, "Nada")
    await Scheduler(ctx).run(until_idle=True, max_cycles=1)
    assert ctx.store.checkpoints(sid)[-1]["node"] == "admit"  # admitted, but nothing dispatched
    assert load_state(ctx, sid).phase == "intake"
    events = [e["type"] for e in ctx.store.events_since(0)]
    assert "scheduler.paused" in events
    alert = [m for m in ctx.store.list_messages(factory.slug) if m.kind == "finance"]
    assert len(alert) == 1 and alert[0].executive_audit() == []
    state = await Scheduler(ctx).aanswer(alert[0].id, FounderAnswer(option_key="raise_10"))
    assert state is None and Factory.open(factory.root).config.budget.cap_usd > 5
    await ctx.aclose()


async def test_resume_from_checkpoint_after_interruption(factory: Factory):
    from loompa.engine.scheduler import runtime_for

    ctx = make_ctx(factory, dry_run=True)
    sid = seed_story(ctx, "Retomável")
    state = await runtime_for(ctx).run_until(load_state(ctx, sid), stop_after=["spec_review"])
    assert state.stage == Stage.PLAN and ctx.store.get_story(sid)["stage"] == "PLAN"
    assert state.route == ["intake", "spec", "spec_review", "plan", "dev", "test", "review"]
    await ctx.aclose()
    # "restart": a fresh context continues from LangGraph's SQLite checkpoint
    ctx2 = make_ctx(factory, dry_run=True)
    await Scheduler(ctx2).run()
    state = load_state(ctx2, sid)
    assert state.stage == Stage.AWAITING_FOUNDER
    assert [c["node"] for c in ctx2.store.checkpoints(sid)][:5] == [
        "admit",
        "node_intake",
        "node_spec",
        "node_spec_review",
        "node_plan",
    ]
    hist = await runtime_for(ctx2).history(sid)
    assert hist and hist[0]["stage"] == "AWAITING_FOUNDER"
    await ctx2.aclose()


def test_end_of_day_report_is_executive(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    ProductOwnerAgent(ctx).add_item("A")  # still waiting in the backlog
    msg = MasterAgent(ctx).end_of_day_report()
    assert (
        msg.kind == "info"
        and "Resumo do dia" in msg.title
        and "No backlog: 1" in msg.context
        and msg.executive_audit() == []
    )
    assert Store(factory.paths.state_db).list_messages(factory.slug)[0].id == msg.id
    ctx.close()


async def test_red_baseline_is_not_blamed_on_story(factory: Factory):
    # main already has a failing test: the story must still deliver, and the debt becomes a learning
    (factory.root / "tests" / "test_legacy.py").write_text("def test_legacy():\n    assert False\n")
    git("add", ".", cwd=factory.root)
    git("commit", "-qm", "test: legacy red", cwd=factory.root)
    ctx = make_ctx(factory, dry_run=True)
    sid = seed_story(ctx, "Entrega com base vermelha")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    assert state.extra["baseline"]["failing"] == ["test_legacy"]
    assert any("já falha" in row["title"] for row in ctx.store.list_learnings())
    assert any(e["type"] == "inspector.baseline_red" for e in ctx.store.events_since(0))
    ctx.close()


# ------------------------------------------------------------------- runner crashes


async def test_scheduler_retries_transient_runner_crash_without_double_dispatch(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
):
    """A crash outside the nodes (e.g. a locked checkpoint db) is retried after the Ops
    backoff; the story is dispatched once at a time and reported once."""
    import sqlite3

    from loompa.engine.langgraph_engine import GraphRuntime

    ctx = make_ctx(factory, dry_run=True)
    sid = seed_story(ctx, "Crash transitório")
    original = GraphRuntime.run_story
    calls: list[str] = []

    async def flaky(self: GraphRuntime, state: Any) -> Any:
        calls.append(state.story_id)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return await original(self, state)

    monkeypatch.setattr(GraphRuntime, "run_story", flaky)
    done = await Scheduler(ctx).run()
    assert done == [sid] and calls == [sid, sid]
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "delivery"
    types = [e["type"] for e in ctx.store.events_since(0)]
    assert "story.retry" in types and types.count("scheduler.dispatch") == 2
    await ctx.aclose()


async def test_scheduler_escalates_persistent_runner_crash_to_inbox(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
):
    from loompa.engine.langgraph_engine import GraphRuntime

    ctx = make_ctx(factory, dry_run=True)
    sid = seed_story(ctx, "Crash permanente")

    async def broken(self: GraphRuntime, state: Any) -> Any:
        raise KeyError("foo")  # not transient: escalate on the first crash

    monkeypatch.setattr(GraphRuntime, "run_story", broken)
    done = await Scheduler(ctx).run()
    assert done == []
    state = load_state(ctx, sid)
    assert state.stage == Stage.AWAITING_FOUNDER
    assert state.blocked_reason == "persistent_failure" and state.resume_stage == Stage.BACKLOG
    msg = ctx.store.get_message(state.blocked_message_id)
    assert msg is not None and msg.executive_audit() == []
    assert "KeyError" not in msg.context and "foo" not in msg.context
    await ctx.aclose()


async def test_one_engine_per_factory(factory: Factory):
    """While validating `contas` a `loompa run` stayed alive next to a new one: both dispatched
    the same stories into the same worktrees. The second engine is now refused."""
    import os

    from loompa.engine.lock import EngineBusy, EngineLock

    lock = EngineLock(factory.paths.loompa / "engine.lock")
    lock.acquire()
    assert EngineLock(lock.path).holder() == str(os.getpid())
    ctx = make_ctx(factory, dry_run=True)
    with pytest.raises(EngineBusy, match="outra esteira já está rodando"):
        await Scheduler(ctx).run()
    lock.release()
    assert EngineLock(lock.path).holder() is None
    await Scheduler(ctx).run()  # free again: runs, and releases when done
    assert EngineLock(lock.path).holder() is None
    await ctx.aclose()
