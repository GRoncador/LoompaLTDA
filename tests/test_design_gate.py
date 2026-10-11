"""The design gate (plan Phase 15, ADR-0039): screens approved before code."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import git
from loompa.agents import MasterAgent
from loompa.comms import FounderAnswer
from loompa.design import (
    DesignPaths,
    approved_html,
    colours_in,
    contrast_problems,
    load_tokens,
    tokens_css,
    with_tokens,
)
from loompa.engine import Scheduler, Stage, load_state
from loompa.engine.design_gate import BASE_KEY, DESIGN_KEY
from loompa.engine.phases import build_route
from loompa.factory import Factory, bootstrap_factory
from test_engine import PYTEST_CMD, make_ctx


@pytest.fixture
def web(git_repo: Path, hub) -> Factory:
    (git_repo / "index.html").write_text("<!doctype html><html lang='pt'><body>oi</body></html>")
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    git("add", ".", cwd=git_repo)
    git("commit", "-qm", "feat: page", cwd=git_repo)
    f = bootstrap_factory(git_repo, name="Web", store=hub).factory
    f.config.quality.test_command = PYTEST_CMD
    f.config.quality.lint_command = ""
    f.config.quality.typecheck_command = ""
    f.config.quality.smoke_command = ""
    f.save()
    return Factory.open(git_repo)


def test_the_route_puts_the_screens_between_spec_and_plan():
    route = build_route("feature", "STANDARD", design=True)
    assert route[route.index("spec_review") + 1 : route.index("plan")] == [
        "design",
        "design_review",
    ]
    simple = build_route("feature", "SIMPLE", design=True)
    assert simple[simple.index("spec") + 1 : simple.index("plan")] == ["design", "design_review"]
    assert "design" not in build_route("feature", "STANDARD")


def test_tokens_become_css_variables_and_screens_carry_them():
    tokens = {"color": {"primary": "#0B57D0", "on-primary": "#fff"}, "space": ["4px", "8px"]}
    css = tokens_css(tokens)
    assert "--color-primary: #0B57D0;" in css and "--space-2: 8px;" in css
    page = with_tokens("<html><head><script>x()</script></head><body>a</body></html>", css)
    assert page.count('data-loompa="tokens"') == 1 and "<script" not in page
    again = with_tokens(page, css)
    assert again.count('data-loompa="tokens"') == 1
    assert colours_in("color:#ABC; background: rgb(1, 2, 3)") == {"#aabbcc", "rgb(1,2,3)"}


def test_contrast_is_measured_on_the_pairs_the_base_promises():
    good = {
        "color": {"text": "#111111", "bg": "#ffffff", "primary": "#0b57d0", "on-primary": "#fff"}
    }
    assert contrast_problems(good) == []
    bad = {"color": {"text": "#cccccc", "bg": "#ffffff"}}
    assert "text" in contrast_problems(bad)[0]


async def test_a_screen_story_gets_a_base_then_screens_then_the_plan(web: Factory):
    ctx = make_ctx(web, dry_run=True)
    master = MasterAgent(ctx)
    await master.meeting("Tela de cadastro")
    master.start_sprint()
    await Scheduler(ctx).run()
    state = load_state(ctx, "S-001")
    # first the visual base: two alternatives, photographed, for the founder to pick
    assert state.stage == Stage.AWAITING_FOUNDER and state.blocked_reason == "design_base"
    assert state.extra[BASE_KEY] == "asked" and state.extra[DESIGN_KEY]["mode"] == "light"
    msg = ctx.store.get_message(state.blocked_message_id)
    assert [o.key for o in msg.options] == ["1", "2", "adjust"]
    assert len(msg.screens) == 2 and not msg.executive_audit()
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="2"))
    assert load_tokens(web.root)["color"]["primary"] == "#0b57d0"
    assert "## Front-end" in web.paths.constitution.read_text()
    await Scheduler(ctx).run()
    state = load_state(ctx, "S-001")
    # no open question (dry run), so the screens were drawn and wait for approval
    assert state.blocked_reason == "design", state.blocked_reason
    msg = ctx.store.get_message(state.blocked_message_id)
    assert msg.screens and msg.screens[0]["html"].startswith("S-001/round-1/")
    assert (DesignPaths.of(web.root).root / msg.screens[0]["html"]).is_file()
    # an adjustment makes a second round
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="adjust", text="botão maior"))
    await Scheduler(ctx).run()
    state = load_state(ctx, "S-001")
    assert state.extra[DESIGN_KEY]["round"] == 2 and state.blocked_reason == "design"
    msg = ctx.store.get_message(state.blocked_message_id)
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="approve"))
    assert (DesignPaths.of(web.root).approved("S-001") / "manifest.json").is_file()
    assert "Approved screens" in approved_html(web.root, "S-001")
    await Scheduler(ctx).run()
    state = load_state(ctx, "S-001")
    assert state.plan_ready and state.blocked_reason == "delivery"


async def test_a_second_screen_story_waits_for_the_base_of_the_first(web: Factory):
    ctx = make_ctx(web, dry_run=True)
    master = MasterAgent(ctx)
    await master.meeting("Tela de cadastro; Página de perfil")
    master.start_sprint()
    web.config.schedule.max_parallel = 1
    await Scheduler(ctx, max_parallel=1).run()
    states = [load_state(ctx, s) for s in ("S-001", "S-002")]
    asked = [s for s in states if s.blocked_reason == "design_base"]
    parked = [s for s in states if s.blocked_reason != "design_base"]
    assert len(asked) == 1 and len(parked) == 1
    assert parked[0].extra.get("waiting", {}).get("design_base") == asked[0].story_id
    msg = ctx.store.get_message(asked[0].blocked_message_id)
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="1"))
    await Scheduler(ctx).run()
    for sid in ("S-001", "S-002"):
        assert load_state(ctx, sid).blocked_reason == "design"


async def test_a_factory_without_a_screen_skips_the_gate(git_repo: Path, hub):
    (git_repo / "tests").mkdir()
    (git_repo / "tests" / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    git("add", ".", cwd=git_repo)
    git("commit", "-qm", "feat: lib", cwd=git_repo)
    f = bootstrap_factory(git_repo, name="Lib", store=hub).factory
    f.config.quality.test_command = PYTEST_CMD
    f.config.quality.lint_command = ""
    f.save()
    ctx = make_ctx(Factory.open(git_repo), dry_run=True)
    master = MasterAgent(ctx)
    await master.meeting("Página de ajuda")
    master.start_sprint()
    await Scheduler(ctx).run()
    state = load_state(ctx, "S-001")
    assert "design" not in state.route and state.blocked_reason == "delivery"
    assert json.dumps(state.extra).find(DESIGN_KEY) == -1


def test_colours_outside_the_tokens_in_style_files_are_refused():
    from loompa.design.check import stray_colours

    tokens = {"color": {"primary": "#0b57d0", "bg": "#fff"}}
    diff = (
        "diff --git a/src/app.css b/src/app.css\n+++ b/src/app.css\n"
        "+.a { color: #0B57D0; background: #ffffff; border-color: #123456 }\n"
        "diff --git a/src/sprites.js b/src/sprites.js\n+++ b/src/sprites.js\n"
        "+const PALETTE = ['#ff00ff']\n"
        "diff --git a/tests/page.test.css b/tests/page.test.css\n+++ b/tests/page.test.css\n"
        "+.x { color: #abcdef }\n"
    )
    assert stray_colours(diff, tokens) == [("src/app.css", "#123456")]


def test_a_figma_link_names_its_file_and_frame():
    from loompa.design.figma import FigmaError, parse_link

    ref = parse_link("https://www.figma.com/design/AbC123/Loja?node-id=12-34&t=x")
    assert (ref.key, ref.node) == ("AbC123", "12:34")
    for bad in ("https://example.com/design/x?node-id=1-2", "https://www.figma.com/design/AbC"):
        with pytest.raises(FigmaError):
            parse_link(bad)


async def test_a_figma_frame_is_read_through_the_rest_api():
    import httpx

    from loompa.design.figma import fetch_frame

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("X-Figma-Token") in (None, "tok")
        if request.url.path == "/v1/images/AbC123":
            return httpx.Response(200, json={"images": {"1:2": "https://cdn.figma/x.png"}})
        if request.url.host == "cdn.figma":
            return httpx.Response(200, content=b"PNG")
        doc = {
            "name": "Home",
            "type": "FRAME",
            "children": [{"type": "TEXT", "name": "t", "characters": "Olá"}],
        }
        return httpx.Response(200, json={"nodes": {"1:2": {"document": doc}}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        picture, outline = await fetch_frame(
            "https://www.figma.com/file/AbC123/x?node-id=1-2", "tok", client=client
        )
    assert picture == b"PNG" and "Olá" in outline and "frame Home" in outline


async def test_front_end_questions_are_asked_once_and_answered_together(web: Factory):
    from loompa.agents.dryrun import dry_run_script, role_of

    def script(model, messages, tools):  # noqa: ANN001
        if role_of(messages) == "ux" and "front-end questions" in messages[0].content:
            return json.dumps(
                {
                    "questions": [
                        {
                            "text": "Aparelho principal?",
                            "why": "muda o layout",
                            "options": ["Celular", "Computador"],
                            "recommended": 0,
                        },
                        {
                            "text": "Lista vazia mostra?",
                            "why": "estado vazio",
                            "options": ["Uma mensagem", "Um exemplo"],
                            "recommended": 1,
                        },
                    ],
                    "assumptions": [],
                }
            )
        if role_of(messages) == "ux" and "Draw the screens" in messages[0].content:
            seen.append(messages[-1].content)
        return dry_run_script(model, messages, tools)

    seen: list[str] = []
    ctx = make_ctx(web, script, dry_run=True)
    from loompa.design import DesignPaths as _P

    tokens = _P.of(web.root)
    tokens.root.mkdir(parents=True, exist_ok=True)
    tokens.tokens.write_text(json.dumps({"color": {"bg": "#ffffff", "text": "#111111"}}))
    master = MasterAgent(ctx)
    await master.meeting("Tela de histórico")
    master.start_sprint()
    await Scheduler(ctx).run()
    state = load_state(ctx, "S-001")
    assert state.blocked_reason == "design_questions"
    msg = ctx.store.get_message(state.blocked_message_id)
    assert [q.id for q in msg.questions] == ["q1", "q2"]
    assert [o.recommended for o in msg.questions[1].options] == [False, True]
    await Scheduler(ctx).aanswer(
        msg.id, FounderAnswer(option_key="answer", answers={"q1": "1", "q2": "uma ilustração"})
    )
    await Scheduler(ctx).run()
    state = load_state(ctx, "S-001")
    assert state.blocked_reason == "design" and state.extra[DESIGN_KEY]["asked"]
    assert "Aparelho principal? → Computador" in seen[-1]
    assert "Lista vazia mostra? → uma ilustração" in seen[-1]


def test_references_are_kept_listed_and_served(web: Factory, hub):
    from fastapi.testclient import TestClient

    from loompa.dashboard.app import create_app

    png = (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe\x02\xfe\xa7\x35\x81"
        b"\x84\x00\x00\x00\x00IEND\xaeB`\x82"
    )
    with TestClient(create_app(dry_run=True, run_engine=False, store=hub)) as c:
        slug = web.slug
        r = c.post(
            f"/api/factories/{slug}/design/references",
            data={"kind": "image", "title": "paleta", "note": "algo assim, mais claro"},
            files={"file": ("p.png", png, "image/png")},
        )
        assert r.status_code == 200, r.text
        ref = r.json()["reference"]
        assert ref["id"] == "R-001" and ref["file"] == "R-001.png"
        r = c.post(
            f"/api/factories/{slug}/design/references",
            data={"kind": "text", "note": "minimalista", "story_id": "S-009"},
        )
        assert r.json()["reference"]["story_id"] == "S-009"
        assert c.get(f"/api/factories/{slug}/design/file/references/R-001.png").content == png
        assert c.get(f"/api/factories/{slug}/design/file/../config.yaml").status_code == 404
        view = c.get(f"/api/factories/{slug}/design").json()
        assert [r["id"] for r in view["references"]] == ["R-001"]  # S-009's is the story's
        story = c.get(f"/api/factories/{slug}/stories/S-009/design").json()
        assert [r["id"] for r in story["references"]] == ["R-001", "R-002"]
        assert c.delete(f"/api/factories/{slug}/design/references/R-001").status_code == 200
        bad = c.post(
            f"/api/factories/{slug}/design/references", data={"kind": "link", "url": "ftp://x"}
        )
        assert bad.status_code == 400


async def test_the_built_page_is_compared_with_the_approved_screen(web: Factory, tmp_path: Path):
    """15.6: past the threshold a model with vision judges; a deviation fails with the pictures."""
    import sys

    from loompa.agents.ux import UXAgent
    from loompa.config.schema import ModelCandidate
    from loompa.design import write_json
    from loompa.design.check import check_design
    from loompa.design.render import file_url, look_at
    from loompa.engine import StoryState
    from loompa.smoke import find_chrome

    if find_chrome() is None:
        pytest.skip("no Chrome or Chromium here")
    paths = DesignPaths.of(web.root)
    approved = paths.approved("S-001") / "home"
    approved.mkdir(parents=True)
    page = "<!doctype html><html lang='pt'><body style='margin:0'><div style='height:900px;background:{c}'>x</div></body></html>"
    (approved / "main.html").write_text(page.format(c="#ffffff"))
    await look_at([(file_url(approved / "main.html"), approved / "main")])
    write_json(
        paths.approved("S-001") / "manifest.json",
        {
            "screens": [
                {"id": "home", "dir": "home", "title": "Início", "route": "/", "states": ["main"]}
            ]
        },
    )
    built = tmp_path / "built"
    built.mkdir()
    (built / "index.html").write_text(page.format(c="#0b57d0"))
    asked: list[str] = []

    def script(model, messages, tools):  # noqa: ANN001
        asked.append(messages[-1].content)
        assert messages[-1].images and messages[-1].images[0].startswith("data:image/png;base64,")
        return json.dumps({"deviation": True, "workaround": False, "changes": ["the page is blue"]})

    web.config.models.vision = [ModelCandidate(provider="openrouter", model="seer")]
    ctx = make_ctx(web, script)
    serve = f"""sh -c '"{sys.executable}" -m http.server $PORT --bind 127.0.0.1'"""
    out = await check_design(
        web.root,
        built,
        StoryState(story_id="S-001", title="Início"),
        "",
        smoke_cmd=serve,
        shots_dir=tmp_path / "shots",
        threshold=0.1,
        ux=UXAgent(ctx),
    )
    assert out.problems and "deviates: the page is blue" in out.problems[0]
    assert "compare-home-desktop.png" in out.shots
    assert (tmp_path / "shots" / "compare-home-desktop.png").is_file()
    assert asked and "approved on the left" in asked[0]
    await ctx.aclose()


def test_the_gate_and_the_vision_models_are_set_in_the_settings(web: Factory):
    from loompa.config.schema import ModelCandidate
    from loompa.config.settings import SettingsPatch, apply_settings
    from loompa.factory_kind import design_gate

    cfg = web.config
    assert design_gate(cfg) == "light"  # a personal factory, gate on auto
    cfg.factory.kind = "commercial"
    assert design_gate(cfg) == "full"
    seer = ModelCandidate(provider="openrouter", model="seer")
    notes = apply_settings(web.root, cfg, SettingsPatch(design_gate="off", vision=[seer]))
    assert notes == ["sem telas antes do código", "modelos com visão atualizados"]
    assert design_gate(cfg) == "off" and cfg.models.vision[0].model == "seer"
    assert apply_settings(web.root, cfg, SettingsPatch(design_gate="off", vision=[seer])) == []
    with pytest.raises(ValueError):
        apply_settings(
            web.root, cfg, SettingsPatch(vision=[ModelCandidate(provider="x", model="y")])
        )
