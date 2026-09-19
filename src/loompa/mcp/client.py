"""Thin MCP client on the official SDK (ADR-0009).

`McpHub` knows the factory's MCP servers (`tools.tavily`, `tools.mcp.*`) and opens them for one
agent run: `async with hub.session("analyst") as web:` connects to every enabled server that role
may use, lists its tools and translates them into the `{name, description, parameters}` shape
the model router already converts for every provider. A server that cannot be reached (no key,
offline, refused) is *recorded* in `session.unavailable`, never raised: the agent declares the
limitation instead of crashing the story.

Connections live exactly as long as the `async with` block, in the task that opened it, which is
what the SDK's task groups require. Keys are read from the secrets at connect time and go only
to the transport (header, URL parameter or child-process environment); they are never stored on
the objects here and are redacted from every error text.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterable, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from loompa.aci.tools import TOOL_SPECS, ToolResult
from loompa.config.schema import LoompaConfig, McpServerConfig
from loompa.config.secrets import redact
from loompa.llm.probe import ProbeResult
from loompa.llm.providers import resolve_key

CONNECT_TIMEOUT_S = 30.0
MAX_RESULT_CHARS = 12_000
MAX_LIST_PAGES = 5
_LOCAL_TOOL_NAMES = frozenset(t["name"] for t in TOOL_SPECS)

# (stack, server name, config, key) -> anything `mcp.Client` accepts as a server. Tests inject
# an in-process server here; production builds the HTTP or stdio transport.
Connector = Callable[[AsyncExitStack, str, McpServerConfig, str], Awaitable[Any]]


@dataclass(frozen=True)
class McpTool:
    name: str  # what the model sees: unique and provider-safe
    server: str
    remote_name: str  # what the server calls it
    description: str
    parameters: dict[str, Any]


class McpSession:
    """The live connections of one agent run and the tools they expose."""

    def __init__(self, redactor: Callable[[str], str] = lambda s: s):
        self.tools: dict[str, McpTool] = {}
        self.unavailable: dict[str, str] = {}  # server -> why it could not be used
        self.calls_ok = 0
        self.calls_failed = 0
        self._clients: dict[str, Any] = {}
        self._timeouts: dict[str, float] = {}
        self._redact = redactor

    @property
    def available(self) -> bool:
        return bool(self.tools)

    def specs(self) -> list[dict[str, Any]]:
        return [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in self.tools.values()
        ]

    def owns(self, name: str) -> bool:
        return name in self.tools

    def describe(self) -> str:
        """One line for the agent's prompt about what the web tools can and cannot do."""
        if self.tools:
            return "web tools available: " + ", ".join(sorted(self.tools))
        why = "; ".join(f"{s}: {r}" for s, r in self.unavailable.items()) or "none configured"
        return f"web tools NOT available ({why})"

    async def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self.tools.get(name)
        if tool is None:
            return ToolResult(False, f"erro: ferramenta desconhecida: {name}")
        timeout = self._timeouts.get(tool.server, 60.0)
        try:
            async with asyncio.timeout(timeout + 5):  # backstop over the SDK's own read timeout
                res = await self._clients[tool.server].call_tool(tool.remote_name, args)
        except TimeoutError:
            self.calls_failed += 1
            return ToolResult(False, f"erro: {tool.server} demorou mais de {timeout:.0f}s")
        except Exception as exc:  # noqa: BLE001 - a failing tool is information for the model
            self.calls_failed += 1
            return ToolResult(False, f"erro: {tool.server} falhou ({self._redact(_brief(exc))})")
        text = self._redact(render_result(res))  # a server may echo the key it was sent
        ok = not getattr(res, "is_error", False)
        if ok:
            self.calls_ok += 1
        else:
            self.calls_failed += 1
        body = (
            f'<external_data source="{tool.server}/{tool.remote_name}">\n{text}\n</external_data>'
        )
        return ToolResult(ok, body[:MAX_RESULT_CHARS])


class McpHub:
    def __init__(
        self,
        config: LoompaConfig,
        secrets: Mapping[str, str] | None = None,
        *,
        connector: Connector | None = None,
    ):
        self.config = config  # the live object: settings changes apply on the next session
        self.secrets = secrets
        self._connector = connector or default_connector

    # ---------------------------------------------------------------- selection
    def servers_for(self, role: str) -> dict[str, McpServerConfig]:
        return {
            name: spec
            for name, spec in self.config.tools.servers().items()
            if spec.enabled and role in spec.roles
        }

    def _key(self, spec: McpServerConfig) -> str:
        return resolve_key(spec.api_key_env, self.secrets) if spec.api_key_env else ""

    def unusable(self, spec: McpServerConfig) -> str | None:
        """Why a server cannot even be tried; None when it can."""
        if spec.transport == "http" and not spec.url:
            return "sem endereço configurado"
        if spec.transport == "stdio" and not spec.command:
            return "sem comando configurado"
        if spec.auth != "none" and spec.api_key_env and not self._key(spec):
            return f"chave não configurada ({spec.api_key_env})"
        return None

    def _redactor(self) -> Callable[[str], str]:
        values = [
            k for s in self.config.tools.servers().values() if (k := self._key(s)) and len(k) >= 4
        ]
        return lambda text: redact(text, values)

    # ------------------------------------------------------------------ session
    @asynccontextmanager
    async def session(
        self, role: str | None = None, *, only: Iterable[str] | None = None
    ) -> AsyncIterator[McpSession]:
        """Connect to the servers `role` may use (or just the `only` ones) for one agent run."""
        redactor = self._redactor()
        sess = McpSession(redactor)
        if only is not None:
            servers = {n: s for n, s in self.config.tools.servers().items() if n in set(only)}
        else:
            servers = self.servers_for(role or "")
        async with AsyncExitStack() as stack:
            used: set[str] = set()
            for name, spec in servers.items():
                if not spec.enabled:
                    sess.unavailable[name] = "desativado nas configurações"
                    continue
                if reason := self.unusable(spec):
                    sess.unavailable[name] = reason
                    continue
                child = AsyncExitStack()
                try:
                    async with asyncio.timeout(CONNECT_TIMEOUT_S):
                        target = await self._connector(child, name, spec, self._key(spec))
                        client = await child.enter_async_context(_client(target, spec))
                        remote = await _list_tools(client)
                except Exception as exc:  # noqa: BLE001 - unreachable server = declared limitation
                    sess.unavailable[name] = redactor(_explain(exc))
                    try:
                        await child.aclose()
                    except Exception:  # noqa: BLE001, S110
                        pass
                    continue
                stack.push_async_callback(child.aclose)
                sess._clients[name] = client
                sess._timeouts[name] = spec.timeout_s
                for tool in remote:
                    if spec.allow and not any(
                        fnmatch.fnmatchcase(tool.name.lower(), pat.lower()) for pat in spec.allow
                    ):
                        continue
                    exposed = _unique_name(tool.name, name, used)
                    used.add(exposed)
                    sess.tools[exposed] = McpTool(
                        name=exposed,
                        server=name,
                        remote_name=tool.name,
                        description=(tool.description or tool.title or "")[:600],
                        parameters=clean_schema(tool.input_schema),
                    )
                if not any(t.server == name for t in sess.tools.values()):
                    sess.unavailable[name] = "o servidor não oferece nenhuma ferramenta permitida"
            yield sess


# ------------------------------------------------------------------------- transports


async def default_connector(
    stack: AsyncExitStack, name: str, spec: McpServerConfig, key: str
) -> Any:
    """The real transports: streamable HTTP (with the key in a header or URL parameter) or a
    stdio child process (with the key in its environment only)."""
    from mcp import StdioServerParameters

    if spec.transport == "stdio":
        env = {(spec.auth_name or spec.api_key_env): key} if key and spec.auth == "env" else {}
        return StdioServerParameters(command=spec.command, args=list(spec.args), env=env or None)

    import httpx2
    from mcp.client.streamable_http import streamable_http_client

    url = spec.url
    headers = dict(spec.headers)
    if key and spec.auth == "bearer":
        headers["Authorization"] = f"Bearer {key}"
    elif key and spec.auth == "query":
        sep = "&" if "?" in url else "?"
        url = f"{url}{sep}{urlencode({spec.auth_name or 'apiKey': key})}"
    http = await stack.enter_async_context(
        httpx2.AsyncClient(headers=headers, timeout=httpx2.Timeout(30.0, read=300.0))
    )
    return streamable_http_client(url, http_client=http)


def _client(target: Any, spec: McpServerConfig) -> Any:
    from mcp import Client

    return Client(target, read_timeout_seconds=spec.timeout_s, cache=None)


async def _list_tools(client: Any) -> list[Any]:
    tools: list[Any] = []
    cursor: str | None = None
    for _ in range(MAX_LIST_PAGES):
        page = await client.list_tools(cursor=cursor)
        tools += list(page.tools)
        cursor = getattr(page, "next_cursor", None)
        if not cursor:
            break
    return tools


# ---------------------------------------------------------------------------- helpers


def _unique_name(remote: str, server: str, used: set[str]) -> str:
    """A model-safe tool name that collides with neither a local tool nor another server's."""
    base = re.sub(r"[^A-Za-z0-9_-]", "_", remote)[:64] or "tool"
    for candidate in (base, f"{server}_{base}"[:64]):
        if candidate not in used and candidate not in _LOCAL_TOOL_NAMES:
            return candidate
    n = 2
    while f"{base[:58]}_{n}" in used:
        n += 1
    return f"{base[:58]}_{n}"


_DROPPED_SCHEMA_KEYS = {"$schema", "additionalProperties"}  # providers such as Gemini refuse them


def clean_schema(schema: Any) -> dict[str, Any]:
    """The tool's JSON schema without the keys some providers reject, always an object schema."""

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            return {k: walk(v) for k, v in node.items() if k not in _DROPPED_SCHEMA_KEYS}
        if isinstance(node, list):
            return [walk(v) for v in node]
        return node

    out = walk(schema) if isinstance(schema, dict) else {}
    out.setdefault("type", "object")
    out.setdefault("properties", {})
    return out


def render_result(res: Any) -> str:
    """Text of a tool result: text blocks joined, other blocks named, structured data last."""
    parts: list[str] = []
    for block in getattr(res, "content", None) or []:
        text = getattr(block, "text", None)
        if isinstance(text, str):
            parts.append(text)
            continue
        resource = getattr(block, "resource", None)
        if resource is not None and isinstance(getattr(resource, "text", None), str):
            parts.append(resource.text)
        elif getattr(block, "uri", None):
            parts.append(f"[link: {block.uri}]")
        else:
            parts.append(f"[{getattr(block, 'type', 'conteúdo')} omitido]")
    if not parts and getattr(res, "structured_content", None):
        parts.append(json.dumps(res.structured_content, ensure_ascii=False))
    return "\n".join(parts).strip() or "(resposta vazia)"


def _brief(exc: BaseException) -> str:
    inner = exc
    while isinstance(inner, BaseExceptionGroup) and inner.exceptions:  # anyio task groups wrap
        inner = inner.exceptions[0]
    return f"{type(inner).__name__}: {str(inner)[:140]}".strip(": ")


def _explain(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return f"sem resposta em {CONNECT_TIMEOUT_S:.0f}s"
    return _brief(exc)


# ------------------------------------------------------------------------------ probe


async def probe_server(hub: McpHub, name: str) -> ProbeResult:
    """Founder-facing check of one server through the same path the agents use."""
    spec = hub.config.tools.servers().get(name)
    if spec is None:
        return ProbeResult(name, False, "servidor não está na configuração desta fábrica")
    start = time.monotonic()
    async with hub.session(only=[name]) as sess:
        ms = int((time.monotonic() - start) * 1000)
        if sess.available:
            names = ", ".join(sorted(sess.tools))
            return ProbeResult(
                name,
                True,
                f"servidor MCP ok ({len(sess.tools)} ferramentas: {names})",
                latency_ms=ms,
            )
        reason = sess.unavailable.get(name, "sem detalhes")
    return ProbeResult(name, False, f"servidor MCP indisponível: {reason}", latency_ms=ms)
