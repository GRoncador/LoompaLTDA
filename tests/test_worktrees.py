from pathlib import Path

import pytest

from loompa.worktrees import GitAuthorityError, GitError, WorktreeManager
from test_engine import factory  # noqa: F401


def test_worktree_lifecycle(git_repo: Path):
    wm = WorktreeManager(git_repo)
    assert wm.default_branch() == "main"
    wt = wm.create("S-001", title="Login page")
    assert wt.path == git_repo / ".loompa" / "worktrees" / "S-001"
    assert wt.branch == "loompa/s-001-login-page" and wt.path.is_dir()
    assert wm.create("S-001", title="Login page").branch == wt.branch  # idempotent
    (wt.path / "feature.py").write_text("x = 1\n")
    assert wm.status(wt) == ["?? feature.py"]
    res = wm.commit_all(wt, "feat: add feature")
    assert res is not None and res.files == 1
    assert wm.commit_all(wt, "noop") is None
    assert wm.log(wt) and "feat: add feature" in wm.log(wt)[0]
    assert "feature.py" in wm.diff_stat(wt) and "+x = 1" in wm.diff(wt)
    assert not (git_repo / "feature.py").exists()  # root untouched
    assert [w.story_id for w in wm.list()] == ["S-001"]
    assert not wm.has_conflicts_with_base(wt)
    deployer = wm.as_role("deployer")
    assert deployer.rebase_on_base(wt)
    sha = deployer.merge_into_base(wt)
    assert sha and (git_repo / "feature.py").exists()
    assert deployer.remove("S-001", delete_branch=True)
    assert not wt.path.exists() and wm.list() == []
    assert wm.remove("S-001") is False


def test_conflict_detection(git_repo: Path):
    wm = WorktreeManager(git_repo)
    wt = wm.create("S-002")
    (wt.path / "README.md").write_text("# story version\n")
    wm.commit_all(wt, "docs: story")
    (git_repo / "README.md").write_text("# main version\n")
    wm.git("add", "-A")
    wm.git("-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "docs: main")
    assert wm.has_conflicts_with_base(wt)
    assert wm.as_role("deployer").rebase_on_base(wt) is False
    assert "docs: story" in wm.log(wt)[0]  # rebase aborted cleanly


def test_create_requires_commits(tmp_path: Path):
    repo = tmp_path / "empty"
    repo.mkdir()
    WorktreeManager(repo).git("init", "-q", "-b", "main")
    with pytest.raises(GitError, match="primeiro commit"):
        WorktreeManager(repo).create("S-1")


@pytest.mark.parametrize("role", [None, "worker", "product_owner", "master", "inspector", "kaizen"])
def test_only_the_deployer_may_change_shared_history(git_repo: Path, role: str | None):
    root = WorktreeManager(git_repo)
    wm = root.as_role(role) if role else root
    wt = root.create("S-003", title="Guarded")
    (wt.path / "guarded.py").write_text("x = 1\n")
    assert wm.commit_all(wt, "feat: inside the story branch") is not None  # ordinary work is fine
    head = root.git("rev-parse", "HEAD")

    with pytest.raises(GitAuthorityError, match="Deployer"):
        wm.merge_into_base(wt)
    with pytest.raises(GitAuthorityError):
        wm.rebase_on_base(wt)
    with pytest.raises(GitAuthorityError):
        wm.open_pull_request(wt, title="t", body="b")
    # the raw escape hatch is guarded too, however the flags are spelled
    for args in (
        ("merge", wt.branch),
        ("-c", "user.name=x", "merge", wt.branch),
        ("push", "origin", wt.branch),
        ("rebase", "main"),
        ("checkout", "main"),
        ("reset", "--hard", "HEAD~1"),
        ("branch", "-D", wt.branch),
        ("-C", str(git_repo), "push"),
    ):
        with pytest.raises(GitAuthorityError):
            wm.git(*args, cwd=wt.path)
    with pytest.raises(GitAuthorityError):
        wm.remove("S-003", delete_branch=True)

    assert root.git("rev-parse", "HEAD") == head  # nothing moved
    assert not (git_repo / "guarded.py").exists()
    assert wt.path.is_dir() and root.git("branch", "--list", wt.branch)

    # the Deployer can, and roles cannot borrow its view by name alone on another manager
    assert root.as_role("deployer").merge_into_base(wt)
    assert (git_repo / "guarded.py").exists()
    assert root.actor is None


def test_read_only_git_stays_open_to_everyone(git_repo: Path):
    wm = WorktreeManager(git_repo).as_role("worker")
    wt = wm.create("S-004")
    assert wm.status(wt) == [] and wm.default_branch() == "main"
    assert wm.git("log", "--oneline", "-1", cwd=wt.path)
    assert wm.git("branch", "--list")  # listing branches is not deleting them
    assert wm.remove("S-004")


async def test_agents_reach_git_through_their_role(factory):
    from loompa.agents import (
        DeployerAgent,
        InspectorAgent,
        KaizenAgent,
        MasterAgent,
        ProductOwnerAgent,
        WorkerAgent,
    )
    from test_engine import make_ctx

    ctx = make_ctx(factory)
    wt = ctx.worktrees.create("S-009", title="Por papel")
    (wt.path / "a.txt").write_text("a\n")
    for cls in (WorkerAgent, InspectorAgent, ProductOwnerAgent, MasterAgent, KaizenAgent):
        agent = cls(ctx)
        assert agent.git.actor == agent.role != "deployer"
        with pytest.raises(GitAuthorityError):
            agent.git.merge_into_base(wt)
    with pytest.raises(GitAuthorityError):
        ctx.worktrees.merge_into_base(wt)  # nor is the bare engine handle allowed to
    deployer = DeployerAgent(ctx)
    assert deployer.git.actor == "deployer"
    assert deployer.git.commit_all(wt, "feat: a") is not None
    assert deployer.git.merge_into_base(wt)
    ctx.close()
