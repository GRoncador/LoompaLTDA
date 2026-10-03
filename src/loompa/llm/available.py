"""What each key of the factory can call (plan 9.1): the model list of every provider with a key.

It is the source of truth for *availability*: `GET <base_url>/models` on the OpenAI-compatible
APIs (OpenRouter, Google AI Studio, OpenAI, xAI, the BYOK makers), `/v1/models` on Anthropic and
the models pulled into a local Ollama. Providers are asked in parallel; one that fails shows its
reason and never hides the others. Prices and ratings are not here (they come from the catalogue,
9.2/9.4). The result is cached in the hub, keyed by provider and base URL, so the screen opens
without a network call; only the founder's refresh asks again. Never stores or logs a key.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

from loompa.config.schema import LoompaConfig, ProviderConfig
from loompa.llm.catalog import CATALOG_CACHE_DIR
from loompa.llm.providers import is_google_endpoint, resolve_key

CACHE_FILE = "provider_models.json"
TIMEOUT_S = 20.0

# Founder-facing reasons (pt-BR); the technical cause goes in `reason`.
_STATUS_TEXT = {
    401: "a chave foi recusada",
    403: "a chave não tem permissão para listar modelos",
    404: "este provedor não publica a lista de modelos",
    429: "o provedor pediu para esperar (limite de pedidos)",
}


@dataclass
class ProviderModels:
    name: str
    label: str
    ok: bool
    models: list[str] = field(default_factory=list)
    detail: str = ""  # pt-BR, founder-facing
    reason: str = ""  # technical, redacted (status line or error class)
    checked_at: float = 0.0
    base_url: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ByokCheck:
    """A `byok_models` entry against the maker's own list: does the id we send exist there?"""

    provider: str
    openrouter_id: str
    own_id: str
    found: bool | None  # None: the maker's list is not known (no key, or listing failed)


def _models_url(cfg: ProviderConfig) -> str:
    base = cfg.base_url.rstrip("/")
    if cfg.kind == "anthropic":
        base = base or "https://api.anthropic.com"
        return f"{base}/v1/models?limit=1000"
    return f"{base}/models"


def _headers(cfg: ProviderConfig, key: str) -> dict[str, str]:
    headers = dict(cfg.extra_headers)
    if cfg.kind == "anthropic":
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
    elif key:
        headers["Authorization"] = f"Bearer {key}"
    return headers


def _ids(cfg: ProviderConfig, body: Any) -> list[str]:
    data = body.get("data") if isinstance(body, dict) else body
    if not isinstance(data, list) and isinstance(body, dict):
        data = body.get("models")  # Ollama's native shape
    out: list[str] = []
    for item in data if isinstance(data, list) else []:
        raw = item.get("id") or item.get("name") if isinstance(item, dict) else item
        if not isinstance(raw, str) or not raw:
            continue
        if is_google_endpoint(cfg.base_url) and raw.startswith("models/"):
            raw = raw.removeprefix("models/")  # Google lists `models/gemini-…`, calls `gemini-…`
        out.append(raw)
    return sorted(set(out))


def wanted(config: LoompaConfig, secrets: Mapping[str, str] | None) -> dict[str, ProviderConfig]:
    """The providers worth asking: every one with a key set, plus a keyless local one (Ollama)
    that some slot uses. A keyless provider nobody uses is left alone: asking costs a timeout."""
    used = {
        c.provider
        for tiers in [config.models.tiers, *config.models.matrix.values()]
        for cands in tiers.values()
        for c in cands
    }
    out = {}
    for name, cfg in config.providers.items():
        if cfg.kind == "mock":
            continue
        keyed = bool(cfg.api_key_env) and bool(resolve_key(cfg.api_key_env, secrets))
        if keyed or (not cfg.api_key_env and name in used):
            out[name] = cfg
    return out


async def list_provider(
    name: str, cfg: ProviderConfig, secrets: Mapping[str, str] | None, client: httpx.AsyncClient
) -> ProviderModels:
    key = resolve_key(cfg.api_key_env, secrets) if cfg.api_key_env else ""
    result = ProviderModels(
        name=name,
        label=cfg.label or name,
        ok=False,
        checked_at=time.time(),
        base_url=cfg.base_url,
    )
    try:
        resp = await client.get(_models_url(cfg), headers=_headers(cfg, key), timeout=TIMEOUT_S)
    except httpx.TimeoutException:
        result.detail, result.reason = "o provedor não respondeu a tempo", "timeout"
        return result
    except httpx.HTTPError as exc:
        result.detail, result.reason = "não consegui falar com o provedor", type(exc).__name__
        return result
    if resp.status_code >= 400:
        result.detail = _STATUS_TEXT.get(resp.status_code, "o provedor recusou o pedido")
        result.reason = f"HTTP {resp.status_code}"
        return result
    try:
        result.models = _ids(cfg, resp.json())
    except ValueError:
        result.detail, result.reason = "a resposta do provedor veio num formato inesperado", "json"
        return result
    result.ok = True
    result.detail = f"{len(result.models)} modelos disponíveis"
    return result


async def available(
    config: LoompaConfig,
    secrets: Mapping[str, str] | None,
    *,
    client: httpx.AsyncClient | None = None,
    cache_dir: Path | None = None,
) -> list[ProviderModels]:
    """Ask every provider worth asking, in parallel, and refresh the cache with the answers."""
    providers = wanted(config, secrets)
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        results = await asyncio.gather(
            *(list_provider(n, c, secrets, client) for n, c in providers.items())
        )
    finally:
        if own:
            await client.aclose()
    save_cache(results, cache_dir)
    return list(results)


def _cache_path(cache_dir: Path | None) -> Path:
    return (cache_dir or CATALOG_CACHE_DIR) / CACHE_FILE


def save_cache(results: list[ProviderModels], cache_dir: Path | None = None) -> None:
    path = _cache_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        old = {}
    for r in results:
        old[f"{r.name}|{r.base_url}"] = r.as_dict()
    path.write_text(json.dumps(old, ensure_ascii=False, indent=1), encoding="utf-8")


def cached(
    config: LoompaConfig, secrets: Mapping[str, str] | None, cache_dir: Path | None = None
) -> list[ProviderModels]:
    """What the last refresh found for this factory's providers, without a network call."""
    try:
        data = json.loads(_cache_path(cache_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for name, cfg in wanted(config, secrets).items():
        raw = data.get(f"{name}|{cfg.base_url}")
        if isinstance(raw, dict):
            out.append(
                ProviderModels(
                    **{k: raw[k] for k in ProviderModels.__dataclass_fields__ if k in raw}
                )
            )
    return out


def byok_checks(config: LoompaConfig, results: list[ProviderModels]) -> list[ByokCheck]:
    """Each maker id `byok_models` would send, checked against that maker's list."""
    lists = {r.name: set(r.models) for r in results if r.ok}
    return [
        ByokCheck(name, or_id, own, (own in lists[name]) if name in lists else None)
        for name, cfg in config.providers.items()
        for or_id, own in sorted(cfg.byok_models.items())
    ]
