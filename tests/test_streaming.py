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
    provider.token_idle_s = 0.05  # bytes keep coming: the connection is fine, the work is not

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


# ----------------------------------------------------------------- the loop detector (ADR-0016 §3)

LANGS = ["lao", "tibetan", "myanmar", "georgian", "armenian", "ethiopic", "cherokee", "khmer"]
LOOP = "".join(
    f"\nPotential issue: The implementation's `media` command doesn't use `{lang}`. Good.\n"
    for lang in LANGS * 30
)


def test_the_smoke_runs_loop_is_caught_and_real_reasoning_is_not():
    from pathlib import Path

    from loompa.llm.providers import repetition

    assert "sentence pattern" in repetition("Let me check the implementation.\n" + LOOP)
    # repetition with no sentence to count is caught by how well it compresses
    assert "compresses" in repetition("pensando de novo sobre o parser sem fim " * 400)
    # real prose of this repository, long and on one subject, is not a loop
    prose = (
        Path(__file__).parents[1] / "docs" / "adr" / "0015-trace-real-cost-and-stall-watchdog.md"
    )
    assert repetition(prose.read_text()) == ""
    assert repetition(LOOP[:3000]) == ""  # too little text to call it a loop
    # contas Sprint 2: the Product Owner cycled over three sentences for 96k tokens; a period
    # inside the quotes splits each in two, so no single pattern reached half the lines
    cycle = "".join(
        f'Need maybe "description" for C1 "{d}." Good.\n\n'
        for d in [
            "A normalização fica isolada para ser usada pelos leitores de CSV e OFX",
            "Não altera os helpers atuais parse_valor/parse_data nem o modelo",
            "Converte a data para AAAA-MM-DD e garante valor positivo",
        ]
        * 40
    )
    assert "a cycle of 3 sentence patterns" in repetition("Let me write the cards.\n" + cycle)
    # the false positive of the calibration run: a converging plan that deliberated, re-writing
    # the same code snippet a few times between varied ideas, compressed 17x and was cut
    snippet = (
        "```python\n@app.command(context_settings={'ignore_unknown_options': True})\n"
        "def converter(valor: str, unidade: str) -> None:\n    ...\n```\n"
    )
    ideas = [
        "OK, executive decision: I'll treat this as a risk and let the Worker verify it early.",
        "But better: design it so it works regardless of how the parser splits the tokens.",
        "Idea: use a custom parser on the argument? No, that does not change tokenization.",
        "Idea: the context has allow_interspersed_args, but that is about order, not dashes.",
        "Idea: an app-level setting on the Typer object; I do not think there is a relevant one.",
        "Hmm, actually, the safest route is a test that calls the command with -40 exactly.",
        "If that test fails, the Worker adds ignore_unknown_options and checks again.",
        "The spec asks for one decimal place, so the format string is the easy part here.",
        "Rounding 37.77 gives 37.8 with the default formatting, which matches criterion four.",
        "What about lowercase units? Normalising with upper() covers criterion seven.",
        "An invalid unit must exit with a non-zero code and print nothing converted.",
        "A non-numeric value fails the float conversion; catching it gives a clear message.",
        "Without arguments, Click's missing-argument error already exits with code two.",
        "So the plan has three tasks: the pure functions, the command, and the error paths.",
    ]
    deliberation = (
        "".join(idea + "\n" + (snippet if i % 4 == 0 else "") for i, idea in enumerate(ideas)) * 6
    )  # the same thoughts revisited a few times, as a long deliberation does
    assert repetition(deliberation) == ""


@respx.mock
async def test_a_streamed_answer_that_loops_is_cut_and_still_metered(provider):
    from loompa.llm.providers import LoopDetected

    chunks = [delta(reasoning=line + "\n") for line in LOOP.strip().splitlines() if line]
    respx.post(URL).mock(return_value=httpx.Response(200, headers=SSE, content=sse(*chunks)))
    with pytest.raises(LoopDetected) as exc:
        await provider.complete("m", [Message("user", "julgue a entrega")])
    partial = exc.value.partial
    assert partial.finish_reason == "loop" and partial.output_tokens > 1000
    assert partial.input_tokens > 0 and "sentence pattern" in exc.value.sample
    assert not exc.value.retryable
    await provider.aclose()


async def test_the_router_traces_a_loop_and_tries_the_next_model(tmp_path):
    from loompa.config.schema import ModelCandidate
    from loompa.finance import CostTracker
    from loompa.llm import MockProvider
    from loompa.llm.providers import LLMResponse, LoopDetected
    from loompa.store import Store
    from loompa.trace import Tracer, read_trace

    cfg = default_config()
    cfg.models.tiers = {
        "tier2": [
            ModelCandidate(provider="a", model="looper"),
            ModelCandidate(provider="b", model="ok"),
        ]
    }
    cfg.models.matrix = {}

    class Looper(MockProvider):
        async def complete(self, model, messages, **kw):  # type: ignore[override]
            partial = LLMResponse("", [], model, "a", 900, 30000, finish_reason="loop")
            partial.raw = {"choices": [{"message": {"reasoning": LOOP}}]}
            raise LoopDetected(
                "a/looper: raciocínio em loop, cortado (x)", partial=partial, sample="x"
            )

    events: list[tuple[str, dict]] = []
    store = Store(":memory:")
    router = ModelRouter(
        cfg,
        tracker=CostTracker(store, cfg, "f"),
        providers={"a": Looper("a"), "b": MockProvider("b", script=lambda *a: "veredito")},
        tracer=Tracer(tmp_path),
        on_event=lambda t, **kw: events.append((t, kw)),
    )
    rc = await router.complete("inspector", [Message("user", "u")], story_id="S-1")
    assert rc.response.text == "veredito" and rc.candidate.model == "ok"
    assert [t for t, _ in events] == ["llm.loop", "llm.fallthrough", "llm.rested"]
    assert events[1][1]["reason"] == "loop"
    (span,) = [s for s in read_trace(tmp_path / "S-1.jsonl").spans if s.get("kind") == "llm"]
    first = span["attrs"]["attempts"][0]
    assert first["outcome"] == "loop" and first["output_tokens"] == 30000
    assert "doesn't use" in first["reasoning_tail"]
    assert store.usage_totals("f")["calls"] == 2  # the cut attempt was metered too


def test_ops_tells_the_founder_a_loop_plainly_and_tries_again_at_once():
    from loompa.agents.ops import triage
    from loompa.comms import audit_executive_text

    t = triage(LLMError("todos os modelos do tier falharam: a/x: raciocínio em loop, cortado (y)"))
    assert not t.transient and "repetitivo" in t.cause and audit_executive_text(t.cause) == []
