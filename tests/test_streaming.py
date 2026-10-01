"""ADR-0016 §4: OpenAI-compatible calls are streamed. The answer is assembled from its chunks,
the timeout is silence instead of duration, and a long call says it is still writing."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import httpx
import pytest
import respx

from loompa.config import default_config
from loompa.llm import LLMError, Message, ModelRouter, OpenAICompatibleProvider
from loompa.llm.providers import _StreamedAnswer

URL = "https://openrouter.ai/api/v1/chat/completions"
SSE = {"content-type": "text/event-stream"}


def sse(*chunks: dict | str) -> bytes:
    lines = []
    for c in chunks:
        lines.append(c if isinstance(c, str) else "data: " + json.dumps(c))
        lines.append("")
    return ("\n".join(lines) + "\n").encode()


def delta(**d) -> dict:
    return {"model": "z-ai/glm-5.3-flash", "choices": [{"index": 0, "delta": d}]}


ANSWER = sse(
    ": OPENROUTER PROCESSING",
    {**delta(reasoning="Let me "), "provider": "Together"},
    delta(reasoning="think."),
    delta(content="Lendo "),
    delta(
        tool_calls=[
            {
                "index": 0,
                "id": "call_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"pa'},
            }
        ]
    ),
    delta(tool_calls=[{"index": 0, "function": {"arguments": 'th": "a.py"}'}}]),
    {
        "model": "z-ai/glm-5.3-flash",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
    },
    {
        "model": "z-ai/glm-5.3-flash",
        "choices": [],
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 40,
            "cost": 0.0012,
            "completion_tokens_details": {"reasoning_tokens": 12},
        },
    },
    "data: [DONE]",
)


@pytest.fixture
def provider(monkeypatch) -> OpenAICompatibleProvider:
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    return OpenAICompatibleProvider("openrouter", default_config().providers["openrouter"])


@respx.mock
async def test_a_streamed_answer_is_assembled_from_its_chunks(provider):
    route = respx.post(URL).mock(return_value=httpx.Response(200, headers=SSE, content=ANSWER))
    seen: list[int] = []
    r = await provider.complete(
        "z-ai/glm-5.3-flash", [Message("user", "u")], on_progress=seen.append
    )
    body = json.loads(route.calls[0].request.content)
    assert body["stream"] is True and body["stream_options"] == {"include_usage": True}
    assert r.content == "Lendo " and r.finish_reason == "tool_calls"
    (call,) = r.tool_calls
    assert (call.id, call.name, call.arguments) == ("call_1", "read_file", {"path": "a.py"})
    assert r.served_by == "Together" and r.cost_usd == 0.0012  # the billed cost still arrives
    assert (r.input_tokens, r.output_tokens, r.reasoning_tokens) == (100, 40, 12)
    assert r.raw["choices"][0]["message"]["reasoning"] == "Let me think."  # for the cut tail
    # an estimate per chunk that carried tokens (characters / 4); keep-alives are not progress
    assert (
        len(seen) == 5
        and seen == sorted(seen)
        and seen[-1] == len("Let me think.Lendo " + '{"path": "a.py"}') // 4
    )
    await provider.aclose()


@respx.mock
async def test_a_server_that_answers_whole_is_still_read(provider):
    respx.post(URL).mock(
        return_value=httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
        )
    )
    r = await provider.complete("m", [Message("user", "u")])
    assert r.content == "ok" and r.finish_reason == "stop"
    await provider.aclose()


@respx.mock
async def test_an_error_in_the_middle_of_the_stream_is_an_llm_error(provider):
    body = sse(delta(content="me"), {"error": {"code": 502, "message": "upstream died"}})
    respx.post(URL).mock(return_value=httpx.Response(200, headers=SSE, content=body))
    with pytest.raises(LLMError, match="upstream died") as exc:
        await provider.complete("m", [Message("user", "u")])
    assert exc.value.retryable
    await provider.aclose()


@respx.mock
async def test_a_stream_that_only_keeps_alive_fails_on_silence(provider):
    """A server that keeps the connection open but writes nothing is not progress."""
    provider.idle_s = 0.05

    async def keep_alive() -> AsyncIterator[bytes]:
        yield sse(delta(content="a"))
        for _ in range(20):
            await asyncio.sleep(0.02)
            yield b": OPENROUTER PROCESSING\n\n"

    respx.post(URL).mock(return_value=httpx.Response(200, headers=SSE, content=keep_alive()))
    with pytest.raises(LLMError, match="nenhum token") as exc:
        await provider.complete("m", [Message("user", "u")])
    assert exc.value.retryable
    await provider.aclose()


@respx.mock
async def test_a_status_error_is_read_before_the_stream(provider):
    respx.post(URL).mock(return_value=httpx.Response(400, json={"error": {"message": "bad"}}))
    with pytest.raises(LLMError, match="bad") as exc:
        await provider.complete("m", [Message("user", "u")])
    assert exc.value.status == 400
    await provider.aclose()


def test_gemini_and_a_provider_turned_off_are_not_streamed(monkeypatch):
    cfg = default_config()
    assert not OpenAICompatibleProvider("gemini", cfg.providers["gemini"]).streams
    off = cfg.providers["openrouter"].model_copy(update={"stream": False})
    assert not OpenAICompatibleProvider("openrouter", off).streams
    assert OpenAICompatibleProvider("openrouter", cfg.providers["openrouter"]).streams


def test_a_tool_name_sent_whole_twice_is_not_doubled():
    a = _StreamedAnswer()
    for piece in ("read_file", "read_file"):
        a.feed("data: " + json.dumps(delta(tool_calls=[{"index": 0, "function": {"name": piece}}])))
    a.feed("data: " + json.dumps(delta(tool_calls=[{"index": 1, "function": {"name": "run_"}}])))
    a.feed("data: " + json.dumps(delta(tool_calls=[{"index": 1, "function": {"name": "tests"}}])))
    names = [c["function"]["name"] for c in a.as_response()["choices"][0]["message"]["tool_calls"]]
    assert names == ["read_file", "run_tests"]


@respx.mock
async def test_a_long_streamed_call_reports_progress_and_has_no_wall_clock_limit(monkeypatch):
    """The stall watchdog hears `llm.progress`, so a call still writing is never a hang; and
    `call_timeout_s` no longer ends a streamed call that keeps writing."""
    from loompa.llm import router as router_mod

    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setattr(router_mod, "PROGRESS_EVERY_S", 0.0)

    async def slow() -> AsyncIterator[bytes]:
        for word in ("um ", "dois ", "três"):
            await asyncio.sleep(0.03)
            yield sse(delta(content=word))
        yield sse(
            {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {}},
            "data: [DONE]",
        )

    respx.post(URL).mock(return_value=httpx.Response(200, headers=SSE, content=slow()))
    cfg = default_config()
    cfg.models.call_timeout_s = 10  # the floor; the call below takes ~0.1 s anyway
    events: list[tuple[str, dict]] = []
    router = ModelRouter(cfg, on_event=lambda t, **kw: events.append((t, kw)))
    rc = await router.complete("worker", [Message("user", "u")], story_id="S-1")
    assert rc.response.content == "um dois três"
    progress = [kw for t, kw in events if t == "llm.progress"]
    assert progress and progress[-1]["tokens"] == len("um dois três") // 4
    assert progress[0]["story_id"] == "S-1"
    await router.aclose()
