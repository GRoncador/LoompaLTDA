"""models.dev: price and card of each model per provider, and one model across routes (plan 9.2/9.3).

models.dev (MIT, community-maintained) publishes one JSON with every provider the factory knows and,
per provider, each model's price, context, tool support and reasoning options. Two uses here:

* **Card (9.2).** What a provider's own API charges and supports for a model. The meter prices a
  direct call with it when the price table has no row for that model.
* **Identity (9.3).** The same model has a different id on each route (`z-ai/glm-5.3` on
  OpenRouter, `glm-5.3` on Z.AI). Two entries are one model only when models.dev gives both the
  same `canonical_model_id`. After that, one explicit rule per provider, never name similarity: a
  maker entry with no canonical id is `id_prefix + its id`, compared exactly with an OpenRouter
  entry's id or canonical id. A wrong match puts one model's price and rating on another, as the
  hand-written `qwen/qwen3.8-max-0902 -> qwen3.8-max` did (the pinned release against Alibaba's
  moving alias). What matches nothing is counted, not guessed.

Fetched only by a founder's refresh or a stale cache at engine start; read from the hub cache
otherwise. Everything degrades to "unknown" when the file is missing.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from loompa.config.schema import LoompaConfig, Price
from loompa.llm.catalog import CATALOG_CACHE_DIR

log = logging.getLogger(__name__)

URL = "https://models.dev/api.json"
CACHE_FILE = "models_dev.json"
STALE_S = 24 * 3600
OPENROUTER = "openrouter"

# Loompa's provider names that differ from models.dev's ids (`ProviderConfig.models_dev_id` wins).
PROVIDER_IDS = {"gemini": "google", "moonshot": "moonshotai"}


@dataclass(frozen=True)
class ModelCard:
    provider: str  # models.dev provider id
    id: str
    name: str = ""
    canonical: str = ""
    price: Price | None = None
    context: int | None = None
    max_output: int | None = None
    tools: bool | None = None
    reasoning: bool | None = None
    efforts: tuple[str, ...] = ()  # effort labels models.dev lists; empty = none or a toggle only
    toggle_only: bool = False  # thinking can be turned on or off, with no effort labels
    deprecated: bool = False


@dataclass
class Identity:
    """One maker's OpenRouter ids and its own ids, as models.dev ties them."""

    provider: str  # Loompa provider name
    pairs: dict[str, str] = field(default_factory=dict)  # OpenRouter id -> maker id
    unmatched: list[str] = field(default_factory=list)  # maker ids with no OpenRouter pair
    contradicted: dict[str, str] = field(default_factory=dict)  # config pairs models.dev refutes


def hub_cache_dir() -> Path:
    """The hub's cache folder, following LOOMPA_HOME like the registry does."""
    env = os.environ.get("LOOMPA_HOME")
    return Path(env) / "cache" if env else CATALOG_CACHE_DIR


def _cache_path(cache_dir: Path | None) -> Path:
    return (cache_dir or hub_cache_dir()) / CACHE_FILE


_memo: dict[Path, tuple[float, dict[str, Any]]] = {}


def load(cache_dir: Path | None = None) -> dict[str, Any]:
    """The cached JSON, re-read only when the file changed. {} when there is none."""
    path = _cache_path(cache_dir)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    hit = _memo.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    _memo[path] = (mtime, data)
    return data


def age_s(cache_dir: Path | None = None) -> float | None:
    try:
        return time.time() - _cache_path(cache_dir).stat().st_mtime
    except OSError:
        return None


async def refresh(
    *, client: httpx.AsyncClient | None = None, cache_dir: Path | None = None, timeout: float = 30.0
) -> dict[str, Any]:
    """Download models.dev into the hub cache. Raises httpx errors; the old cache stays."""
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        resp = await client.get(URL, timeout=timeout)
        resp.raise_for_status()
        data = resp.json()
    finally:
        if own:
            await client.aclose()
    if not isinstance(data, dict) or OPENROUTER not in data:
        raise ValueError("models.dev: unexpected shape")
    path = _cache_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)
    return data


async def refresh_if_stale(cache_dir: Path | None = None) -> bool:
    """Best effort at engine start: a day-old cache is fetched again; a failure keeps the old."""
    age = age_s(cache_dir)
    if age is not None and age < STALE_S:
        return False
    try:
        await refresh(cache_dir=cache_dir, timeout=10.0)
    except Exception as exc:  # noqa: BLE001 - offline is normal; the cache (or nothing) stands
        log.info("models.dev not refreshed: %s", exc)
        return False
    return True


def provider_id(config: LoompaConfig, name: str) -> str:
    cfg = config.providers.get(name)
    return (cfg.models_dev_id if cfg is not None and cfg.models_dev_id else "") or PROVIDER_IDS.get(
        name, name
    )


def _price(cost: Any) -> Price | None:
    if not isinstance(cost, dict) or cost.get("input") is None or cost.get("output") is None:
        return None
    try:
        return Price(
            input=float(cost["input"]),
            output=float(cost["output"]),
            cached_input=float(cost.get("cache_read") or cost["input"]),
        )
    except (TypeError, ValueError):
        return None


def _card(md_provider: str, raw: dict[str, Any]) -> ModelCard:
    options = raw.get("reasoning_options") if isinstance(raw.get("reasoning_options"), list) else []
    efforts: list[str] = []
    toggle = False
    for opt in options:
        if not isinstance(opt, dict):
            continue
        if opt.get("type") == "effort" and isinstance(opt.get("values"), list):
            efforts += [str(v) for v in opt["values"]]
        elif opt.get("type") == "toggle":
            toggle = True
    limit = raw.get("limit") if isinstance(raw.get("limit"), dict) else {}
    return ModelCard(
        provider=md_provider,
        id=str(raw.get("id") or ""),
        name=str(raw.get("name") or ""),
        canonical=str(raw.get("canonical_model_id") or ""),
        price=_price(raw.get("cost")),
        context=limit.get("context") if isinstance(limit.get("context"), int) else None,
        max_output=limit.get("output") if isinstance(limit.get("output"), int) else None,
        tools=raw.get("tool_call") if isinstance(raw.get("tool_call"), bool) else None,
        reasoning=raw.get("reasoning") if isinstance(raw.get("reasoning"), bool) else None,
        efforts=tuple(dict.fromkeys(efforts)),
        toggle_only=toggle and not efforts,
        deprecated=raw.get("status") == "deprecated",
    )


def _models(data: dict[str, Any], md_provider: str) -> dict[str, dict[str, Any]]:
    block = data.get(md_provider)
    models = block.get("models") if isinstance(block, dict) else None
    return {k: v for k, v in (models or {}).items() if isinstance(v, dict)}


def card(
    config: LoompaConfig, provider: str, model: str, data: dict[str, Any] | None = None
) -> ModelCard | None:
    """The card of `model` on Loompa provider `provider`, or None when models.dev has none."""
    data = load() if data is None else data
    md = provider_id(config, provider)
    raw = _models(data, md).get(model)
    return _card(md, raw) if raw is not None else None


def price(config: LoompaConfig, provider: str, model: str) -> Price | None:
    c = card(config, provider, model)
    return c.price if c is not None else None


def identity(
    config: LoompaConfig, provider: str, data: dict[str, Any] | None = None
) -> Identity | None:
    """OpenRouter id -> `provider`'s own id, from models.dev. None without data for both sides."""
    data = load() if data is None else data
    router = _models(data, OPENROUTER)
    maker = _models(data, provider_id(config, provider))
    if not router or not maker:
        return None
    cfg = config.providers.get(provider)
    prefix = cfg.id_prefix if cfg is not None else ""
    by_key: dict[str, list[str]] = {}
    for or_id, raw in router.items():
        for key in {or_id, str(raw.get("canonical_model_id") or "")} - {""}:
            by_key.setdefault(key, []).append(or_id)
    out = Identity(provider=provider)
    for own_id, raw in maker.items():
        canonical = str(raw.get("canonical_model_id") or "")
        if canonical:
            hits = [o for o in by_key.get(canonical, []) if _canonical(router[o], o) == canonical]
        elif prefix:
            hits = by_key.get(prefix + own_id, [])
        else:
            hits = []
        for or_id in hits:
            out.pairs[or_id] = own_id
        if not hits:
            out.unmatched.append(own_id)
    if cfg is not None:
        for or_id, own_id in cfg.byok_models.items():
            refuted = _refutes(router.get(or_id), maker.get(own_id))
            if refuted:
                out.contradicted[or_id] = own_id
    out.unmatched.sort()
    return out


def _canonical(raw: dict[str, Any], fallback: str) -> str:
    return str(raw.get("canonical_model_id") or fallback)


def _refutes(router_raw: dict[str, Any] | None, maker_raw: dict[str, Any] | None) -> bool:
    """A hand-written pair is wrong when models.dev names both sides and gives them different
    canonical ids. Missing data never refutes: the founder's rule stands."""
    if not router_raw or not maker_raw:
        return False
    a, b = router_raw.get("canonical_model_id"), maker_raw.get("canonical_model_id")
    return bool(a) and bool(b) and a != b


_tables: dict[tuple[Any, ...], dict[str, str]] = {}


def byok_table(config: LoompaConfig, provider: str) -> dict[str, str]:
    """What the router sends to `provider` instead of OpenRouter: the config's explicit pairs,
    minus those models.dev refutes, plus the pairs models.dev ties. Only a provider that opted in
    (pairs, a prefix or a models.dev id) gets any: a Gemini key does not reroute by surprise."""
    cfg = config.providers.get(provider)
    if cfg is None or not (cfg.byok_models or cfg.id_prefix or cfg.models_dev_id):
        return {}
    data = load()
    key = (
        id(data),
        provider,
        cfg.id_prefix,
        cfg.models_dev_id,
        tuple(sorted(cfg.byok_models.items())),
    )
    if key in _tables:
        return _tables[key]
    ident = identity(config, provider, data)
    if ident is None:
        table = dict(cfg.byok_models)
    else:
        table = {**ident.pairs, **cfg.byok_models}
        for or_id in ident.contradicted:
            table.pop(or_id, None)
    _tables[key] = table
    return table
