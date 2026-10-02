"""Provider adapters. One OpenAI-compatible HTTP adapter covers DeepSeek, Gemini (OpenAI
endpoint), OpenRouter, Groq, Ollama and vLLM; a native Anthropic adapter is optional."""

from __future__ import annotations

import collections
import json
import os
import re
import time
import zlib
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

import httpx

from loompa.config.schema import ProviderConfig


def resolve_key(env_name: str, secrets: Mapping[str, str] | None = None) -> str:
    """Key value for `env_name`: secrets files first, process environment as fallback."""
    if not env_name:
        return ""
    if secrets is not None:
        val = secrets.get(env_name)
        if val:
            return val
    return os.environ.get(env_name, "")


class LLMError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


class LoopDetected(LLMError):
    """A streamed answer caught repeating itself (ADR-0016 §3): cut on strong evidence only.
    `partial` is what was written up to the cut, so the attempt is still metered and traced."""

    def __init__(self, message: str, *, partial: LLMResponse, sample: str):
        super().__init__(message, retryable=False)
        self.partial = partial
        self.sample = sample


# How each provider says "I do not have that model". A 404 is the documented answer, but the one
# that matters in practice is OpenRouter's, which returns 400 with a sentence. Checked against
# the real API: `{"error":{"message":"x/y is not a valid model ID","code":400}}`.
_NO_SUCH_MODEL = (
    "not a valid model",
    "no endpoints found",
    "model not found",
    "unknown model",
    "does not exist",
    "no such model",
    "invalid model",
)


def model_not_found(exc: LLMError) -> bool:
    """Whether the provider answered that this model id is not one of its own.

    Retrying cannot fix a name, so the router skips the candidate and the founder hears about it
    once (`loompa.models_sync.ModelWatch`)."""
    if exc.status == 404:
        return True
    text = str(exc).lower()
    return exc.status == 400 and any(phrase in text for phrase in _NO_SUCH_MODEL)


class QuotaExhausted(LLMError):
    """Rate limit / quota. `retry_after` (seconds) is the provider's own hint when it gave one."""

    def __init__(
        self, message: str, *, status: int | None = None, retry_after: float | None = None
    ):
        super().__init__(message, status=status, retryable=True)
        self.retry_after = retry_after


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    """Best-effort: `Retry-After` header, Gemini's `retryDelay: "23s"` detail, or an
    OpenAI-style "try again in 12.3s" message. None when the provider gave no hint.

    Never raises. It is evaluated while a `QuotaExhausted` is being built, so an exception
    here replaces the quota error with itself and escapes the router's fall-through: a rate
    limit then kills the call instead of pausing the model."""
    header = resp.headers.get("retry-after")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    try:
        body = resp.json()
    except ValueError:
        return None
    # Google's OpenAI-compatible endpoint wraps the error in a list: `[{"error": {...}}]`.
    if isinstance(body, list):
        body = next((item for item in body if isinstance(item, dict)), {})
    err = (body.get("error") or {}) if isinstance(body, dict) else {}
    if isinstance(err, dict):
        for detail in err.get("details") or []:
            delay = detail.get("retryDelay") if isinstance(detail, dict) else None
            if isinstance(delay, str) and delay.endswith("s"):
                try:
                    return float(delay[:-1])
                except ValueError:
                    pass
        m = re.search(r"try again in ([\d.]+)\s*(ms|s)", str(err.get("message", "")))
        if m:
            secs = float(m.group(1))
            return secs / 1000 if m.group(2) == "ms" else secs
    return None


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]
    # Provider metadata that must come back with the call. Gemini 3 attaches a thought signature
    # (`extra_content`) to every function call it makes and answers 400 if the next request does
    # not return it. Opaque to everyone but the adapter that produced it.
    extra: dict[str, Any] | None = field(default=None, repr=False, compare=False)


@dataclass
class Message:
    role: str  # system | user | assistant | tool
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_call_id: str | None = None
    name: str | None = None
    cache: bool = False  # stable prefix block: providers with explicit prompt caching mark it


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[ToolCall]
    model: str
    provider: str
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    finish_reason: str = ""
    duration_ms: int = 0
    raw: dict[str, Any] | None = None
    # What the provider says the call cost in USD, when it says it (OpenRouter's `usage.cost`);
    # None means the factory's price table decides.
    cost_usd: float | None = None
    served_by: str = ""  # who actually ran it behind a router (OpenRouter's `provider`)
    reasoning_tokens: int = 0  # part of `output_tokens` the model spent thinking

    @property
    def text(self) -> str:
        return self.content or ""

    @property
    def truncated(self) -> bool:
        """The model ran out of output budget mid-answer (OpenAI `length`, Anthropic
        `max_tokens`, Gemini `MAX_TOKENS`): the text or the tool call is incomplete."""
        return self.finish_reason.lower() in ("length", "max_tokens")


class LLMProvider:
    name: str = "base"
    # A streamed provider has no wall-clock limit: it fails when nothing arrives for a while,
    # and reports progress (`on_progress`) while it writes (ADR-0016 §4).
    streams: bool = False

    async def complete(
        self,
        model: str,
        messages: list[Message],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        json_mode: bool = False,
        reasoning_effort: str = "",
    ) -> LLMResponse:
        raise NotImplementedError

    async def aclose(self) -> None:
        return None


def _openai_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("parameters", {"type": "object", "properties": {}}),
            },
        }
        for t in tools
    ]


# Documented bypass for function calls Gemini did not make itself (a history that came from
# another model, or from a scripted provider).
SKIP_SIGNATURE_VALIDATION = "skip_thought_signature_validator"


def is_google_endpoint(base_url: str) -> bool:
    return "googleapis.com" in base_url


# The conversation a call belongs to (one tool loop). OpenRouter routes every call of a session to
# the provider that served the first one, so the provider's prompt cache keeps hitting; without
# it the key is a hash of the first two messages, which a rewritten opening would change.
_session: ContextVar[str] = ContextVar("loompa_llm_session", default="")


@contextmanager
def llm_session(key: str) -> Iterator[None]:
    """Calls made inside belong to the session `key` (at most 256 characters)."""
    token = _session.set(key[:256])
    try:
        yield
    finally:
        _session.reset(token)


def is_openrouter_endpoint(base_url: str) -> bool:
    return "openrouter.ai" in base_url


def _openai_tool_call(tc: ToolCall, *, google: bool, dummy_signature: bool) -> dict[str, Any]:
    call: dict[str, Any] = {
        "id": tc.id,
        "type": "function",
        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
    }
    # Other OpenAI-compatible servers may reject unknown message fields, so only Google gets it.
    extra = tc.extra
    if google and extra is None and dummy_signature:
        extra = {"google": {"thought_signature": SKIP_SIGNATURE_VALIDATION}}
    if google and extra:
        call["extra_content"] = extra
    return call


def _openai_messages(
    messages: list[Message], *, google: bool = False, dummy_signature: bool = False
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        if m.role == "tool":
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": m.content})
        elif m.role == "assistant" and m.tool_calls:
            out.append(
                {
                    "role": "assistant",
                    "content": m.content or None,
                    "tool_calls": [
                        _openai_tool_call(tc, google=google, dummy_signature=dummy_signature)
                        for tc in m.tool_calls
                    ],
                }
            )
        else:
            out.append({"role": m.role, "content": m.content})
    return out


def _parse_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw or "{}")
        return val if isinstance(val, dict) else {"value": val}
    except json.JSONDecodeError:
        return {"_raw": raw}


def _cached_tokens(usage: Mapping[str, Any]) -> int:
    """Cached prompt tokens. `prompt_tokens_details` is `{"cached_tokens": N}` on OpenAI-shaped
    servers and `prompt_cache_hit_tokens` on DeepSeek; anything else counts as zero."""
    details = usage.get("prompt_tokens_details")
    cached = (
        (details.get("cached_tokens") if isinstance(details, dict) else None)
        or usage.get("prompt_cache_hit_tokens")
        or 0
    )
    return int(cached or 0)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def reported_cost(usage: Mapping[str, Any]) -> float | None:
    """The call's cost as the provider billed it, or None when it does not say.

    OpenRouter puts it in `usage.cost` (USD) on every answer; it is the price of the provider
    that served the call, which the public catalogue does not tell (checked live on 2026-09-30:
    one model is served by 29 providers between US$ 0.04 and 0.66). With the founder's own key
    at the upstream provider (BYOK), `cost` is only OpenRouter's fee and the inference is billed
    upstream: `cost_details.upstream_inference_cost` is added."""
    cost = _number(usage.get("cost"))
    if cost is None:
        return None
    if usage.get("is_byok"):
        details = usage.get("cost_details")
        upstream = (
            _number(details.get("upstream_inference_cost")) if isinstance(details, dict) else None
        )
        cost += upstream or 0.0
    return max(cost, 0.0)


def _reasoning_tokens(usage: Mapping[str, Any]) -> int:
    details = usage.get("completion_tokens_details")
    value = details.get("reasoning_tokens") if isinstance(details, dict) else None
    return int(value) if isinstance(value, int) and not isinstance(value, bool) else 0


def _parse_openai_response(data: Any, model: str, provider: str, duration_ms: int) -> LLMResponse:
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = data.get("usage") or {}
    served_by = data.get("provider")
    tool_calls = [
        ToolCall(
            tc.get("id") or f"call_{i}",
            tc["function"]["name"],
            _parse_args(tc["function"].get("arguments")),
            extra=tc["extra_content"] if isinstance(tc.get("extra_content"), dict) else None,
        )
        for i, tc in enumerate(msg.get("tool_calls") or [])
        if tc.get("function")
    ]
    return LLMResponse(
        content=msg.get("content") or "",
        tool_calls=tool_calls,
        model=data.get("model") or model,
        provider=provider,
        input_tokens=int(usage.get("prompt_tokens") or 0),
        output_tokens=int(usage.get("completion_tokens") or 0),
        cached_tokens=_cached_tokens(usage),
        finish_reason=choice.get("finish_reason") or "",
        duration_ms=duration_ms,
        raw=data,
        cost_usd=reported_cost(usage),
        served_by=served_by if isinstance(served_by, str) else "",
        reasoning_tokens=_reasoning_tokens(usage),
    )


# The loop detector (ADR-0016 §3) cuts only on strong evidence: in the last LOOP_WINDOW characters
# of what the model writes, one sentence pattern (literal text, with code spans, numbers and quotes
# masked) making up most of the lines. Text with almost no sentences to count (a degenerate
# repetition of words) is judged by how well it compresses instead.
# Calibrated live (2026-10-01): real reasoning compressed ~2.7x with no pattern above 2 lines; the
# second smoke run's loop ("doesn't use `tibetan`. Good." for hundreds of scripts) had one pattern
# in 99% of its lines and compressed 38x. A compression rule for all text was tried and dropped the
# same day: it cut a converging plan at 17x (it had re-written the same code snippet a few times
# while deliberating). Deliberation is thinking; the output room bounds it.
# A loop can also be a cycle of a few sentences: `contas` Sprint 2's Product Owner wrote 96k tokens
# of 'Need maybe "description" for C1 "…". Good.' over three alternating lines (a period inside the
# quotes also splits one sentence in two), each ~33% of the window, so no single pattern passed
# the share. The rule counts every line whose pattern repeats LOOP_CYCLE_MIN times or more.
LOOP_WINDOW = 12000
LOOP_CHECK_EVERY = 4000
LOOP_MIN_REPEATS = 30
LOOP_MIN_SHARE = 0.5
LOOP_CYCLE_MIN = 8  # a pattern counts toward a cycle from this many repeats in the window
LOOP_FEW_SENTENCES = 5  # below this, the text has too few sentences for the pattern rule
LOOP_MIN_RATIO = 30.0
_SENTENCES = re.compile(r"[\n.!?]+")


def _pattern(sentence: str) -> str:
    s = re.sub(r"`[^`]*`", "`x`", sentence.lower())
    s = re.sub(r"\d+(?:\.\d+)?", "0", s)
    s = re.sub(r"'[^'\n]*'|\"[^\"\n]*\"", "'x'", s)
    return re.sub(r"\s+", " ", s).strip()


def _tail(parts: list[str], size: int = LOOP_WINDOW) -> str:
    """The last `size` characters of a list of stream deltas, without joining all of it."""
    out: list[str] = []
    n = 0
    for piece in reversed(parts):
        out.append(piece)
        n += len(piece)
        if n >= size:
            break
    return "".join(reversed(out))[-size:]


def repetition(text: str) -> str:
    """Why the end of `text` is a loop, or "" when it is not (see LOOP_* above)."""
    window = text[-LOOP_WINDOW:]
    if len(window) < LOOP_WINDOW // 2:
        return ""
    lines = [x for x in _SENTENCES.split(window) if len(x.strip()) >= 20]
    if len(lines) >= LOOP_FEW_SENTENCES:
        counts = collections.Counter(_pattern(x) for x in lines)
        pattern, n = counts.most_common(1)[0]
        if n >= LOOP_MIN_REPEATS and n / len(lines) >= LOOP_MIN_SHARE:
            return f"one sentence pattern {n} times in {len(lines)}: {pattern[:120]}"
        cycle = [(p, k) for p, k in counts.most_common() if k >= LOOP_CYCLE_MIN]
        looped = sum(k for _, k in cycle)
        if looped >= LOOP_MIN_REPEATS and looped / len(lines) >= LOOP_MIN_SHARE:
            return (
                f"a cycle of {len(cycle)} sentence patterns, {looped} of {len(lines)} lines: "
                f"{pattern[:120]}"
            )
        return ""
    raw = window.encode()
    ratio = len(raw) / max(1, len(zlib.compress(raw, 6)))
    if ratio >= LOOP_MIN_RATIO:
        return f"text with no sentences compresses {ratio:.0f}x: {window[-120:]}"
    return ""


class _StreamedAnswer:
    """An OpenAI-shaped answer assembled from its server-sent chunks: content, reasoning and
    tool-call deltas (merged by index), the finish reason, and the usage of the last chunk."""

    def __init__(self) -> None:
        self.content: list[str] = []
        self.reasoning: list[str] = []
        self.calls: dict[int, dict[str, Any]] = {}
        self.finish = ""
        self.usage: dict[str, Any] = {}
        self.model = ""
        self.provider = ""
        self.error: Any = None
        self.done = False
        # characters written so far (content, reasoning, tool arguments): a chunk carries several
        # tokens, so counting chunks read 7k for a call that wrote 18k (live, 2026-10-01)
        self.chars = 0
        self._checked = 0

    @property
    def tokens(self) -> int:
        """Roughly the tokens written so far (four characters each)."""
        return self.chars // 4

    def looping(self) -> str:
        """Every LOOP_CHECK_EVERY characters, whether the answer is repeating itself."""
        if self.chars - self._checked < LOOP_CHECK_EVERY:
            return ""
        self._checked = self.chars
        return repetition(_tail(self.reasoning)) or repetition(_tail(self.content))

    def feed(self, line: str) -> bool:
        """Read one line of the stream; True when it carried tokens (progress)."""
        line = line.strip()
        if not line.startswith("data:"):  # blank separators and ": keep-alive" comments
            return False
        data = line[5:].strip()
        if data == "[DONE]":
            self.done = True
            return False
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return False
        if not isinstance(chunk, dict):
            return False
        if chunk.get("error"):
            self.error, self.done = chunk["error"], True
            return False
        self.model = chunk.get("model") or self.model
        if isinstance(chunk.get("provider"), str):
            self.provider = chunk["provider"]
        if isinstance(chunk.get("usage"), dict):
            self.usage = chunk["usage"]
        got = False
        for choice in chunk.get("choices") or []:
            delta = choice.get("delta") or {}
            if delta.get("content"):
                self.content.append(delta["content"])
                self.chars += len(delta["content"])
                got = True
            thought = delta.get("reasoning") or delta.get("reasoning_content")
            if isinstance(thought, str) and thought:
                self.reasoning.append(thought)
                self.chars += len(thought)
                got = True
            for tc in delta.get("tool_calls") or []:
                got = self._merge_call(tc) or got
            if choice.get("finish_reason"):
                self.finish = choice["finish_reason"]
        return got

    def _merge_call(self, tc: dict[str, Any]) -> bool:
        cur = self.calls.setdefault(
            int(tc.get("index", len(self.calls))),
            {"id": None, "type": "function", "function": {"name": "", "arguments": ""}},
        )
        if tc.get("id"):
            cur["id"] = tc["id"]
        fn = tc.get("function") or {}
        name = fn.get("name") or ""
        if name:
            have = cur["function"]["name"]
            # most servers send the name once; one that repeats it whole must not double it
            cur["function"]["name"] = name if not have or name.startswith(have) else have + name
        if fn.get("arguments"):
            cur["function"]["arguments"] += fn["arguments"]
            self.chars += len(fn["arguments"])
        if isinstance(tc.get("extra_content"), dict):  # Gemini's thought signature
            cur["extra_content"] = tc["extra_content"]
        return bool(name or fn.get("arguments"))

    def as_response(self) -> dict[str, Any]:
        """The same shape a non-streamed call returns, so one parser reads both."""
        msg: dict[str, Any] = {"role": "assistant", "content": "".join(self.content) or None}
        if self.reasoning:
            msg["reasoning"] = "".join(self.reasoning)
        if self.calls:
            msg["tool_calls"] = [
                {**c, "id": c["id"] or f"call_{i}"} for i, c in sorted(self.calls.items())
            ]
        data: dict[str, Any] = {
            "model": self.model,
            "choices": [{"message": msg, "finish_reason": self.finish}],
            "usage": self.usage,
        }
        if self.provider:
            data["provider"] = self.provider
        return data


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        name: str,
        cfg: ProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 180.0,
        secrets: Mapping[str, str] | None = None,
        idle_s: float = 300.0,
        token_idle_s: float = 900.0,
    ):
        self.name = name
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")
        self.google = is_google_endpoint(self.base_url)
        self.api_key = resolve_key(cfg.api_key_env, secrets)
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owned = client is None
        self.idle_s = idle_s
        self.token_idle_s = token_idle_s

    @property
    def streams(self) -> bool:  # type: ignore[override]
        # Gemini's endpoint stays whole: its streamed thought signatures were never checked
        # against a real key, and a broken signature fails every tool call (ADR-0016 §4).
        return self.cfg.stream and not self.google

    def available(self) -> bool:
        return bool(self.api_key) or not self.cfg.api_key_env

    async def complete(
        self,
        model,
        messages,
        *,
        tools=None,
        temperature=0.2,
        max_tokens=4096,
        json_mode=False,
        reasoning_effort="",
        on_progress: Callable[[int], None] | None = None,
    ) -> LLMResponse:
        try:
            return await self._complete(
                model,
                messages,
                tools,
                temperature,
                max_tokens,
                json_mode,
                reasoning_effort,
                dummy_signature=False,
                on_progress=on_progress,
            )
        except LLMError as exc:
            # Gemini 3 refuses a function call in the history that it did not sign. That happens
            # when the loop switched providers halfway (the router fell through). Say once that
            # the signature check may be skipped for those calls; nothing changes otherwise.
            if not (
                self.google
                and exc.status == 400
                and "thought_signature" in str(exc)
                and any(tc.extra is None for m in messages for tc in m.tool_calls)
            ):
                raise
            return await self._complete(
                model,
                messages,
                tools,
                temperature,
                max_tokens,
                json_mode,
                reasoning_effort,
                dummy_signature=True,
                on_progress=on_progress,
            )

    async def _complete(
        self,
        model,
        messages,
        tools,
        temperature,
        max_tokens,
        json_mode,
        reasoning_effort,
        *,
        dummy_signature: bool,
        on_progress: Callable[[int], None] | None = None,
    ) -> LLMResponse:
        if not self.available():
            raise LLMError(f"chave de API ausente: defina {self.cfg.api_key_env}", retryable=True)
        payload: dict[str, Any] = {
            "model": model,
            "messages": _openai_messages(
                messages, google=self.google, dummy_signature=dummy_signature
            ),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = _openai_tools(tools)
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        if reasoning_effort:
            payload["reasoning_effort"] = reasoning_effort
        headers = {"Content-Type": "application/json", **self.cfg.extra_headers}
        if _session.get() and is_openrouter_endpoint(self.base_url):
            headers["x-session-id"] = _session.get()
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        start = time.monotonic()
        if self.streams:
            payload["stream"] = True
            payload["stream_options"] = {"include_usage": True}
            return await self._streamed(model, payload, headers, start, on_progress)
        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            raise LLMError(
                f"{self.name}: falha de rede ({type(exc).__name__})", retryable=True
            ) from exc
        duration = int((time.monotonic() - start) * 1000)
        self._raise_for_status(resp, model)
        return self._parse(resp.json(), model, duration)

    async def _streamed(
        self,
        model: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        start: float,
        on_progress: Callable[[int], None] | None,
    ) -> LLMResponse:
        """A streamed call: no wall-clock limit, only silence. No byte at all for `idle_s` (the
        read timeout: the connection is gone) or keep-alive comments without a token for
        `token_idle_s` (the server says it is working, but nothing comes) fail it as retryable.
        A server answering with plain JSON is read whole, as before."""
        idle, token_idle = self.idle_s, self.token_idle_s
        answer = _StreamedAnswer()
        try:
            async with self._client.stream(
                "POST",
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=headers,
                timeout=httpx.Timeout(connect=30.0, read=idle, write=60.0, pool=30.0),
            ) as resp:
                if resp.status_code >= 400:
                    await resp.aread()
                    self._raise_for_status(resp, model)
                if "text/event-stream" not in resp.headers.get("content-type", ""):
                    body = await resp.aread()
                    duration = int((time.monotonic() - start) * 1000)
                    return self._parse(json.loads(body or b"{}"), model, duration)
                last_token = time.monotonic()
                async for line in resp.aiter_lines():
                    now = time.monotonic()
                    if answer.feed(line):
                        last_token = now
                        if on_progress is not None:
                            on_progress(answer.tokens)
                        if why := answer.looping():
                            raise self._loop(model, answer, payload, start, why)
                    elif now - last_token > token_idle:
                        raise LLMError(
                            f"{self.name}/{model}: nenhum token por {token_idle:.0f}s",
                            retryable=True,
                        )
                    if answer.done:
                        break
        except httpx.TimeoutException as exc:
            raise LLMError(
                f"{self.name}/{model}: nada recebido por {idle:.0f}s", retryable=True
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMError(
                f"{self.name}: falha de rede ({type(exc).__name__})", retryable=True
            ) from exc
        if answer.error is not None:
            err = answer.error if isinstance(answer.error, dict) else {"message": answer.error}
            code = err.get("code")
            raise LLMError(
                f"{self.name}/{model}: erro no meio da resposta: {str(err.get('message'))[:200]}",
                status=code if isinstance(code, int) else None,
                retryable=True,
            )
        duration = int((time.monotonic() - start) * 1000)
        return self._parse(answer.as_response(), model, duration)

    def _loop(
        self, model: str, answer: _StreamedAnswer, payload: dict[str, Any], start: float, why: str
    ) -> LoopDetected:
        """What was written until the cut, metered on estimates: the stream ends before the
        usage chunk, and the provider bills the tokens anyway."""
        partial = self._parse(answer.as_response(), model, int((time.monotonic() - start) * 1000))
        partial.finish_reason = "loop"
        partial.output_tokens = partial.output_tokens or answer.tokens
        partial.reasoning_tokens = partial.reasoning_tokens or answer.tokens
        partial.input_tokens = partial.input_tokens or len(json.dumps(payload["messages"])) // 4
        return LoopDetected(
            f"{self.name}/{model}: raciocínio em loop, cortado ({why[:160]})",
            partial=partial,
            sample=why,
        )

    def _raise_for_status(self, resp: httpx.Response, model: str) -> None:
        if resp.status_code == 429 or resp.status_code in (402, 503):
            raise QuotaExhausted(
                f"{self.name}/{model}: cota/limite ({resp.status_code})",
                status=resp.status_code,
                retry_after=_retry_after_seconds(resp),
            )
        if resp.status_code >= 500:
            raise LLMError(
                f"{self.name}/{model}: erro do servidor ({resp.status_code})",
                status=resp.status_code,
                retryable=True,
            )
        if resp.status_code >= 400:
            raise LLMError(f"{self.name}/{model}: {resp.text[:300]}", status=resp.status_code)

    def _parse(self, data: Any, model: str, duration: int) -> LLMResponse:
        try:
            return _parse_openai_response(data, model, self.name, duration)
        except (AttributeError, TypeError, KeyError, IndexError) as exc:
            # A 200 whose shape we cannot read is provider trouble like any other: make it an
            # LLMError so the router falls through to the next candidate instead of letting a
            # raw AttributeError kill the call. The payload goes in the message: these are
            # provider quirks, and the body is the only way to see what changed.
            raise LLMError(
                f"{self.name}/{model}: resposta em formato inesperado "
                f"({type(exc).__name__}: {exc}); corpo: {json.dumps(data, ensure_ascii=False)[:400]}",
                retryable=True,
            ) from exc

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()


class AnthropicProvider(LLMProvider):
    def __init__(
        self,
        name: str,
        cfg: ProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 180.0,
        secrets: Mapping[str, str] | None = None,
    ):
        self.name = name
        self.cfg = cfg
        self.base_url = (cfg.base_url or "https://api.anthropic.com").rstrip("/")
        self.api_key = resolve_key(cfg.api_key_env or "ANTHROPIC_API_KEY", secrets)
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owned = client is None

    def available(self) -> bool:
        return bool(self.api_key)

    async def complete(
        self,
        model,
        messages,
        *,
        tools=None,
        temperature=0.2,
        max_tokens=4096,
        json_mode=False,
        reasoning_effort="",  # Anthropic expresses thinking as a budget, not an effort label
    ) -> LLMResponse:
        if not self.available():
            raise LLMError("chave de API ausente: defina ANTHROPIC_API_KEY", retryable=True)
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        conv: list[dict[str, Any]] = []
        for m in messages:
            if m.role == "system":
                continue
            if m.role == "tool":
                block = {"type": "tool_result", "tool_use_id": m.tool_call_id, "content": m.content}
                if conv and conv[-1]["role"] == "user" and isinstance(conv[-1]["content"], list):
                    conv[-1]["content"].append(block)
                else:
                    conv.append({"role": "user", "content": [block]})
            elif m.role == "assistant" and m.tool_calls:
                content: list[dict[str, Any]] = []
                if m.content:
                    content.append({"type": "text", "text": m.content})
                content += [
                    {"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments}
                    for tc in m.tool_calls
                ]
                conv.append({"role": "assistant", "content": content})
            else:
                conv.append({"role": m.role, "content": m.content})
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": conv,
        }
        if system:
            if any(m.role == "system" and m.cache for m in messages):
                payload["system"] = [
                    {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}
                ]
            else:
                payload["system"] = system
        if tools:
            payload["tools"] = [
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "input_schema": t.get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
            **self.cfg.extra_headers,
        }
        start = time.monotonic()
        try:
            resp = await self._client.post(
                f"{self.base_url}/v1/messages", json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            raise LLMError(
                f"{self.name}: falha de rede ({type(exc).__name__})", retryable=True
            ) from exc
        duration = int((time.monotonic() - start) * 1000)
        if resp.status_code in (429, 529):
            raise QuotaExhausted(
                f"{self.name}/{model}: limite ({resp.status_code})",
                status=resp.status_code,
                retry_after=_retry_after_seconds(resp),
            )
        if resp.status_code >= 500:
            raise LLMError(
                f"{self.name}/{model}: erro do servidor", status=resp.status_code, retryable=True
            )
        if resp.status_code >= 400:
            raise LLMError(f"{self.name}/{model}: {resp.text[:300]}", status=resp.status_code)
        data = resp.json()
        text_parts, tool_calls = [], []
        for block in data.get("content") or []:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append(
                    ToolCall(block.get("id", ""), block.get("name", ""), block.get("input") or {})
                )
        usage = data.get("usage") or {}
        cached = int(usage.get("cache_read_input_tokens") or 0)
        return LLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            model=data.get("model") or model,
            provider=self.name,
            input_tokens=int(usage.get("input_tokens") or 0) + cached,
            output_tokens=int(usage.get("output_tokens") or 0),
            cached_tokens=cached,
            finish_reason=data.get("stop_reason") or "",
            duration_ms=duration,
            raw=data,
        )

    async def aclose(self) -> None:
        if self._owned:
            await self._client.aclose()


class MockProvider(LLMProvider):
    """Scripted provider for tests and `loompa run --dry-run`.

    `script` is a callable receiving (model, messages, tools) and returning either a string,
    a list of ToolCall, or a full LLMResponse. Token counts are estimated from text length.
    """

    def __init__(
        self,
        name: str = "mock",
        script: Callable[[str, list[Message], list[dict[str, Any]] | None], Any] | None = None,
    ):
        self.name = name
        self.script = script
        self.calls: list[dict[str, Any]] = []

    async def complete(
        self,
        model,
        messages,
        *,
        tools=None,
        temperature=0.2,
        max_tokens=4096,
        json_mode=False,
        reasoning_effort="",
    ) -> LLMResponse:
        self.calls.append(
            {
                "model": model,
                "messages": messages,
                "tools": tools,
                "json_mode": json_mode,
                "reasoning_effort": reasoning_effort,
                "max_tokens": max_tokens,
            }
        )
        result = self.script(model, messages, tools) if self.script else "ok"
        if isinstance(result, LLMResponse):
            return result
        if isinstance(result, Exception):
            raise result
        prompt_chars = sum(len(m.content) for m in messages)
        if isinstance(result, list):
            return LLMResponse(
                content="",
                tool_calls=result,
                model=model,
                provider=self.name,
                input_tokens=prompt_chars // 4,
                output_tokens=40 * len(result),
                finish_reason="tool_calls",
            )
        return LLMResponse(
            content=str(result),
            tool_calls=[],
            model=model,
            provider=self.name,
            input_tokens=prompt_chars // 4,
            output_tokens=max(1, len(str(result)) // 4),
            finish_reason="stop",
        )


def build_provider(
    name: str,
    cfg: ProviderConfig,
    *,
    client: httpx.AsyncClient | None = None,
    secrets: Mapping[str, str] | None = None,
    idle_s: float = 300.0,
    token_idle_s: float = 900.0,
) -> LLMProvider:
    if cfg.kind == "anthropic":
        return AnthropicProvider(name, cfg, client=client, secrets=secrets)
    if cfg.kind == "mock":
        return MockProvider(name)
    return OpenAICompatibleProvider(
        name, cfg, client=client, secrets=secrets, idle_s=idle_s, token_idle_s=token_idle_s
    )


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", re.S)


def extract_json(text: str) -> Any:
    """Best-effort JSON extraction from a model reply (fenced or bare)."""
    text = (text or "").strip()
    m = _JSON_BLOCK.search(text)
    candidates = [m.group(1)] if m else []
    candidates.append(text)
    start = text.find("{")
    if start >= 0:
        candidates.append(text[start : text.rfind("}") + 1])
    start = text.find("[")
    if start >= 0:
        candidates.append(text[start : text.rfind("]") + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except json.JSONDecodeError:
            continue
    raise ValueError("resposta do modelo não contém JSON válido")
