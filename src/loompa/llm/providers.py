"""Provider adapters. One OpenAI-compatible HTTP adapter covers DeepSeek, Gemini (OpenAI
endpoint), OpenRouter, Groq, Ollama and vLLM; a native Anthropic adapter is optional."""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from loompa.config.schema import ProviderConfig


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
    OpenAI-style "try again in 12.3s" message. None when the provider gave no hint."""
    header = resp.headers.get("retry-after")
    if header:
        try:
            return max(0.0, float(header))
        except ValueError:
            pass
    try:
        err = resp.json().get("error") or {}
    except ValueError:
        return None
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


def _openai_messages(messages: list[Message]) -> list[dict[str, Any]]:
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
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
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


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        name: str,
        cfg: ProviderConfig,
        *,
        client: httpx.AsyncClient | None = None,
        timeout: float = 180.0,
    ):
        self.name = name
        self.cfg = cfg
        self.base_url = cfg.base_url.rstrip("/")
        self.api_key = os.environ.get(cfg.api_key_env, "") if cfg.api_key_env else ""
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owned = client is None

    def available(self) -> bool:
        return bool(self.api_key) or not self.cfg.api_key_env

    async def complete(
        self, model, messages, *, tools=None, temperature=0.2, max_tokens=4096, json_mode=False
    ) -> LLMResponse:
        if not self.available():
            raise LLMError(f"chave de API ausente: defina {self.cfg.api_key_env}", retryable=True)
        payload: dict[str, Any] = {
            "model": model,
            "messages": _openai_messages(messages),
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = _openai_tools(tools)
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
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
        choice = (data.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        usage = data.get("usage") or {}
        cached = (
            ((usage.get("prompt_tokens_details") or {}).get("cached_tokens"))
            or usage.get("prompt_cache_hit_tokens")
            or 0
        )
        tool_calls = [
            ToolCall(
                tc.get("id") or f"call_{i}",
                tc["function"]["name"],
                _parse_args(tc["function"].get("arguments")),
            )
            for i, tc in enumerate(msg.get("tool_calls") or [])
            if tc.get("function")
        ]
        return LLMResponse(
            content=msg.get("content") or "",
            tool_calls=tool_calls,
            model=data.get("model") or model,
            provider=self.name,
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            cached_tokens=int(cached or 0),
            finish_reason=choice.get("finish_reason") or "",
            duration_ms=duration,
            raw=data,
        )

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
    ):
        self.name = name
        self.cfg = cfg
        self.base_url = (cfg.base_url or "https://api.anthropic.com").rstrip("/")
        self.api_key = os.environ.get(cfg.api_key_env or "ANTHROPIC_API_KEY", "")
        self._client = client or httpx.AsyncClient(timeout=timeout)
        self._owned = client is None

    def available(self) -> bool:
        return bool(self.api_key)

    async def complete(
        self, model, messages, *, tools=None, temperature=0.2, max_tokens=4096, json_mode=False
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
        self, model, messages, *, tools=None, temperature=0.2, max_tokens=4096, json_mode=False
    ) -> LLMResponse:
        self.calls.append(
            {"model": model, "messages": messages, "tools": tools, "json_mode": json_mode}
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
    name: str, cfg: ProviderConfig, *, client: httpx.AsyncClient | None = None
) -> LLMProvider:
    if cfg.kind == "anthropic":
        return AnthropicProvider(name, cfg, client=client)
    if cfg.kind == "mock":
        return MockProvider(name)
    return OpenAICompatibleProvider(name, cfg, client=client)


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
