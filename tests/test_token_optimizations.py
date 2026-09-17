"""Prompt-cache layout, Anthropic cache_control and tool-history pruning."""

from __future__ import annotations

import httpx
import respx

from loompa.agents.worker import prune_tool_history
from loompa.config import default_config
from loompa.llm import Message, ToolCall
from loompa.llm.providers import AnthropicProvider


def test_prune_collapses_old_tool_results_only():
    msgs = [Message("system", "s", cache=True), Message("user", "u")]
    for i in range(10):
        msgs.append(Message("assistant", "", tool_calls=[ToolCall(f"c{i}", "read_file", {})]))
        msgs.append(
            Message(
                "tool", f"line one of {i}\n" + "x" * 2000, tool_call_id=f"c{i}", name="read_file"
            )
        )
    pruned = prune_tool_history(msgs, keep_last=3)
    assert pruned == 7
    tools = [m for m in msgs if m.role == "tool"]
    assert all(
        t.content.startswith("[resumido] resultado anterior de read_file") for t in tools[:7]
    )
    assert all(len(t.content) > 2000 for t in tools[-3:])
    assert "line one of 0" in tools[0].content
    assert prune_tool_history(msgs, keep_last=3) == 0  # idempotent
    short = [Message("tool", "ok", tool_call_id="x", name="done")] * 5
    assert prune_tool_history(short, keep_last=1) == 0  # short outputs untouched


@respx.mock
async def test_anthropic_marks_cached_system_block(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    route = respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "hi"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    p = AnthropicProvider("anthropic", default_config().providers["anthropic"])
    await p.complete(
        "claude-sonnet-5", [Message("system", "stable", cache=True), Message("user", "u")]
    )
    body = route.calls[0].request.content.decode()
    assert '"cache_control":{"type":"ephemeral"}' in body and '"text":"stable"' in body
    await p.complete("claude-sonnet-5", [Message("system", "plain"), Message("user", "u")])
    assert '"system":"plain"' in route.calls[1].request.content.decode()
    await p.aclose()


async def test_worker_prompt_has_stable_prefix(tmp_path, monkeypatch):
    """Task-specific text lives only in the user message; constitution/spec/plan in the system block."""
    from loompa.agents.worker import WorkerAgent
    from loompa.engine import EngineContext, StoryState
    from loompa.factory import bootstrap_factory
    from loompa.llm import MockProvider, ModelRouter

    monkeypatch.setenv("LOOMPA_HOME", str(tmp_path / "hub"))
    f = bootstrap_factory(tmp_path / "repo", name="P", preset="custom").factory
    seen: list[list[Message]] = []

    def script(model, messages, tools):
        seen.append(list(messages))
        return [ToolCall("d", "done", {"summary": "ok"})]

    provider = MockProvider("m", script=script)
    ctx = EngineContext.build(
        f,
        router=ModelRouter(f.config, providers=dict.fromkeys(f.config.providers, provider)),
        dry_run=True,
    )
    state = StoryState(story_id="S-001", title="Login", allowed_paths=["src/"])
    aci = ctx.aci_for(tmp_path / "repo")
    await WorkerAgent(ctx)._run_task(
        state, aci, 1, "criar tela", "SPEC-TEXT", "PLAN-TEXT", "- [ ] T1: criar tela"
    )
    await WorkerAgent(ctx)._run_task(
        state,
        aci,
        2,
        "criar teste",
        "SPEC-TEXT",
        "PLAN-TEXT",
        "- [x] T1: criar tela\n- [ ] T2: criar teste",
    )
    s1, s2 = seen[0][0], seen[1][0]
    assert (
        s1.role == "system" and s1.cache and s1.content == s2.content
    )  # identical prefix across tasks
    assert (
        "SPEC-TEXT" in s1.content
        and "PLAN-TEXT" in s1.content
        and "Regras invioláveis" in s1.content
    )
    assert "criar tela" in seen[0][1].content and "criar tela" not in s1.content
    ctx.close()
