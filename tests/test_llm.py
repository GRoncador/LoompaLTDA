import httpx
import pytest
import respx

from loompa.config import default_config
from loompa.finance import CostTracker
from loompa.llm import (
    LLMError,
    Message,
    MockProvider,
    ModelRouter,
    OpenAICompatibleProvider,
    QuotaExhausted,
    ToolCall,
)
from loompa.llm.providers import AnthropicProvider, extract_json
from loompa.store import Store

OPENAI_OK = {
    "id": "x",
    "model": "deepseek-chat",
    "choices": [
        {
            "finish_reason": "tool_calls",
            "message": {
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {"name": "read_file", "arguments": '{"path": "a.py"}'},
                    }
                ],
            },
        }
    ],
    "usage": {"prompt_tokens": 120, "completion_tokens": 20, "prompt_cache_hit_tokens": 100},
}


@respx.mock
async def test_openai_compatible_provider_parses_tool_calls(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    cfg = default_config()
    route = respx.post("https://api.deepseek.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=OPENAI_OK)
    )
    p = OpenAICompatibleProvider("deepseek", cfg.providers["deepseek"])
    resp = await p.complete(
        "deepseek-chat",
        [Message("system", "s"), Message("user", "u")],
        tools=[{"name": "read_file", "parameters": {"type": "object"}}],
    )
    assert resp.tool_calls == [ToolCall("c1", "read_file", {"path": "a.py"})]
    assert (
        resp.input_tokens == 120
        and resp.cached_tokens == 100
        and resp.finish_reason == "tool_calls"
    )
    body = route.calls[0].request
    assert body.headers["Authorization"] == "Bearer k"
    assert b'"tools"' in body.content and b'"messages"' in body.content
    await p.aclose()


@respx.mock
async def test_provider_errors(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    cfg = default_config()
    p = OpenAICompatibleProvider("deepseek", cfg.providers["deepseek"])
    respx.post("https://api.deepseek.com/v1/chat/completions").mock(
        return_value=httpx.Response(429, json={})
    )
    with pytest.raises(QuotaExhausted):
        await p.complete("deepseek-chat", [Message("user", "u")])
    respx.post("https://api.deepseek.com/v1/chat/completions").mock(
        return_value=httpx.Response(400, text="bad request")
    )
    with pytest.raises(LLMError, match="bad request"):
        await p.complete("deepseek-chat", [Message("user", "u")])
    monkeypatch.delenv("DEEPSEEK_API_KEY")
    p2 = OpenAICompatibleProvider("deepseek", cfg.providers["deepseek"])
    assert not p2.available()
    with pytest.raises(LLMError, match="DEEPSEEK_API_KEY"):
        await p2.complete("deepseek-chat", [Message("user", "u")])


@respx.mock
async def test_anthropic_provider(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    cfg = default_config()
    respx.post("https://api.anthropic.com/v1/messages").mock(
        return_value=httpx.Response(
            200,
            json={
                "model": "claude-sonnet-5",
                "stop_reason": "tool_use",
                "content": [
                    {"type": "text", "text": "hi"},
                    {"type": "tool_use", "id": "t1", "name": "done", "input": {"summary": "s"}},
                ],
                "usage": {"input_tokens": 10, "output_tokens": 5, "cache_read_input_tokens": 4},
            },
        )
    )
    p = AnthropicProvider("anthropic", cfg.providers["anthropic"])
    msgs = [
        Message("system", "sys"),
        Message("user", "u"),
        Message("assistant", "", tool_calls=[ToolCall("t0", "read_file", {"path": "a"})]),
        Message("tool", "content", tool_call_id="t0"),
    ]
    resp = await p.complete(
        "claude-sonnet-5", msgs, tools=[{"name": "done", "parameters": {"type": "object"}}]
    )
    assert (
        resp.text == "hi"
        and resp.tool_calls[0].name == "done"
        and resp.input_tokens == 14
        and resp.cached_tokens == 4
    )
    await p.aclose()


def scripted(model, messages, tools):
    return f"{model} says hi"


async def test_router_falls_through_and_meters_cost():
    cfg = default_config()
    store = Store(":memory:")
    tracker = CostTracker(store, cfg, "f")
    calls: list[str] = []
    bad = MockProvider("deepseek", script=lambda m, msgs, t: QuotaExhausted("quota"))
    good = MockProvider("gemini", script=scripted)
    router = ModelRouter(
        cfg,
        tracker=tracker,
        providers={"deepseek": bad, "gemini": good},
        on_call=lambda role, agent, rc: calls.append(rc.candidate.model),
    )
    rc = await router.complete(
        "worker", [Message("user", "hello world" * 100)], agent="worker-1", story_id="S-1"
    )
    assert (
        rc.candidate.provider == "gemini"
        and rc.tier == "tier2"
        and rc.response.text == "gemini-2.5-flash says hi"
    )
    assert calls == ["gemini-2.5-flash"] and rc.cost_usd > 0
    assert (
        store.usage_totals("f")["calls"] == 1
        and store.usage_by("model", "f")[0]["key"] == "gemini-2.5-flash"
    )
    # deepseek is now in cooldown: the second call skips it without invoking it again
    n = len(bad.calls)
    rc2 = await router.complete("architect", [Message("user", "x")], tier_override="tier2")
    assert rc2.candidate.provider == "gemini" and len(bad.calls) == n
    # tier override to tier1 -> deepseek-reasoner is a different key, so it's tried and fails; gemini pro answers
    rc3 = await router.complete("worker", [Message("user", "x")], tier_override="tier1")
    assert rc3.candidate.model == "gemini-2.5-pro" and rc3.tier == "tier1"


async def test_router_all_fail_raises():
    cfg = default_config()
    cfg.models.tiers["tier2"] = cfg.models.tiers["tier2"][:1]
    router = ModelRouter(
        cfg,
        providers={"deepseek": MockProvider("deepseek", script=lambda *a: LLMError("boom"))},
        max_retries=0,
    )
    with pytest.raises(LLMError, match="todos os modelos"):
        await router.complete("worker", [Message("user", "x")])


def test_extract_json():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('prefix {"a": [1, 2]} suffix') == {"a": [1, 2]}
    assert extract_json("[1, 2]") == [1, 2]
    with pytest.raises(ValueError):
        extract_json("nothing here")
