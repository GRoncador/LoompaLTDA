"""Provider adapters. One OpenAI-compatible HTTP adapter covers DeepSeek, Gemini (OpenAI
endpoint), OpenRouter, Groq, Ollama and vLLM; a native Anthropic adapter is optional."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable, Mapping
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

    @property
    def text(self) -> str:
        return self.content or ""


class LLMProvider:
    name: str = "base"

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


def _parse_openai_response(
    data: Any, model: str, provider: str, duration_ms: int
) -> LLMResponse:
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    usage = data.get("usage") or {}
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
    )


class OpenAICompatibleProvider(LLMProvider):
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
        self.base_url = cfg.base_url.rstrip("/")
        self.google = is_google_endpoint(self.base_url)
        self.api_key = resolve_key(cfg.api_key_env, secrets)
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owned = client is None

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
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        start = time.monotonic()
        try:
            resp = await self._client.post(
                f"{self.base_url}/chat/completions", json=payload, headers=headers
            )
        except httpx.HTTPError as exc:
            raise LLMError(
                f"{self.name}: falha de rede ({type(exc).__name__})", retryable=True
            ) from exc
        duration = int((time.monotonic() - start) * 1000)
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
        data = resp.json()
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
) -> LLMProvider:
    if cfg.kind == "anthropic":
        return AnthropicProvider(name, cfg, client=client, secrets=secrets)
    if cfg.kind == "mock":
        return MockProvider(name)
    return OpenAICompatibleProvider(name, cfg, client=client, secrets=secrets)


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
