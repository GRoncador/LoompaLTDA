"""OpenCode Worker backend (Fase 2 spike, ADR-0007): scripted fake `opencode` binary, no network."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from conftest import git
from loompa.agents.opencode_worker import OpenCodeWorker
from loompa.engine import EngineContext, Stage
from loompa.engine.state import StoryState
from loompa.factory import Factory, bootstrap_factory
from loompa.llm import MockProvider, ModelRouter
from loompa.speckit import story_dir

FAKE_OPENCODE = """#!/bin/sh
# Minimal stand-in for `opencode run --format json --agent <a> --model <m> "<prompt>"`.
echo "opencode $@" >> "$OPENCODE_LOG"
echo "changed" >> CHANGED.txt
echo '{"text": "DONE: fake task done"}'
"""

FAKE_OPENCODE_BLOCKED = """#!/bin/sh
echo '{"text": "BLOCKED: falta uma credencial"}'
"""

FAKE_OPENCODE_FAIL = """#!/bin/sh
echo "boom" 1>&2
exit 1
"""


def _install_fake_opencode(bin_dir: Path, script: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / "opencode"
    path.write_text(script)
    path.chmod(path.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def factory(git_repo: Path, hub) -> Factory:
    git("add", ".", cwd=git_repo)
    result = bootstrap_factory(git_repo, name="Demo", store=hub)
    f = result.factory
    f.config.quality.test_command = ""
    f.save()
    return Factory.open(git_repo)


def make_ctx(factory: Factory) -> EngineContext:
    provider = MockProvider("mock")
    router = ModelRouter(
        factory.config, providers=dict.fromkeys(factory.config.providers, provider)
    )
    return EngineContext.build(factory, router=router)


def seed_story_with_tasks(ctx: EngineContext, story_id: str = "S-001") -> StoryState:
    paths = story_dir(ctx.root, story_id)
    paths.root.mkdir(parents=True, exist_ok=True)
    paths.spec.write_text("# Spec\nFaz a coisa.\n")
    paths.plan.write_text("# Plan\nFaz simples.\n")
    paths.tasks.write_text("- [ ] T1: Implementar a coisa\n")
    ctx.store.upsert_story(
        {"id": story_id, "factory": ctx.slug, "title": "A coisa", "stage": Stage.DEV}
    )
    return StoryState(
        story_id=story_id, title="A coisa", allowed_paths=["**"], route=["dev"], phase="dev"
    )


async def test_opencode_worker_completes_task_and_commits(
    factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ctx = make_ctx(factory)
    bin_dir = tmp_path / "bin"
    _install_fake_opencode(bin_dir, FAKE_OPENCODE)
    log_file = tmp_path / "opencode.log"
    monkeypatch.setenv("OPENCODE_LOG", str(log_file))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    state = seed_story_with_tasks(ctx)
    wt = ctx.worktrees.create(state.story_id, title=state.title)
    state.worktree = str(wt.path)
    state.branch = wt.branch

    worker = OpenCodeWorker(ctx)
    result = await worker.run(state, wt)

    assert result.ok
    assert state.tasks_done == [1]
    assert len(state.commits) == 1
    assert (wt.path / "CHANGED.txt").is_file()
    assert (wt.path / ".opencode" / "agents" / "loompa-worker.md").is_file()
    assert log_file.read_text().strip().startswith("opencode run")
    # approximate cost was recorded even without real token counts
    story = ctx.store.get_story(state.story_id)
    assert story is not None and story["cost_usd"] >= 0.0
    await ctx.aclose()


async def test_opencode_worker_reports_blocked(
    factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ctx = make_ctx(factory)
    bin_dir = tmp_path / "bin"
    _install_fake_opencode(bin_dir, FAKE_OPENCODE_BLOCKED)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    state = seed_story_with_tasks(ctx)
    wt = ctx.worktrees.create(state.story_id, title=state.title)

    result = await OpenCodeWorker(ctx).run(state, wt)

    assert not result.ok
    assert result.blocked_reason and "credencial" in result.blocked_reason
    assert state.tasks_done == []
    await ctx.aclose()


async def test_opencode_worker_raises_on_nonzero_exit(
    factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    ctx = make_ctx(factory)
    bin_dir = tmp_path / "bin"
    _install_fake_opencode(bin_dir, FAKE_OPENCODE_FAIL)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    state = seed_story_with_tasks(ctx)
    wt = ctx.worktrees.create(state.story_id, title=state.title)

    with pytest.raises(RuntimeError, match="opencode terminou com erro"):
        await OpenCodeWorker(ctx).run(state, wt)
    await ctx.aclose()


async def test_node_dev_picks_opencode_backend_from_config(
    factory: Factory, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """`worker.backend: opencode` in config routes node_dev to OpenCodeWorker, not WorkerAgent
    (the ACI-based one never writes `.opencode/agents/`)."""
    from loompa.engine.graph import node_dev

    factory.config.worker.backend = "opencode"
    ctx = make_ctx(factory)
    bin_dir = tmp_path / "bin"
    _install_fake_opencode(bin_dir, FAKE_OPENCODE)
    monkeypatch.setenv("OPENCODE_LOG", str(tmp_path / "opencode.log"))
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    state = seed_story_with_tasks(ctx)
    state = await node_dev(ctx, state)

    assert state.tasks_done == [1]
    wt = ctx.worktrees.get(state.story_id)
    assert wt is not None and (wt.path / ".opencode" / "agents" / "loompa-worker.md").is_file()
    await ctx.aclose()


async def test_opencode_worker_missing_binary_raises(
    factory: Factory, monkeypatch: pytest.MonkeyPatch
):
    import loompa.agents.opencode_worker as mod

    ctx = make_ctx(factory)
    monkeypatch.setattr(mod.shutil, "which", lambda _name: None)

    state = seed_story_with_tasks(ctx)
    wt = ctx.worktrees.create(state.story_id, title=state.title)

    with pytest.raises(RuntimeError, match="não foi encontrado no PATH"):
        await OpenCodeWorker(ctx).run(state, wt)
    await ctx.aclose()


async def test_opencode_agent_cannot_change_shared_history(factory: Factory):
    """The generated agent file denies the git commands only the Deployer may run."""
    import yaml

    ctx = make_ctx(factory)
    state = seed_story_with_tasks(ctx)
    wt = ctx.worktrees.create(state.story_id, title=state.title)
    OpenCodeWorker(ctx)._write_agent_config(wt, state)
    text = (wt.path / ".opencode" / "agents" / "loompa-worker.md").read_text()
    front = yaml.safe_load(text.split("---\n")[1])
    bash = front["permission"]["bash"]
    keys = list(bash)
    assert keys[0] == "*" and bash["*"] == "allow"  # wildcard first: the denials that follow win
    for denied in ("git push*", "git * merge*", "git rebase*", "git checkout*", "gh *"):
        assert bash[denied] == "deny" and keys.index(denied) > 0
    assert front["permission"]["edit"]["*"] == "deny"
    await ctx.aclose()
