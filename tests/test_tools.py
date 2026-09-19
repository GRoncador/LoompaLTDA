"""Fase 4 (ADR-0009): permission profiles per role, protected files and the generic tool loop."""

from __future__ import annotations

import json
from typing import Any

import pytest

from loompa.aci.tools import is_protected
from loompa.agents.base import FINAL_JSON, LoompaAgent
from loompa.agents.toolbox import (
    PROFILES,
    READ_TOOLS,
    Toolbox,
    profile_for,
)
from loompa.engine import StoryState
from loompa.factory import Factory
from loompa.llm import Message, ToolCall
from test_engine import factory, make_ctx  # noqa: F401


class Prober(LoompaAgent):
    role = "architect"
    display = "Prober"


# ------------------------------------------------------------------------- profiles


def test_profiles_by_role():
    worker, inspector = profile_for("worker"), profile_for("inspector")
    assert {"write_file", "run_tests", "done", "blocked"} <= worker.tools
    assert worker.write_paths is None  # the story's own plan decides where it may write
    assert "run_tests" in inspector.tools and not {"write_file", "edit_file"} & inspector.tools
    for role in ("architect", "product", "product_owner", "analyst", "master"):
        p = profile_for(role)
        assert p.tools >= READ_TOOLS and "run_tests" not in p.tools and "done" not in p.tools
        assert p.write_paths == (".loompa/specs/",) and p.worktree_write_paths == ("docs/",)
    unknown = profile_for("deployer")  # anyone else: read only, no writes at all
    assert unknown.tools == READ_TOOLS and unknown.write_paths == ()
    assert set(PROFILES) >= {"worker", "inspector", "analyst"}


async def test_profile_is_enforced_when_the_tool_is_called(factory: Factory):
    ctx = make_ctx(factory)
    box = Toolbox.for_role(ctx, "analyst")
    assert {t["name"] for t in box.spec()} == set(profile_for("analyst").tools)
    # a tool outside the profile is refused even though the model "made it up"
    res = await box.call("run_tests", {})
    assert not res.ok and "não está disponível para o papel analyst" in res.output
    # reads work anywhere in the repository
    assert (await box.call("read_file", {"path": "app/calc.py"})).ok
    # writes only land in the story artifacts
    outside = await box.call("write_file", {"path": "app/calc.py", "content": "x = 1\n"})
    assert not outside.ok and "fora do escopo" in outside.output
    assert (factory.root / "app" / "calc.py").read_text().startswith("def add")
    inside = await box.call(
        "write_file", {"path": ".loompa/specs/S-009/notes.md", "content": "# notas\n"}
    )
    assert inside.ok and (factory.root / ".loompa/specs/S-009/notes.md").is_file()
    await ctx.aclose()


async def test_offered_narrows_the_profile_and_never_widens_it(factory: Factory):
    ctx = make_ctx(factory)
    reader = Toolbox.for_role(ctx, "analyst", offered=READ_TOOLS)
    assert {t["name"] for t in reader.spec()} == set(READ_TOOLS)
    refused = await reader.call("write_file", {"path": ".loompa/specs/S-009/x.md", "content": "x"})
    assert not refused.ok and "não está disponível" in refused.output
    wide = Toolbox.for_role(ctx, "analyst", offered=READ_TOOLS | {"run_tests", "done"})
    assert "run_tests" not in {t["name"] for t in wide.spec()}  # the profile is the ceiling
    await ctx.aclose()


async def test_docs_are_writable_only_inside_a_worktree(factory: Factory):
    ctx = make_ctx(factory)
    main_checkout = Toolbox.for_role(ctx, "architect")
    res = await main_checkout.call("write_file", {"path": "docs/adr.md", "content": "# adr\n"})
    assert not res.ok and not (factory.root / "docs").exists()  # never dirty the founder's tree
    in_wt = Toolbox.for_role(ctx, "architect", in_worktree=True)
    assert (await in_wt.call("write_file", {"path": "docs/adr.md", "content": "# adr\n"})).ok
    await ctx.aclose()


async def test_worker_writes_follow_the_plan(factory: Factory):
    ctx = make_ctx(factory)
    box = Toolbox.for_role(ctx, "worker", allowed_paths=["app/"])
    assert (await box.call("write_file", {"path": "app/new.py", "content": "y = 2\n"})).ok
    assert not (await box.call("write_file", {"path": "other.py", "content": "y"})).ok
    assert (await box.call("note_learning", {"title": "dívida"})).ok and box.learnings
    await ctx.aclose()


# --------------------------------------------------------------------- protected files


def test_is_protected():
    for rel in (
        ".env",
        "app/.env.production",
        ".loompa/.env",
        ".loompa/state.db",
        ".loompa/logs/S-001.log",
        ".git/config",
        "certs/server.pem",
        "secrets.env",
        "id_rsa",
    ):
        assert is_protected(rel), rel
    for rel in (".env.example", "app/calc.py", ".loompa/specs/S-001/spec.md", "docs/env.md", "."):
        assert not is_protected(rel), rel


@pytest.mark.parametrize("role", ["worker", "analyst", "inspector"])
async def test_no_role_can_read_or_write_credentials(factory: Factory, role: str):
    (factory.root / ".env").write_text("STRIPE_KEY=sk-live-do-not-leak\n")
    (factory.root / ".loompa" / ".env").write_text("TAVILY_API_KEY=tvly-do-not-leak\n")
    (factory.root / ".env.example").write_text("STRIPE_KEY=\n")
    ctx = make_ctx(factory)
    box = Toolbox.for_role(ctx, role, allowed_paths=[""] if role == "worker" else None)
    for path in (".env", ".loompa/.env", ".loompa/state.db", ".git/config"):
        res = await box.call("read_file", {"path": path})
        assert not res.ok and "protegido" in res.output, path
        assert "do-not-leak" not in res.output
    assert (await box.call("read_file", {"path": ".env.example"})).ok
    if role == "worker":
        res = await box.call("write_file", {"path": ".env", "content": "X=1\n"})
        assert not res.ok and "protegido" in res.output
    await ctx.aclose()


# ---------------------------------------------------------------------------- the loop


def scripted_loop(replies: list[Any]):
    """A provider script that answers with `replies` in order (a repeat of the last one after)."""
    seen: list[list[Message]] = []

    def script(model: str, messages: list[Message], tools: list[dict[str, Any]] | None) -> Any:
        seen.append(list(messages))
        return replies[min(len(seen) - 1, len(replies) - 1)]

    return script, seen


async def test_loop_runs_tools_and_ends_on_plain_text(factory: Factory):
    script, seen = scripted_loop(
        [[ToolCall("c1", "list_dir", {"path": "app"})], "terminei: só há calc.py"]
    )
    ctx = make_ctx(factory, script)
    agent = Prober(ctx)
    box = agent.explore_tools()
    messages = [Message("system", "s"), Message("user", "u")]
    res = await agent.tool_loop(messages, box, story=StoryState(story_id="S-1", title="t"))
    assert res.ended_by == "text" and res.text.startswith("terminei") and res.tool_calls == 1
    tool_msgs = [m for m in messages if m.role == "tool"]
    assert len(tool_msgs) == 1 and "calc.py" in tool_msgs[0].content
    events = [e for e in ctx.store.events_since(0, limit=1000) if e["type"] == "tool.call"]
    assert events[0]["payload"]["tool"] == "list_dir" and events[0]["payload"]["ok"] is True
    assert seen[0] and len(seen) == 2
    await ctx.aclose()


async def test_loop_stops_at_terminal_tools_without_running_them(factory: Factory):
    script, _ = scripted_loop(
        [
            [
                ToolCall("c1", "read_file", {"path": "app/calc.py"}),
                ToolCall("c2", "done", {"summary": "ok"}),
            ]
        ]
    )
    ctx = make_ctx(factory, script)
    agent = Prober(ctx)
    box = Toolbox.for_role(ctx, "worker")
    res = await agent.tool_loop(
        [Message("system", "s"), Message("user", "u")], box, terminal=("done", "blocked")
    )
    assert res.ended_by == "done" and res.args == {"summary": "ok"} and res.tool_calls == 1
    await ctx.aclose()


async def test_loop_nudges_once_and_reports_the_limit(factory: Factory):
    script, seen = scripted_loop(["ainda pensando", [ToolCall("c", "list_dir", {})]])
    ctx = make_ctx(factory, script)
    agent = Prober(ctx)
    messages = [Message("system", "s"), Message("user", "u")]
    res = await agent.tool_loop(
        messages, agent.explore_tools(), max_iterations=3, nudge="continue com as ferramentas"
    )
    assert res.ended_by == "limit" and res.tool_calls == 2
    assert any(m.role == "user" and m.content == "continue com as ferramentas" for m in messages)
    await ctx.aclose()


async def test_loop_prunes_old_tool_results(factory: Factory):
    (factory.root / "app" / "big.py").write_text("\n".join(f"x{i} = {i}" for i in range(150)))
    script, _ = scripted_loop([[ToolCall("c", "read_file", {"path": "app/big.py", "lines": 150})]])
    ctx = make_ctx(factory, script)
    agent = Prober(ctx)
    messages = [Message("system", "s"), Message("user", "u")]
    await agent.tool_loop(messages, agent.explore_tools(), max_iterations=4, keep_tool_results=1)
    tools = [m for m in messages if m.role == "tool"]
    assert tools[-1].content.startswith("app/big.py [")
    assert all(t.content.startswith("[resumido]") for t in tools[:-1])
    await ctx.aclose()


# ------------------------------------------------------------------ JSON with tools


async def test_ask_json_with_tools_explores_then_answers(factory: Factory):
    script, seen = scripted_loop(
        [
            [ToolCall("c1", "search", {"pattern": "def add"})],
            'Aqui está:\n```json\n{"files": ["app/calc.py"], "tasks": ["x"]}\n```',
        ]
    )
    ctx = make_ctx(factory, script)
    agent = Prober(ctx)
    data = await agent.ask_json_with_tools("sys", "user", agent.explore_tools())
    assert data == {"files": ["app/calc.py"], "tasks": ["x"]}
    assert any(m.role == "tool" and "calc.py" in m.content for m in seen[1])
    await ctx.aclose()


async def test_ask_json_with_tools_is_one_shot_when_disabled(factory: Factory):
    calls: list[tuple[bool, Any]] = []

    def script(model: str, messages: list[Message], tools: Any) -> Any:
        calls.append((True, tools))
        return json.dumps({"ok": True})

    ctx = make_ctx(factory, script)
    ctx.config.schedule.agent_tool_iterations = 0
    agent = Prober(ctx)
    assert await agent.ask_json_with_tools("s", "u", agent.explore_tools()) == {"ok": True}
    assert calls == [(True, None)]  # the plain ask_json path: no tools offered
    await ctx.aclose()


async def test_ask_json_with_tools_forces_an_answer_when_the_budget_runs_out(factory: Factory):
    def script(model: str, messages: list[Message], tools: Any) -> Any:
        if any(m.content == FINAL_JSON for m in messages if m.role == "user"):
            return json.dumps({"approach": "com o que deu"})
        return [ToolCall(f"c{len(messages)}", "list_dir", {})]

    ctx = make_ctx(factory, script)
    agent = Prober(ctx)
    data = await agent.ask_json_with_tools("s", "u", agent.explore_tools(), max_iterations=2)
    assert data == {"approach": "com o que deu"}
    await ctx.aclose()


async def test_ask_json_with_tools_retries_invalid_json_once(factory: Factory):
    script, _ = scripted_loop(["isto não é json", json.dumps({"ok": 1})])
    ctx = make_ctx(factory, script)
    agent = Prober(ctx)
    assert await agent.ask_json_with_tools("s", "u", agent.explore_tools()) == {"ok": 1}
    await ctx.aclose()
