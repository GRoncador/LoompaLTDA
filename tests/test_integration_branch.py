"""ADR-0027: several pending deliveries that are not a chain get an integration branch, and a
story keeps up with the deliveries it was written on top of."""

from __future__ import annotations

from conftest import git
from loompa.agents import DeployerAgent
from loompa.dependencies import (
    BUILT_ON_KEY,
    INTEGRATION_KEY,
    WAIT_KEY,
    Gate,
    base_branch_for,
    branch_carries,
    gone_under,
    merge_gate,
    parents,
    plan_gate,
    sync_refs,
)
from loompa.engine import Stage, StoryState
from loompa.factory import Factory
from test_engine import factory, make_ctx  # noqa: F401

ROUTE = ["spec", "spec_review", "plan", "plan_review", "dev", "test", "review"]


def _delivered(sid: str, branch: str, **state) -> dict:
    """A story built, green, waiting only for the founder to accept it."""
    return {
        "id": sid,
        "stage": Stage.AWAITING_FOUNDER,
        "depends_on": [],
        "branch": branch,
        "state": {
            "blocked_reason": "delivery",
            "qa_verdict": "PASS",
            "route": ROUTE,
            "resume_phase": "review",
            **state,
        },
    }


def _rows_two_separate_lines() -> dict:
    return {
        "S-001": _delivered("S-001", "b1"),
        "S-002": _delivered("S-002", "b2"),
        "S-003": {
            "id": "S-003",
            "stage": Stage.PLAN,
            "depends_on": ["S-001", "S-002"],
            "branch": "",
            "state": {"route": ROUTE, "phase": "plan"},
        },
    }


# --------------------------------------------------------------------------- the readings


def test_two_separate_deliveries_ask_for_an_integration_instead_of_the_founder():
    rows = _rows_two_separate_lines()
    gate = plan_gate(rows, "S-003")
    assert gate.gate == Gate.OPEN  # the founder's acceptance is no longer what it waits for
    assert gate.integrate == ["S-001", "S-002"] and gate.unmerged == ["S-001", "S-002"]
    assert not gate.pending and not gate.founder
    # with no integration recorded yet there is nothing to start from
    assert base_branch_for(rows, "S-003") == ""


def test_an_integration_branch_is_the_base_and_both_deliveries_are_parents():
    rows = _rows_two_separate_lines()
    rows["S-003"]["state"]["extra"] = {
        INTEGRATION_KEY: {"branch": "integration/s-003", "deps": ["S-001", "S-002"]}
    }
    assert plan_gate(rows, "S-003").integrate == []  # already built
    assert base_branch_for(rows, "S-003") == "integration/s-003"
    assert parents(rows["S-003"]) == ["S-001", "S-002"]
    # it merges only after both are in the base, and a fourth story on top of it gets all three
    assert merge_gate(rows, "S-003") == ["S-001", "S-002"]
    assert branch_carries(rows, "S-003") == {"S-001", "S-002", "S-003"}
    # a delivery sent back to the backlog under it still stops the delivery, whichever it is
    rows["S-002"]["stage"] = Stage.BACKLOG
    assert gone_under(rows, "S-003") == "S-002"


def test_a_story_keeps_up_with_the_delivery_it_was_written_on_top_of():
    """The branch a worktree started from is a snapshot: a fix the founder asked for in that
    delivery only reaches this story through the sync refs."""
    rows = _rows_two_separate_lines()
    rows["S-003"]["state"]["extra"] = {BUILT_ON_KEY: "S-001"}
    assert sync_refs(rows, "S-003", "main") == ["b1", "main"]
    # once the delivery is accepted and merged, its code comes with the base
    rows["S-001"]["stage"] = Stage.DONE
    assert sync_refs(rows, "S-003", "main") == ["main"]
    # with an integration, every parent's branch is followed
    rows["S-001"]["stage"] = Stage.AWAITING_FOUNDER
    rows["S-003"]["state"]["extra"] = {
        INTEGRATION_KEY: {"branch": "integration/s-003", "deps": ["S-001", "S-002"]}
    }
    assert sync_refs(rows, "S-003", "main") == ["b1", "b2", "main"]
    # a story with no parent syncs with the base alone
    assert sync_refs(rows, "S-001", "main") == ["main"]


# --------------------------------------------------------------------------- the git side


async def test_the_deployer_builds_one_branch_from_two_deliveries(factory: Factory):
    ctx = make_ctx(factory)
    root = factory.root
    for name, file in (("b1", "um.py"), ("b2", "dois.py")):
        git("checkout", "-q", "-b", name, "main", cwd=root)
        (root / file).write_text("x = 1\n")
        git("add", ".", cwd=root)
        git("commit", "-qm", f"feat: {file}", cwd=root)
        git("checkout", "-q", "main", cwd=root)
    state = StoryState(story_id="S-003", title="Depende das duas")
    assert DeployerAgent(ctx).integrate_dependencies(state, {"S-001": "b1", "S-002": "b2"}) == []
    name = state.extra[INTEGRATION_KEY]["branch"]
    assert state.extra[INTEGRATION_KEY]["deps"] == ["S-001", "S-002"]
    files = git("ls-tree", "--name-only", name, cwd=root).split()
    assert "um.py" in files and "dois.py" in files  # the story starts with both
    await ctx.aclose()


async def test_a_conflict_between_two_deliveries_leaves_nothing_behind(factory: Factory):
    """Two deliveries the founder has not accepted that touch the same lines: the factory does not
    pick a side inside a third story's branch."""
    ctx = make_ctx(factory)
    root = factory.root
    for name, text in (("b1", "um\n"), ("b2", "dois\n")):
        git("checkout", "-q", "-b", name, "main", cwd=root)
        (root / "mesmo.py").write_text(text)
        git("add", ".", cwd=root)
        git("commit", "-qm", f"feat: {name}", cwd=root)
        git("checkout", "-q", "main", cwd=root)
    state = StoryState(story_id="S-003", title="Depende das duas")
    conflicts = DeployerAgent(ctx).integrate_dependencies(state, {"S-001": "b1", "S-002": "b2"})
    assert conflicts == ["mesmo.py"]
    assert INTEGRATION_KEY not in state.extra  # nothing recorded
    branches = git("branch", "--format=%(refname:short)", cwd=root).split()
    assert not [b for b in branches if "integration" in b]  # and no branch left over
    await ctx.aclose()


async def test_the_sync_brings_a_parents_new_commits_not_only_the_base(factory: Factory):
    """The gap this closes: `WorktreeManager.get` answers with the default branch, so a story
    built on an unaccepted delivery used to sync only with the base and never saw that delivery's
    later fixes."""
    ctx = make_ctx(factory)
    root = factory.root
    git("checkout", "-q", "-b", "b1", "main", cwd=root)
    (root / "dep.py").write_text("v = 1\n")
    git("add", ".", cwd=root)
    git("commit", "-qm", "feat: dep", cwd=root)
    git("checkout", "-q", "main", cwd=root)

    wt = ctx.worktrees.create("S-002", base="b1")
    assert (wt.path / "dep.py").read_text() == "v = 1\n"
    # the founder asked for changes and the delivery was fixed on its own branch
    git("checkout", "-q", "b1", cwd=root)
    (root / "dep.py").write_text("v = 2  # corrigido\n")
    git("add", ".", cwd=root)
    git("commit", "-qm", "fix: dep", cwd=root)
    git("checkout", "-q", "main", cwd=root)

    # a later run reads the worktree back, and git cannot say what it was created from
    reread = ctx.worktrees.get("S-002")
    assert reread.base == "main"
    state = StoryState(story_id="S-002", title="Em cima da dep")
    assert DeployerAgent(ctx).sync_with_base(state, reread) is None  # the base alone: nothing new
    assert (wt.path / "dep.py").read_text() == "v = 1\n"  # the fix never arrived
    assert DeployerAgent(ctx).sync_with_base(state, reread, ["b1", "main"]) == []
    assert (wt.path / "dep.py").read_text() == "v = 2  # corrigido\n"
    await ctx.aclose()


async def test_an_existing_worktree_remembers_the_branch_it_started_from(factory: Factory):
    from loompa.engine.graph import _ensure_worktree

    ctx = make_ctx(factory)
    root = factory.root
    git("checkout", "-q", "-b", "b1", "main", cwd=root)
    (root / "dep.py").write_text("v = 1\n")
    git("add", ".", cwd=root)
    git("commit", "-qm", "feat: dep", cwd=root)
    git("checkout", "-q", "main", cwd=root)
    ctx.worktrees.create("S-002", base="b1")
    assert ctx.worktrees.get("S-002").base == "main"  # what git can tell on its own

    state = StoryState(story_id="S-002", title="Em cima da dep")
    state.extra[INTEGRATION_KEY] = {"branch": "b1", "deps": ["S-001"]}
    assert _ensure_worktree(ctx, state).base == "b1"
    await ctx.aclose()


# --------------------------------------------------------------------------- end to end


async def test_a_story_on_two_separate_deliveries_runs_without_waiting_for_the_founder(
    factory: Factory,
):
    """The case this was built for: C depends on A and B, neither depends on the other, both
    built and green. Before, C waited for an acceptance; now the Deployer merges the two and C is
    written on top of both."""
    from loompa.agents import MasterAgent, ProductOwnerAgent
    from loompa.dependencies import rows_by_id
    from loompa.engine import Scheduler, load_state

    ctx = make_ctx(factory, dry_run=True)
    po = ProductOwnerAgent(ctx)
    a, b, c = (po.add_item(t).story_id for t in ("Guardar centavos", "Exportar CSV", "Relatório"))
    po.set_dependencies(c, [a, b])
    MasterAgent(ctx).start_sprint([a, b, c])
    for _ in range(3):  # `run()` returns when nothing is running; C parks while A and B build
        await Scheduler(ctx).run()

    for sid in (a, b):
        assert load_state(ctx, sid).blocked_reason == "delivery"
    sc = load_state(ctx, c)
    assert sc.stage != Stage.AWAITING_FOUNDER  # it was not held, and nobody was asked
    assert sc.plan_ready and WAIT_KEY in sc.extra  # parked before `review`, as the chain does
    integration = sc.extra[INTEGRATION_KEY]
    assert integration["deps"] == sorted([a, b])
    # C's branch descends from both deliveries: the integration's merges are in its history
    # (the dry-run Worker writes the same file in every story, so counting files proves nothing)
    rows = rows_by_id(ctx.store, factory.slug)
    log = git("log", "--format=%s", cwd=ctx.worktrees.get(c).path)
    assert f"merge {rows[a]['branch']} into" in log
    assert f"merge {rows[b]['branch']} into" in log
    assert merge_gate(rows, c) == sorted([a, b])
    assert not [m for m in ctx.store.list_messages(factory.slug) if m.story_id == c]
    await ctx.aclose()


async def test_two_deliveries_that_conflict_stop_the_story_and_ask(factory: Factory):
    """A conflict between two deliveries the founder has not accepted is not the factory's to
    settle: resolving it inside a third story's branch would pick a side on code that may still
    be sent back."""
    from loompa.comms import FounderAnswer
    from loompa.engine import Scheduler, load_state

    ctx = make_ctx(factory, dry_run=True)
    calls = {"n": 0}
    real = DeployerAgent.integrate_dependencies

    def conflicting(self, state, deps):  # noqa: ANN001
        calls["n"] += 1
        return ["loompa_dryrun/mesmo.md"]

    DeployerAgent.integrate_dependencies = conflicting
    try:
        from loompa.agents import MasterAgent, ProductOwnerAgent

        po = ProductOwnerAgent(ctx)
        a, b, c = (po.add_item(t).story_id for t in ("Centavos", "CSV", "Relatório"))
        po.set_dependencies(c, [a, b])
        MasterAgent(ctx).start_sprint([a, b, c])
        for _ in range(3):
            await Scheduler(ctx).run()
        sc = load_state(ctx, c)
        assert sc.stage == Stage.AWAITING_FOUNDER and sc.blocked_reason == "integration"
        msg = next(m for m in ctx.store.list_messages(factory.slug, "pending") if m.story_id == c)
        assert msg.executive_audit() == []  # no paths, no branch names
        assert [o.key for o in msg.options] == ["wait", "skip", "drop"]
        assert calls["n"] == 1

        # "wait": the story goes back to the gate, which parks it until an acceptance
        st = await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="wait"))
        assert st.phase == "plan" and st.blocked_reason is None
        assert INTEGRATION_KEY not in st.extra
    finally:
        DeployerAgent.integrate_dependencies = real
    await ctx.aclose()
