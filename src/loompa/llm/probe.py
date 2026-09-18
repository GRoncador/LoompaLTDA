"""Connection tests for providers and tools: one minimal call, a founder-readable verdict.

Used by `loompa init`, `loompa providers test` and the dashboard settings screen. Never
returns or logs the key; error text is trimmed and never includes the request."""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass

import httpx

from loompa.config.schema import LoompaConfig, ToolProviderConfig
from loompa.llm.providers import LLMError, Message, QuotaExhausted, build_provider, resolve_key


@dataclass
class ProbeResult:
    name: str
    ok: bool
    detail: str  # pt-BR, founder-facing
    model: str = ""
    latency_ms: int = 0

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _first_model(config: LoompaConfig, provider: str) -> str:
    for tier in ("tier2", "tier1"):
        for cand in config.models.tiers.get(tier, []):
            if cand.provider == provider:
                return cand.model
    for cands in config.models.tiers.values():
        for cand in cands:
            if cand.provider == provider:
                return cand.model
    return ""


async def probe_provider(
    config: LoompaConfig,
    name: str,
    *,
    secrets: Mapping[str, str] | None = None,
    model: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> ProbeResult:
    cfg = config.providers.get(name)
    if cfg is None:
        return ProbeResult(name, False, "provedor não está na configuração desta fábrica")
    model = model or _first_model(config, name)
    if not model:
        return ProbeResult(name, False, "nenhum modelo deste provedor aparece nos tiers")
    if cfg.api_key_env and not resolve_key(cfg.api_key_env, secrets):
        return ProbeResult(name, False, "chave não configurada", model=model)
    prov = build_provider(name, cfg, client=client, secrets=secrets)
    start = time.monotonic()
    try:
        resp = await prov.complete(
            model,
            [Message("user", "Responda apenas: ok")],
            temperature=0.0,
            max_tokens=8,
        )
    except QuotaExhausted:
        return ProbeResult(
            name, True, "chave válida; o provedor está com limite de uso no momento", model=model
        )
    except LLMError as exc:
        status = exc.status
        if status in (401, 403):
            detail = "chave recusada pelo provedor"
        elif status == 404:
            detail = f"modelo não encontrado neste provedor ({model})"
        elif status and status >= 500:
            detail = "provedor indisponível no momento"
        elif "rede" in str(exc):
            detail = "sem conexão com o provedor"
        else:
            detail = "o provedor devolveu um erro inesperado"
        return ProbeResult(name, False, detail, model=model)
    except Exception:  # noqa: BLE001
        return ProbeResult(name, False, "falha inesperada ao falar com o provedor", model=model)
    finally:
        if client is None:
            await prov.aclose()
    ms = int((time.monotonic() - start) * 1000)
    if resp.finish_reason or resp.text or resp.output_tokens:
        return ProbeResult(name, True, "conexão ok", model=model, latency_ms=ms)
    return ProbeResult(name, False, "o provedor respondeu vazio", model=model, latency_ms=ms)


async def probe_tavily(
    cfg: ToolProviderConfig,
    *,
    secrets: Mapping[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
) -> ProbeResult:
    key = resolve_key(cfg.api_key_env, secrets)
    if not key:
        return ProbeResult("tavily", False, "chave não configurada")
    own = client is None
    client = client or httpx.AsyncClient(timeout=30.0)
    start = time.monotonic()
    try:
        resp = await client.post(
            f"{(cfg.base_url or 'https://api.tavily.com').rstrip('/')}/search",
            json={"query": "loompa ltda", "max_results": 1, "search_depth": "basic"},
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
        )
    except httpx.HTTPError:
        return ProbeResult("tavily", False, "sem conexão com o serviço de busca")
    finally:
        if own:
            await client.aclose()
    ms = int((time.monotonic() - start) * 1000)
    if resp.status_code in (401, 403):
        return ProbeResult("tavily", False, "chave recusada pelo serviço de busca")
    if resp.status_code == 429:
        return ProbeResult("tavily", True, "chave válida; limite de uso atingido no momento")
    if resp.status_code >= 400:
        return ProbeResult("tavily", False, "o serviço de busca devolveu um erro inesperado")
    return ProbeResult("tavily", True, "busca web ok", latency_ms=ms)
