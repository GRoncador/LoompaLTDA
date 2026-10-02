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
    model_not_found,
)
from loompa.llm.providers import (
    AnthropicProvider,
    LLMResponse,
    _retry_after_seconds,
    extract_json,
)
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
                            {
                                "@type": "type.googleapis.com/google.rpc.RetryInfo",
                                "retryDelay": "23s",
                            },
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


def two_provider_matrix():
    """A factory whose tiers name two providers, so a fall-through can be observed. Written here
    instead of leaning on the shipped defaults: what a new factory ships with is a product choice
    and must be free to change without breaking the router's tests."""
    from loompa.config.schema import ALL_CLUSTERS, ModelCandidate

    cfg = default_config()
    tiers = {
        "tier1": [ModelCandidate(provider="deepseek", model="deepseek-reasoner")],
        "tier2": [
            ModelCandidate(provider="gemini", model="gemini-3.5-flash-lite"),
            ModelCandidate(provider="deepseek", model="deepseek-chat"),
        ],
        "tier3": [ModelCandidate(provider="deepseek", model="deepseek-chat")],
    }
    cfg.models.tiers = {t: list(cs) for t, cs in tiers.items()}
    cfg.models.matrix = {c: {t: list(cs) for t, cs in tiers.items()} for c in ALL_CLUSTERS}
    return cfg


def single_provider_config():
    cfg = two_provider_matrix()
    cfg.models.tiers["tier2"] = [c for c in cfg.models.tiers["tier2"] if c.provider == "deepseek"][
        :1
    ]
    return cfg


async def test_router_falls_through_and_meters_cost():
    cfg = two_provider_matrix()
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
    cfg = single_provider_config()
    router = ModelRouter(
        cfg,
        providers={"deepseek": MockProvider("deepseek", script=lambda *a: LLMError("boom"))},
        max_retries=0,
    )
    with pytest.raises(LLMError, match="todos os modelos"):
        await router.complete("worker", [Message("user", "x")])


async def test_router_waits_for_single_candidate_cooldown():
    cfg = single_provider_config()
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


def test_a_model_the_provider_does_not_have_is_told_apart_from_other_errors():
    """OpenRouter answers 400 with a sentence, not 404, so a status check alone misses it."""
    assert model_not_found(LLMError("x/y is not a valid model ID", status=400))
    assert model_not_found(LLMError("No endpoints found for a/b", status=400))
    assert model_not_found(LLMError("modelo sumiu", status=404))
    assert not model_not_found(LLMError("invalid api key", status=400))
    assert not model_not_found(LLMError("overloaded", status=503))


async def test_a_call_that_never_ends_gives_way_to_the_next_model():
    """The HTTP timeout is per read; a server that keeps a request alive never trips it. A call
    now has a wall-clock limit and the next model gets the turn."""
    import asyncio

    class Hung(MockProvider):
        async def complete(self, model, messages, **kw):
            await super().complete(model, messages, **kw)
            await asyncio.sleep(3600)

    cfg = two_provider_matrix()
    cfg.models.call_timeout_s = 0.05
    hung = Hung("gemini")
    router = ModelRouter(
        cfg, providers={"gemini": hung, "deepseek": MockProvider("deepseek", script=scripted)}
    )
    rc = await router.complete("worker", [Message("user", "x")])
    assert rc.candidate.provider == "deepseek" and len(hung.calls) == 1
    await router.complete("worker", [Message("user", "y")])
    assert len(hung.calls) == 1  # rested, not asked again right away


async def test_the_router_reports_a_missing_model_once_and_falls_through():
    """A wrong name cannot be fixed by retrying: the candidate is skipped and said out loud."""
    cfg = two_provider_matrix()
    gone: list[tuple[str, str]] = []
    bad = MockProvider(
        "gemini",
        script=lambda *a: LLMError("gemini-3.5-flash-lite is not a valid model ID", status=400),
    )
    router = ModelRouter(
        cfg,
        providers={"gemini": bad, "deepseek": MockProvider("deepseek", script=scripted)},
        on_model_gone=lambda cand, detail: gone.append((cand.provider, cand.model)),
        max_retries=2,
    )
    rc = await router.complete("worker", [Message("user", "x")])
    assert rc.candidate.provider == "deepseek"  # fell through to the next candidate
    assert gone == [("gemini", "gemini-3.5-flash-lite")]  # said once, not once per retry
    assert len(bad.calls) == 1  # and not retried: a name does not get better on the second try


def _cut_until(budget_needed: int, provider: str = "deepseek"):
    """A model that needs `budget_needed` output tokens: below that it stops at the limit."""

    class Needy(MockProvider):
        async def complete(self, model, messages, **kw):
            await super().complete(model, messages, **kw)
            done = kw["max_tokens"] >= budget_needed
            return LLMResponse(
                content='{"ok": true}' if done else '{"plan": "meio',
                tool_calls=[],
                model=model,
                provider=provider,
                input_tokens=100,
                output_tokens=min(kw["max_tokens"], budget_needed),
                finish_reason="stop" if done else "length",
            )

    return Needy(provider)


async def test_the_output_room_is_a_backstop_set_by_the_effort():
    """ADR-0016: a call pays only for what it writes, so the room only decides when it is cut.
    The smoke run's plan needed ~20k tokens and was cut at 4k, 8k and 16k."""
    cfg = single_provider_config()
    prov = MockProvider("deepseek", script=scripted)
    router = ModelRouter(cfg, providers={"deepseek": prov})
    for complexity in ("SIMPLE", "STANDARD", "COMPLEX"):  # the story's size no longer sizes it
        await router.complete("architect", [Message("user", "x")], complexity=complexity)
        assert prov.calls[-1]["max_tokens"] == 96000, complexity
    await router.complete(
        "deployer", [Message("user", "x")], max_tokens=400, reasoning_effort="low"
    )
    assert prov.calls[-1]["max_tokens"] == 16384  # an old small cap is a floor, not the room
    await router.complete(
        "worker", [Message("user", "x")], max_tokens=40000, reasoning_effort="low"
    )
    assert prov.calls[-1]["max_tokens"] == 40000  # a call that writes whole files asks for more
    capped = ModelCandidate(provider="deepseek", model="deepseek-chat", max_output_tokens=32768)
    for cluster in cfg.models.matrix:
        cfg.models.matrix[cluster]["tier2"] = [capped]
    await router.complete("worker", [Message("user", "x")])
    assert prov.calls[-1]["max_tokens"] == 32768  # the founder's cap for a model
    for cluster in cfg.models.matrix:
        cfg.models.matrix[cluster]["tier2"] = [
            capped.model_copy(update={"max_output_tokens": None})
        ]
    cfg.models.max_output_ceiling = 5000
    await router.complete("worker", [Message("user", "x")])
    assert prov.calls[-1]["max_tokens"] == 5000  # never past the ceiling


def _small_rooms(cfg, full: int = 4096, ceiling: int = 32768):
    cfg.models.full_output_tokens = full
    cfg.models.light_output_tokens = full
    cfg.models.max_output_ceiling = ceiling
    return cfg


async def test_a_cut_answer_is_retried_with_more_room_and_every_call_is_metered():
    """S-002 in `contas`: the Architect stopped at exactly 4096 tokens twice and the story
    died as 'not valid JSON'. A cut answer is retried with twice the room."""
    cfg = _small_rooms(single_provider_config())
    store = Store(":memory:")
    prov = _cut_until(9000)
    router = ModelRouter(cfg, tracker=CostTracker(store, cfg, "f"), providers={"deepseek": prov})
    rc = await router.complete(
        "architect", [Message("user", "x")], story_id="S-2", complexity="SIMPLE"
    )
    assert rc.response.text == '{"ok": true}' and not rc.response.truncated
    assert [c["max_tokens"] for c in prov.calls] == [4096, 8192, 16384]
    assert store.usage_totals("f")["calls"] == 3  # the cut answers were paid for too


async def test_the_budget_that_fitted_after_a_cut_is_where_the_next_call_starts():
    """`contas`, 2026-09-30: the Worker's self-check was cut on every task and only fitted at
    twice or four times the room, paying for one or two discarded calls each time."""
    cfg = _small_rooms(single_provider_config(), full=900)
    prov = _cut_until(3000)
    router = ModelRouter(cfg, providers={"deepseek": prov})
    await router.complete("worker", [Message("user", "x")], complexity="SIMPLE")
    assert [c["max_tokens"] for c in prov.calls] == [900, 1800, 3600]
    await router.complete("worker", [Message("user", "y")], complexity="SIMPLE")
    assert prov.calls[-1]["max_tokens"] == 3600 and len(prov.calls) == 4
    await router.complete("architect", [Message("user", "z")], complexity="SIMPLE")
    assert prov.calls[4]["max_tokens"] == 900  # another role learns on its own


class _Overthinker(MockProvider):
    """Thinks through any room it is given (the smoke run's Architect, 2026-09-30)."""

    async def complete(self, model, messages, **kw):  # type: ignore[override]
        self.calls.append({"model": model, **kw})
        return LLMResponse(
            content="",
            tool_calls=[],
            model=model,
            provider=self.name,
            input_tokens=100,
            output_tokens=kw["max_tokens"],
            reasoning_tokens=kw["max_tokens"],
            finish_reason="length",
        )


async def test_a_cut_never_lowers_the_effort_and_gives_up_plainly_at_the_ceiling():
    """ADR-0016: the effort was chosen for what the call decides; a cut gets room, never less
    thinking. At the ceiling the call falls through, and the founder hears it plainly."""
    cfg = _small_rooms(single_provider_config(), full=1000, ceiling=4000)
    prov = _Overthinker("deepseek")
    events: list[tuple[str, dict]] = []
    router = ModelRouter(
        cfg, providers={"deepseek": prov}, on_event=lambda t, **kw: events.append((t, kw))
    )
    with pytest.raises(LLMError, match="resposta cortada") as exc:
        await router.complete("architect", [Message("user", "x")], complexity="SIMPLE")
    assert exc.value.retryable is False  # waiting would not make the answer fit
    assert [(c["max_tokens"], c["reasoning_effort"]) for c in prov.calls] == [
        (1000, ""),
        (2000, ""),
        (4000, ""),
    ]
    assert [t for t, _ in events] == ["llm.cut", "llm.cut", "llm.fallthrough", "llm.rested"]


async def test_a_low_call_stays_low_when_it_is_cut():
    cfg = _small_rooms(single_provider_config(), full=1000, ceiling=2000)
    prov = _Overthinker("deepseek")
    router = ModelRouter(cfg, providers={"deepseek": prov})
    with pytest.raises(LLMError):
        await router.complete("worker", [Message("user", "x")], reasoning_effort="low")
    assert {c["reasoning_effort"] for c in prov.calls} == {"low"}


def test_a_lifted_call_goes_one_tier_above_and_tier_one_stays():
    cfg = two_provider_matrix()
    router = ModelRouter(cfg, providers={})
    assert router.candidates("worker", lift=True)[0] == "tier1"
    assert router.candidates("worker", tier_override="tier1", lift=True)[0] == "tier1"
    assert router.candidates("worker", tier_override="tier3", lift=True)[0] == "tier2"
    assert router.candidates("worker")[0] == "tier2"


def test_ops_does_not_wait_out_a_cut_answer_and_says_why_in_plain_words():
    from loompa.agents.ops import triage
    from loompa.comms import audit_executive_text

    t = triage(LLMError("resposta cortada: deepseek/x: 32768 tokens (resposta cortada no limite)"))
    assert not t.transient and "tamanho máximo" in t.cause
    assert audit_executive_text(t.cause) == []


async def test_a_retried_error_is_an_event_not_a_silent_restart():
    """contas Sprint 2: a stream dropped after 15 min of the Product Owner's reasoning restarted
    from zero on the same model, and nothing recorded why."""
    cfg = single_provider_config()
    seen: list[tuple[str, dict]] = []
    flaky = iter([LLMError("deepseek: conexão encerrada no meio da resposta", retryable=True)])

    def script(*_a):
        err = next(flaky, None)
        if err is not None:
            raise err
        return '{"ok": true}'

    router = ModelRouter(
        cfg,
        providers={"deepseek": MockProvider("deepseek", script=script)},
        on_event=lambda t, **kw: seen.append((t, kw)),
    )
    rc = await router.complete("product_owner", [Message("user", "x")], story_id="S-44")
    assert rc.response.text == '{"ok": true}'
    (retry,) = [kw for t, kw in seen if t == "llm.retry"]
    assert retry["story_id"] == "S-44" and "encerrada" in retry["error"] and "lost_s" in retry
    await router.aclose()


async def test_a_candidate_that_ran_away_for_a_role_goes_last_for_that_role():
    """contas Sprint 2: the Product Owner's first candidate thought to the ceiling on most calls
    (one spec review took 44 min) while the next one in the tier answered in about a minute."""
    cfg = _small_rooms(two_provider_matrix(), full=1000, ceiling=2000)
    runaway = _Overthinker("gemini")
    good = MockProvider("deepseek", script=lambda *a: '{"ok": true}')
    events: list[tuple[str, dict]] = []
    router = ModelRouter(
        cfg,
        providers={"gemini": runaway, "deepseek": good},
        on_event=lambda t, **kw: events.append((t, kw)),
    )
    await router.complete("product_owner", [Message("user", "a")])
    tried = len(runaway.calls)
    assert tried and [kw["role"] for t, kw in events if t == "llm.rested"] == ["product_owner"]
    rc = await router.complete("product_owner", [Message("user", "b")])
    assert rc.candidate.provider == "deepseek" and len(runaway.calls) == tried  # tried last now
    await router.complete("worker", [Message("user", "c")])
    assert len(runaway.calls) > tried  # another role keeps the tier's order
