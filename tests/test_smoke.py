"""The browser smoke test for web products (tamagotchi S-015: 108 green tests, a blank screen)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from loompa.smoke import find_chrome, module_refs, smoke, start_command
from test_engine import factory  # noqa: F401

SERVE = f"""sh -c '"{sys.executable}" -m http.server $PORT --bind 127.0.0.1'"""
needs_chrome = pytest.mark.skipif(find_chrome() is None, reason="no Chrome or Chromium here")


def _site(root: Path, draw: str) -> Path:
    (root / "src").mkdir(parents=True)
    (root / "index.html").write_text(
        '<!doctype html><canvas id="tela" width="40" height="40"></canvas>'
        '<script type="module" src="./src/main.js"></script>'
    )
    (root / "src" / "cor.js").write_text("export const COR = '#0f380f';\n")
    (root / "src" / "main.js").write_text(
        "import { COR } from './cor.js';\n"
        "const c = document.getElementById('tela').getContext('2d');\n"
        "c.fillStyle = '#8bac0f'; c.fillRect(0, 0, 40, 40);\n" + draw
    )
    return root


def test_the_start_command_is_found_for_a_web_page(tmp_path: Path):
    assert start_command(tmp_path, None) == ""  # not a web page
    (tmp_path / "index.html").write_text("<p>oi</p>")
    (tmp_path / "package.json").write_text(json.dumps({"scripts": {"start": "node s.js"}}))
    assert start_command(tmp_path, None) == "npm start"
    assert start_command(tmp_path, "") == ""  # configured off
    assert start_command(tmp_path, "make serve") == "make serve"


def test_module_imports_are_followed():
    html = '<script src="./a.js" type="module"></script><script type="module" src="b.js"></script>'
    assert module_refs(html, html=True) == ["b.js", "./a.js"]
    js = "import { x } from './x.js';\nimport './side.js';\nexport { y } from '../y.js';\nimport z from 'lib';"
    assert module_refs(js, html=False) == ["./x.js", "../y.js", "./side.js"]


async def test_a_missing_module_and_a_dead_server_are_problems_without_a_browser(tmp_path: Path):
    site = _site(tmp_path / "site", "")
    (site / "src" / "cor.js").unlink()
    r = await smoke(site, SERVE, chrome="")
    assert any("/src/cor.js, which answers 404" in p for p in r.problems)
    assert any("not rendered" in n for n in r.notes)
    dead = await smoke(site, "sh -c 'echo quebrou; exit 1'", chrome="")
    assert (
        dead.problems
        and "did not serve the page" in dead.problems[0]
        and "quebrou" in dead.problems[0]
    )


@needs_chrome
async def test_a_page_that_draws_passes_and_a_blank_or_broken_one_fails(tmp_path: Path):
    good = await smoke(
        _site(tmp_path / "good", "c.fillStyle = COR; c.fillRect(10, 10, 5, 5);\n"), SERVE
    )
    assert good.ok, good.report()
    blank = await smoke(_site(tmp_path / "blank", ""), SERVE)
    assert any("#tela (40x40) is one solid colour" in p for p in blank.problems)
    broken = await smoke(_site(tmp_path / "broken", "naoExiste();\n"), SERVE)
    assert any("JavaScript error" in p and "naoExiste" in p for p in broken.problems)


async def test_the_inspector_fails_a_page_that_does_not_load(factory):
    """End to end: a story's page imports a module nobody wrote; the tests are green, the
    smoke test is not, and the Worker hears why."""
    from typing import Any

    from conftest import git
    from loompa.agents.dryrun import dry_run_script, role_of
    from loompa.engine import Scheduler, load_state
    from loompa.llm import Message
    from test_engine import make_ctx, seed_story

    root = factory.root
    (root / "index.html").write_text('<script type="module" src="./src/falta.js"></script>')
    git("add", ".", cwd=root)
    git("commit", "-qm", "feat: page", cwd=root)
    factory.config.quality.smoke_command = SERVE
    factory.save()
    seen: list[str] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if role_of(messages) == "worker" and "[smoke] FAIL" in "\n".join(
            m.content for m in messages
        ):
            seen.append("worker saw the smoke failure")
        return dry_run_script(model, messages, tools)

    ctx = make_ctx(type(factory).open(root), script)
    sid = seed_story(ctx, "Página")
    await Scheduler(ctx).run()
    events = [e for e in ctx.store.events_since(0, limit=10_000) if e["type"] == "inspector.smoke"]
    assert events and not events[0]["payload"]["ok"]
    assert any("/src/falta.js, which answers 404" in p for p in events[0]["payload"]["problems"])
    assert seen and "[smoke] FAIL" in "\n".join(load_state(ctx, sid).failure_history)
    await ctx.aclose()
