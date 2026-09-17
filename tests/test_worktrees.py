from pathlib import Path

import pytest

from loompa.worktrees import GitError, WorktreeManager


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
    assert wm.rebase_on_base(wt)
    sha = wm.merge_into_base(wt)
    assert sha and (git_repo / "feature.py").exists()
    assert wm.remove("S-001", delete_branch=True)
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
    assert wm.rebase_on_base(wt) is False
    assert "docs: story" in wm.log(wt)[0]  # rebase aborted cleanly


def test_create_requires_commits(tmp_path: Path):
    repo = tmp_path / "empty"
    repo.mkdir()
    WorktreeManager(repo).git("init", "-q", "-b", "main")
    with pytest.raises(GitError, match="primeiro commit"):
        WorktreeManager(repo).create("S-1")
