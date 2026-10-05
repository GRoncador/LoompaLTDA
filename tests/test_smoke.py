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
async def test_a_page_that_draws_passes_and_a_blank_or_broken_one_fails(
    tmp_path: Path, monkeypatch
):
    # these pages draw synchronously on load: the 3s a real game's timers get is not needed
    monkeypatch.setattr("loompa.smoke.SETTLE_S", 1.0)
    good = await smoke(
        _site(tmp_path / "good", "c.fillStyle = COR; c.fillRect(10, 10, 5, 5);\n"), SERVE
    )
    assert good.ok, good.report()
    blank = await smoke(_site(tmp_path / "blank", ""), SERVE)
    assert any("#tela (40x40) is one solid colour" in p for p in blank.problems)
    broken = await smoke(_site(tmp_path / "broken", "naoExiste();\n"), SERVE)
    assert any("JavaScript error" in p and "naoExiste" in p for p in broken.problems)


async def test_the_inspector_fails_a_page_that_does_not_load(factory, monkeypatch):
    """End to end: a story's page imports a module nobody wrote; the tests are green, the
    smoke test is not, and the Worker hears why."""
    # the 404 is found before any browser: starting Chrome on every round only cost time
    monkeypatch.setattr("loompa.smoke.find_chrome", lambda: None)
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


def test_a_drawing_cut_by_the_canvas_edge_is_a_problem():
    from loompa.smoke import clipped_edges

    # tamagotchi S-048: the sprite's hand and feet past the right and bottom edges
    assert clipped_edges({"top": 0, "bottom": 0.18, "left": 0, "right": 0.05}) == [
        "bottom",
        "right",
    ]
    assert clipped_edges({"top": 1, "bottom": 1, "left": 1, "right": 1}) == []  # a frame
    assert clipped_edges({"top": 0, "bottom": 0, "left": 0, "right": 0}) == []
    assert clipped_edges({}) == []


def _shell_page(root: Path, *, screen: int, shell: int, wide: int = 0) -> Path:
    """A round shell (border-radius 50%) holding a canvas, as tamagotchi's egg holds its LCD;
    `wide` adds a block wider than a phone."""
    root.mkdir(parents=True)
    extra = f'<div style="width:{wide}px;height:10px"></div>' if wide else ""
    (root / "index.html").write_text(
        '<!doctype html><meta name="viewport" content="width=device-width, initial-scale=1">'
        f'<div id="carca" style="width:{shell}px;height:{shell}px;border-radius:50%;'
        'background:#ddd;display:flex;align-items:center;justify-content:center">'
        f'<canvas id="tela" width="{screen}" height="{screen}"></canvas></div>{extra}'
        "<script>const c = document.getElementById('tela').getContext('2d');"
        f"c.fillStyle = '#8bac0f'; c.fillRect(0, 0, {screen}, {screen});"
        "c.fillStyle = '#0f380f'; c.fillRect(20, 20, 10, 10);</script>"
    )
    return root


def test_layout_problems_are_keyed_without_their_pixels():
    from loompa.smoke import layout_problems

    seen = {
        "width": 390,
        "content": 825,
        "sticking": [{"el": "#tela-lcd", "box": "#carca-ovo", "corners": ["top-left"]}],
    }
    out = layout_problems("mobile", seen)
    assert set(out) == {"mobile:scroll", "mobile:#tela-lcd>#carca-ovo"}
    assert "content is 825 px wide" in out["mobile:scroll"]
    assert (
        "crosses" not in out["mobile:scroll"]
        and "rounded edge" in out["mobile:#tela-lcd>#carca-ovo"]
    )
    assert layout_problems("mobile", {"width": 390, "content": 390, "sticking": []}) == {}


@needs_chrome
async def test_a_screen_crossing_its_rounded_shell_and_a_page_wider_than_a_phone_fail(
    tmp_path: Path, monkeypatch
):
    """ADR-0030: tamagotchi S-054's first delivery put the LCD across the egg's rounded edge
    (the rectangles fit: only the shape was crossed) and the page was wider than a phone; the
    founder saw both, every test was green. Screenshots go to `shots_dir`."""
    monkeypatch.setattr("loompa.smoke.SETTLE_S", 1.0)
    shots = tmp_path / "shots"
    bad = await smoke(
        _shell_page(tmp_path / "bad", screen=300, shell=340, wide=700), SERVE, shots_dir=shots
    )
    assert any("#tela sticks out of #carca" in p and "rounded edge" in p for p in bad.problems)
    assert any("at 390 px wide the page scrolls sideways" in p for p in bad.problems)
    assert sorted(bad.shots) == ["desktop.png", "mobile.png"]
    assert all((shots / n).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" for n in bad.shots)
    good = await smoke(_shell_page(tmp_path / "good", screen=200, shell=340), SERVE)
    assert good.ok, good.report()
    # what the base already has is the base's: the story is not failed for it
    known = set(bad.layout)
    again = await smoke(
        _shell_page(tmp_path / "same", screen=300, shell=340, wide=700),
        SERVE,
        layout_baseline=known,
    )
    assert again.ok and any("the base already has" in n for n in again.notes)


async def test_a_delivery_carries_the_screenshots_and_the_base_is_measured_once(
    factory, monkeypatch
):
    """End to end (ADR-0030): the Inspector's smoke saves the screenshots under the hub, the
    delivery lists them for the founder, and the base's layout is measured once per baseline."""
    from loompa.agents.deployer import SHOTS_NOTE
    from loompa.agents.inspector import shots_dir
    from loompa.engine import Scheduler, load_state
    from loompa.smoke import SmokeResult
    from test_engine import make_ctx, seed_story

    runs: list[str] = []

    async def fake_smoke(root, command, *, chrome=None, shots_dir=None, layout_baseline=None):  # type: ignore[no-untyped-def]
        runs.append("story" if shots_dir else "base")
        out = SmokeResult(ran=True, rendered=True, layout={"mobile:scroll": "x"})
        if shots_dir is not None:
            shots_dir.mkdir(parents=True, exist_ok=True)
            for name in ("desktop.png", "mobile.png"):
                (shots_dir / name).write_bytes(b"\x89PNG\r\n\x1a\n")
                out.shots.append(name)
        out.problems = [] if layout_baseline == {"mobile:scroll"} else ["not compared to the base"]
        return out

    monkeypatch.setattr("loompa.agents.inspector.smoke", fake_smoke)
    monkeypatch.setattr("loompa.agents.inspector.start_command", lambda root, cmd: "serve")
    ctx = make_ctx(factory)
    sid = seed_story(ctx, "Página")
    await Scheduler(ctx).run()
    state = load_state(ctx, sid)
    assert state.blocked_reason == "delivery", state.failure_history
    assert runs == ["base", "story"]
    assert state.extra["baseline"]["layout"] == ["mobile:scroll"]
    msg = ctx.store.get_message(state.blocked_message_id)
    assert msg.images == ["desktop.png", "mobile.png"] and SHOTS_NOTE in msg.context
    assert msg.executive_audit() == []
    assert (shots_dir(ctx.slug, sid) / "mobile.png").is_file()
    await ctx.aclose()
