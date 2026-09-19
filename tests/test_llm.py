import json
from typing import Any

import httpx
import pytest
import respx

from loompa.config import default_config
from loompa.config.schema import ModelCandidate
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
from loompa.llm.providers import AnthropicProvider, _retry_after_seconds, extract_json
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


GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
SIGNED = {"google": {"thought_signature": "c2lnbmF0dXJl"}}
GEMINI_TOOL_CALL = {
    "id": "function-call-1",
    "type": "function",
    "extra_content": SIGNED,
    "function": {"name": "list_dir", "arguments": '{"path": "."}'},
}


def gemini_reply(tool_calls: list[dict] | None = None, text: str = "") -> httpx.Response:
    message: dict = {"content": text}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return httpx.Response(
        200,
        json={
            "model": "gemini-3.5-flash-lite",
            "choices": [{"finish_reason": "stop", "message": message}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        },
    )


def sent_tool_calls(route: respx.Route, call: int = -1) -> list[dict]:
    body = json.loads(route.calls[call].request.content)
    return [tc for m in body["messages"] for tc in m.get("tool_calls") or []]


@respx.mock
async def test_gemini_thought_signatures_go_back_with_the_function_call(monkeypatch):
    """Gemini 3 answers 400 ("missing a thought_signature") when a function call in the history
    does not carry the signature it came with. The adapter keeps it and returns it."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    p = OpenAICompatibleProvider("gemini", default_config().providers["gemini"])
    route = respx.post(GEMINI_URL).mock(
        side_effect=[gemini_reply([GEMINI_TOOL_CALL]), gemini_reply(text="pronto")]
    )
    tools = [{"name": "list_dir", "parameters": {"type": "object"}}]
    history = [Message("user", "olhe o projeto")]
    first = await p.complete("gemini-3.5-flash-lite", history, tools=tools)
    assert first.tool_calls == [ToolCall("function-call-1", "list_dir", {"path": "."})]
    assert first.tool_calls[0].extra == SIGNED
    assert "c2lnbmF0dXJl" not in repr(first.tool_calls[0])  # signatures stay out of logs
    history += [
        Message("assistant", "", tool_calls=first.tool_calls),
        Message("tool", "app/", tool_call_id="function-call-1"),
    ]
    await p.complete("gemini-3.5-flash-lite", history, tools=tools)
    (echoed,) = sent_tool_calls(route)
    assert echoed["extra_content"] == SIGNED and echoed["id"] == "function-call-1"
    assert route.call_count == 2  # no retry was needed


@respx.mock
async def test_signatures_are_never_sent_to_other_providers(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "k")
    p = OpenAICompatibleProvider("deepseek", default_config().providers["deepseek"])
    route = respx.post("https://api.deepseek.com/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}}
        )
    )
    signed = ToolCall("c1", "list_dir", {}, extra=SIGNED)  # made by Gemini, replayed elsewhere
    await p.complete(
        "deepseek-chat",
        [Message("user", "u"), Message("assistant", "", tool_calls=[signed])],
    )
    (sent,) = sent_tool_calls(route)
    assert (
        "extra_content" not in sent and b"thought_signature" not in route.calls[0].request.content
    )


@respx.mock
async def test_gemini_history_from_another_model_is_retried_once_with_the_documented_bypass(
    monkeypatch,
):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    p = OpenAICompatibleProvider("gemini", default_config().providers["gemini"])
    refusal = httpx.Response(
        400,
        text='{"error": {"code": 400, "message": "Function call is missing a thought_signature '
        'in functionCall parts."}}',
    )
    route = respx.post(GEMINI_URL).mock(side_effect=[refusal, gemini_reply(text="ok")])
    foreign = ToolCall("c1", "list_dir", {})  # a call another provider made earlier in the loop
    resp = await p.complete(
        "gemini-3.5-flash-lite",
        [
            Message("user", "u"),
            Message("assistant", "", tool_calls=[foreign]),
            Message("tool", "app/", tool_call_id="c1"),
        ],
    )
    assert resp.text == "ok" and route.call_count == 2
    assert "extra_content" not in sent_tool_calls(route, 0)[0]
    assert sent_tool_calls(route, 1)[0]["extra_content"] == {
        "google": {"thought_signature": "skip_thought_signature_validator"}
    }
    assert foreign.extra is None  # the shared history is not rewritten


@respx.mock
async def test_only_that_exact_refusal_is_retried(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    p = OpenAICompatibleProvider("gemini", default_config().providers["gemini"])
    unsigned = [Message("assistant", "", tool_calls=[ToolCall("c1", "list_dir", {})])]
    route = respx.post(GEMINI_URL).mock(return_value=httpx.Response(400, text="bad request"))
    with pytest.raises(LLMError, match="bad request"):
        await p.complete("gemini-3.5-flash-lite", [Message("user", "u"), *unsigned])
    assert route.call_count == 1  # some other 400: not our business
    respx.reset()
    route = respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(400, text="Function call is missing a thought_signature")
    )
    signed = [Message("assistant", "", tool_calls=[ToolCall("c1", "list_dir", {}, extra=SIGNED)])]
    with pytest.raises(LLMError, match="thought_signature"):
        await p.complete("gemini-3.5-flash-lite", [Message("user", "u"), *signed])
    assert route.call_count == 1  # everything was signed already: a retry cannot help


@respx.mock
async def test_an_unreadable_200_falls_through_instead_of_crashing_the_call(monkeypatch):
    """A 200 whose shape we cannot parse is provider trouble: it must reach the router as an
    LLMError (which falls through to the next candidate), never as a raw AttributeError."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    p = OpenAICompatibleProvider("gemini", default_config().providers["gemini"])
    respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(
            200,
            json={  # tool_calls nested one level too deep: `tc` is a list, so `tc.get` blows up
                "choices": [{"message": {"content": "", "tool_calls": [[{"function": {}}]]}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )
    )
    with pytest.raises(LLMError, match="formato inesperado") as err:
        await p.complete("gemini-3.5-flash-lite", [Message("user", "u")])
    assert err.value.retryable and "tool_calls" in str(err.value)  # the body aids the diagnosis
    await p.aclose()


@respx.mock
async def test_a_gemini_rate_limit_becomes_quota_exhausted_not_a_crash(monkeypatch):
    """Google's OpenAI-compatible endpoint returns a 429 whose body is a *list*
    (`[{"error": {...}}]`), not the object every other server sends. Reading the retry delay
    happens while `QuotaExhausted` is being built, so a crash there replaced the quota error
    and escaped the router's fall-through — a rate limit killed the whole turn."""
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    p = OpenAICompatibleProvider("gemini", default_config().providers["gemini"])
    respx.post(GEMINI_URL).mock(
        return_value=httpx.Response(
            429,
            json=[
                {
                    "error": {
                        "code": 429,
                        "status": "RESOURCE_EXHAUSTED",
                        "message": "Quota exceeded for quota metric 'Generate requests'.",
                        "details": [
                            {"@type": "type.googleapis.com/google.rpc.RetryInfo",
                             "retryDelay": "23s"},
                        ],
                    }
                }
            ],
        )
    )
    with pytest.raises(QuotaExhausted) as err:
        await p.complete("gemini-3.5-flash-lite", [Message("user", "u")])
    assert err.value.status == 429 and err.value.retry_after == 23.0
    await p.aclose()


def test_retry_delay_survives_any_error_body():
    """`_retry_after_seconds` runs inside a `raise` expression: it must never raise itself."""
    def resp(body: Any) -> httpx.Response:
        return httpx.Response(429, json=body)

    assert _retry_after_seconds(resp([{"error": {"details": [{"retryDelay": "5s"}]}}])) == 5.0
    assert _retry_after_seconds(resp({"error": {"message": "try again in 300ms"}})) == 0.3
    for hostile in ([], ["nonsense"], [[{"error": {}}]], {"error": []}, "text", 7, None):
        assert _retry_after_seconds(resp(hostile)) is None
    assert _retry_after_seconds(httpx.Response(429, text="<html>502</html>")) is None
    assert _retry_after_seconds(httpx.Response(429, headers={"retry-after": "12"}, json=[])) == 12.0


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
    bad = MockProvider("gemini", script=lambda m, msgs, t: QuotaExhausted("quota"))
    good = MockProvider("deepseek", script=scripted)
    router = ModelRouter(
        cfg,
        tracker=tracker,
        providers={"gemini": bad, "deepseek": good},
        on_call=lambda role, agent, rc: calls.append(rc.candidate.model),
    )
    rc = await router.complete(
        "worker", [Message("user", "hello world" * 100)], agent="worker-1", story_id="S-1"
    )
    assert (
        rc.candidate.provider == "deepseek"
        and rc.tier == "tier2"
        and rc.response.text == "deepseek-chat says hi"
    )
    assert calls == ["deepseek-chat"] and rc.cost_usd > 0
    assert (
        store.usage_totals("f")["calls"] == 1
        and store.usage_by("model", "f")[0]["key"] == "deepseek-chat"
    )
    # gemini flash-lite is now in cooldown: the second call skips it without invoking it again
    n = len(bad.calls)
    rc2 = await router.complete("architect", [Message("user", "x")], tier_override="tier2")
    assert rc2.candidate.provider == "deepseek" and len(bad.calls) == n
    # tier override to tier1 -> deepseek-reasoner heads that tier and answers
    rc3 = await router.complete("worker", [Message("user", "x")], tier_override="tier1")
    assert rc3.candidate.model == "deepseek-reasoner" and rc3.tier == "tier1"


async def test_router_all_fail_raises():
    cfg = default_config()
    cfg.models.tiers["tier2"] = [c for c in cfg.models.tiers["tier2"] if c.provider == "deepseek"][
        :1
    ]
    router = ModelRouter(
        cfg,
        providers={"deepseek": MockProvider("deepseek", script=lambda *a: LLMError("boom"))},
        max_retries=0,
    )
    with pytest.raises(LLMError, match="todos os modelos"):
        await router.complete("worker", [Message("user", "x")])


async def test_router_waits_for_single_candidate_cooldown():
    cfg = default_config()
    cfg.models.tiers["tier2"] = [c for c in cfg.models.tiers["tier2"] if c.provider == "deepseek"][
        :1
    ]
    calls = {"n": 0}

    def flaky(model, messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return QuotaExhausted("cota", status=429, retry_after=0.05)
        return "back"

    router = ModelRouter(
        cfg, providers={"deepseek": MockProvider("deepseek", script=flaky)}, max_retries=0
    )
    rc = await router.complete("worker", [Message("user", "x")])
    assert rc.response.text == "back" and rc.attempts == 2 and calls["n"] == 2
    # a cooldown longer than the wait budget is reported instead of awaited
    calls["n"] = 0
    router._cooldown.clear()
    router.max_cooldown_wait = 0.01

    def slow(model, messages, tools):
        return QuotaExhausted("cota", status=429, retry_after=5)

    router._providers["deepseek"] = MockProvider("deepseek", script=slow)
    with pytest.raises(LLMError, match="excede o limite de espera"):
        await router.complete("worker", [Message("user", "x")])


def test_retry_after_is_parsed_from_header_and_gemini_body():
    from loompa.llm.providers import _retry_after_seconds

    assert _retry_after_seconds(httpx.Response(429, headers={"retry-after": "7"})) == 7.0
    body = {"error": {"message": "quota", "details": [{"retryDelay": "23s"}]}}
    assert _retry_after_seconds(httpx.Response(429, json=body)) == 23.0
    body = {"error": {"message": "Rate limit reached. Please try again in 1.5s."}}
    assert _retry_after_seconds(httpx.Response(429, json=body)) == 1.5
    assert _retry_after_seconds(httpx.Response(429, text="nope")) is None


def test_extract_json():
    assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json('prefix {"a": [1, 2]} suffix') == {"a": [1, 2]}
    assert extract_json("[1, 2]") == [1, 2]
    with pytest.raises(ValueError):
        extract_json("nothing here")


@respx.mock
async def test_reasoning_effort_reaches_the_payload(monkeypatch):
    """A reasoning model spends its output budget on thinking *and* answering. Sending the
    effort is how a short task keeps room for the answer."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    p = OpenAICompatibleProvider("openrouter", default_config().providers["openrouter"])
    route = respx.post("https://openrouter.ai/api/v1/chat/completions").mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}}], "usage": {}}
        )
    )
    await p.complete("z-ai/glm-5.3", [Message("user", "u")], reasoning_effort="low")
    assert json.loads(route.calls[0].request.content)["reasoning_effort"] == "low"
    await p.complete("z-ai/glm-5.3", [Message("user", "u")])
    assert "reasoning_effort" not in json.loads(route.calls[1].request.content)
    await p.aclose()


async def test_the_candidate_sets_the_default_effort_and_the_call_overrides_it():
    cfg = default_config()
    cfg.models.tiers = {
        "tier2": [ModelCandidate(provider="mock", model="m", reasoning_effort="high")]
    }
    mock = MockProvider("mock", script=lambda m, msgs, t: "hi")
    router = ModelRouter(cfg, providers={"mock": mock})
    await router.complete("worker", [Message("user", "u")])
    assert mock.calls[-1]["reasoning_effort"] == "high"  # from the candidate
    await router.complete("worker", [Message("user", "u")], reasoning_effort="low")
    assert mock.calls[-1]["reasoning_effort"] == "low"  # the call wins, like max_tokens
