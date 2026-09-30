"""OpenRouter catalogue: read it, filter it, rank it (ADR-0011).

`loompa models sync` proposes a refreshed model list from `GET /api/v1/models`, a public endpoint
that carries prices, supported parameters, reasoning limits and Artificial Analysis indices. This
module is the pure part: parse the payload, say why a model is out, rank the rest, and merge the
result into a factory's tiers. Nothing here touches the store, the inbox or the network beyond
`fetch_catalog`; governance (propose, approve, never mid-sprint) lives in `loompa.models_sync`.

Two rules the real catalogue forced (447 models on 2026-09-20; 378 take tools, 185 of those have a
benchmark, 31 force reasoning without any way to limit it):

* a model with no benchmark is *left out and counted*, never ranked as if its score were 0, and the
  report says how many; and
* a model whose reasoning is mandatory must accept a low effort, otherwise the output budget goes
  to thinking and the answer comes back empty (the failure that broke tier1, see ADR-0002).
"""

from __future__ import annotations

import json
import statistics
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from loompa.config.schema import LoompaConfig, ModelCandidate, Price

PROVIDER = "openrouter"
FREE_ROUTER = (
    "openrouter/free"  # a free model chosen per request: the last fallback of an alias list
)
LIMITABLE_EFFORTS = {"minimal", "low"}
CATALOG_CACHE_DIR = Path.home() / ".loompa" / "cache"
CATALOG_CACHE_FILE = "openrouter_catalog.json"

# Why a model is out of the ranking, in the order the checks run. `unrated` is last on purpose:
# it counts only models that would otherwise have qualified.
REASONS = {
    "alias": "apelido (-latest): segue sozinho a versão nova do fabricante",
    "variant": "variante (lote, gratuita) que não entra no ranking",
    "no_tools": "não usa ferramentas",
    "not_text": "não é de texto",
    "bad_price": "preço inválido (roteador)",
    "short_context": "contexto curto",
    "expiring": "sai do catálogo em breve",
    "unlimited_reasoning": "raciocínio obrigatório sem como limitar",
    "unrated": "sem nota nos benchmarks",
}


class CatalogError(RuntimeError):
    pass


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    """A catalogue price arrives as a string ("0.00000091"); an index as a number or null."""
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class CatalogModel:
    id: str
    name: str
    input_usd: float | None  # USD per 1M tokens
    output_usd: float | None
    cached_usd: float | None
    context: int
    tools: bool
    text_only: bool
    alias: bool
    target: str  # for an alias, the name of the model it points to today
    target_id: str  # ...and its id, the one `response.model` reports
    coding: float | None
    agentic: float | None
    mandatory_reasoning: bool
    efforts: tuple[str, ...]
    expires: date | None
    intelligence: float | None = None
    created: date | None = None  # when the vendor published it, for "newest first"

    @property
    def vendor(self) -> str:
        return self.id.lstrip("~").split("/", 1)[0]

    @property
    def label(self) -> str:
        """The name a person reads: an alias also says what it points to today."""
        return f"{self.name} (hoje: {self.target})" if self.alias and self.target else self.name

    @property
    def quality(self) -> float | None:
        """Mean of the coding and agentic indices: what a Worker and a tool-using planner do."""
        if self.coding is None or self.agentic is None:
            return None
        return (self.coding + self.agentic) / 2

    def score_tier1(self) -> float | None:
        """Strategic (Master/Architect): 50% intelligence + 30% coding + 20% agentic."""
        if self.coding is None or self.agentic is None:
            return None
        intel = self.intelligence if self.intelligence is not None else self.quality
        if intel is None:
            return None
        return round(0.50 * intel + 0.30 * self.coding + 0.20 * self.agentic, 1)

    def score_tier2(self) -> float | None:
        """Execution (Worker/Inspector): 60% coding + 30% agentic + 10% intelligence."""
        if self.coding is None or self.agentic is None:
            return None
        intel = self.intelligence if self.intelligence is not None else self.quality
        if intel is None:
            return None
        return round(0.60 * self.coding + 0.30 * self.agentic + 0.10 * intel, 1)

    def score_routine(self) -> float | None:
        """Routine & Research (Analyst/Kaizen/Deployer): 55% agentic + 30% intelligence + 15% coding."""
        if self.coding is None or self.agentic is None:
            return None
        intel = self.intelligence if self.intelligence is not None else self.quality
        if intel is None:
            return None
        return round(0.55 * self.agentic + 0.30 * intel + 0.15 * self.coding, 1)

    def score_general(self) -> float | None:
        """One cluster for everyone: the plain mean of the three indices, with no profile in it.
        A factory that turns clusters off is saying it does not want the three-way distinction."""
        if self.coding is None or self.agentic is None:
            return None
        intel = self.intelligence if self.intelligence is not None else self.quality
        if intel is None:
            return None
        return round((intel + self.coding + self.agentic) / 3, 1)

    @property
    def blended(self) -> float | None:
        """USD per 1M tokens at 3 input : 1 output, the mix of a tool loop that re-reads its context."""
        if self.input_usd is None or self.output_usd is None:
            return None
        return (3 * self.input_usd + self.output_usd) / 4

    def price(self) -> Price:
        return Price(
            input=round(self.input_usd or 0.0, 6),
            output=round(self.output_usd or 0.0, 6),
            cached_input=round(self.cached_usd if self.cached_usd is not None else 0.0, 6),
        )


def _benchmarks(entry: dict[str, Any]) -> dict[str, Any]:
    return _dict(_dict(entry.get("benchmarks")).get("artificial_analysis"))


def parse_model(raw: Any, index: dict[str, dict[str, Any]] | None = None) -> CatalogModel | None:
    """One catalogue entry, or None when it has no usable id. Every field is read defensively:
    a provider changing one shape must cost one model, never the whole sync.

    An alias (`~z-ai/glm-latest`) carries no benchmark of its own: it is rated by the model it
    points to today, looked up in `index` (raw entries by id and canonical slug)."""
    entry = _dict(raw)
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id:
        return None
    arch = _dict(entry.get("architecture"))
    pricing = _dict(entry.get("pricing"))
    bench = _benchmarks(entry)
    if not bench and index:
        bench = _benchmarks(index.get(str(_dict(entry.get("alias_target")).get("slug")), {}))
    reasoning = _dict(entry.get("reasoning"))

    def per_million(value: Any) -> float | None:
        n = _number(value)
        return None if n is None else n * 1_000_000

    expires = None
    if isinstance(entry.get("expiration_date"), str):
        try:
            expires = date.fromisoformat(entry["expiration_date"][:10])
        except ValueError:
            expires = None
    created = None
    created_raw = _number(entry.get("created"))
    if created_raw:
        try:
            created = datetime.fromtimestamp(created_raw, tz=UTC).date()
        except (OSError, OverflowError, ValueError):
            created = None
    inputs, outputs = _list(arch.get("input_modalities")), _list(arch.get("output_modalities"))
    context = entry.get("context_length")
    return CatalogModel(
        id=model_id,
        name=str(entry.get("name") or model_id),
        input_usd=per_million(pricing.get("prompt")),
        output_usd=per_million(pricing.get("completion")),
        cached_usd=per_million(pricing.get("input_cache_read")),
        context=context if isinstance(context, int) else 0,
        tools="tools" in _list(entry.get("supported_parameters")),
        text_only="text" in inputs and outputs == ["text"],
        alias=model_id.startswith("~") or bool(entry.get("alias_target")),
        target=str(_dict(entry.get("alias_target")).get("name") or ""),
        target_id=str(_dict(entry.get("alias_target")).get("slug") or ""),
        coding=_number(bench.get("coding_index")),
        agentic=_number(bench.get("agentic_index")),
        mandatory_reasoning=reasoning.get("mandatory") is True,
        efforts=tuple(str(e) for e in _list(reasoning.get("supported_efforts"))),
        expires=expires,
        intelligence=_number(bench.get("intelligence_index")),
        created=created,
    )


def parse_catalog(payload: Any) -> list[CatalogModel]:
    rows = [row for row in _list(_dict(payload).get("data")) if isinstance(row, dict)]
    index = {str(k): row for row in rows for k in (row.get("id"), row.get("canonical_slug")) if k}
    models = [m for raw in _list(_dict(payload).get("data")) if (m := parse_model(raw, index))]
    if not models:
        raise CatalogError("o catálogo veio vazio ou em um formato desconhecido")
    return models


def fetch_catalog(
    config: LoompaConfig,
    *,
    client: httpx.Client | None = None,
    timeout: float = 30.0,
    use_cache: bool = True,
    force_refresh: bool = False,
    cache_dir: Path | None = None,
) -> list[CatalogModel]:
    """The catalogue of the provider this factory calls OpenRouter with. Public: sends no key.

    The payload is cached in ~/.loompa/cache/ and only re-read from the network when the founder
    asks for it (`force_refresh`), so opening the dashboard costs nothing. There is no schedule
    behind it: a model swap disturbs tuned prompts, so it happens when a person decides it does."""
    provider = config.providers.get(PROVIDER)
    if provider is None or not provider.base_url:
        raise CatalogError("a OpenRouter não está configurada nesta fábrica")
    url = provider.base_url.rstrip("/") + "/models"

    if client is not None:
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return parse_catalog(resp.json())
        except httpx.HTTPError as exc:
            raise CatalogError(
                "não consegui ler o catálogo da OpenRouter (sem rede ou serviço fora do ar)"
            ) from exc
        except ValueError as exc:
            raise CatalogError("o catálogo da OpenRouter não veio em JSON") from exc

    target_dir = cache_dir or CATALOG_CACHE_DIR
    cache_file = target_dir / CATALOG_CACHE_FILE

    if use_cache and not force_refresh and cache_file.is_file():
        try:
            cached_data = json.loads(cache_file.read_text(encoding="utf-8"))
            return parse_catalog(cached_data)
        except Exception:
            pass

    try:
        with httpx.Client(timeout=timeout) as cli:
            resp = cli.get(url)
            resp.raise_for_status()
            payload = resp.json()
            if use_cache:
                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    cache_file.write_text(json.dumps(payload), encoding="utf-8")
                except Exception:
                    pass
            return parse_catalog(payload)
    except httpx.HTTPError as exc:
        if use_cache and target_dir.is_dir():
            # Older builds wrote one file per month; any of them still beats no catalogue at all.
            for fallback_file in sorted(target_dir.glob("openrouter_catalog*.json"), reverse=True):
                try:
                    cached_data = json.loads(fallback_file.read_text(encoding="utf-8"))
                    return parse_catalog(cached_data)
                except Exception:
                    continue
        raise CatalogError(
            "não consegui ler o catálogo da OpenRouter (sem rede ou serviço fora do ar)"
        ) from exc
    except ValueError as exc:
        raise CatalogError("o catálogo da OpenRouter não veio em JSON") from exc


@dataclass(frozen=True)
class EndpointPrices:
    """What a model costs across the providers that serve it on OpenRouter, blended 3:1."""

    model: str
    providers: int  # the ones that accept tools: the only ones the factory's calls can land on
    median: float | None  # USD per 1M tokens


def parse_endpoints(model: str, payload: Any) -> EndpointPrices:
    """`GET /models/<id>/endpoints`: one entry per provider, each with its own price. The median
    is the model's price; the catalogue's single `pricing` is one of them, and OpenRouter changes
    which (DeepSeek V4 Flash showed Relace's US$ 0.01/1.28 among 29 providers from 0.04 to 0.66,
    checked on 2026-09-30). A provider entering or leaving at either end barely moves a median."""
    data = _dict(_dict(payload).get("data"))
    blended = []
    for entry in _list(data.get("endpoints")):
        entry = _dict(entry)
        if "tools" not in _list(entry.get("supported_parameters")):
            continue
        pricing = _dict(entry.get("pricing"))
        prompt, completion = _number(pricing.get("prompt")), _number(pricing.get("completion"))
        if prompt is None or completion is None or prompt < 0 or completion < 0:
            continue
        blended.append((3 * prompt + completion) / 4 * 1_000_000)
    return EndpointPrices(
        model=model,
        providers=len(blended),
        median=round(statistics.median(blended), 6) if blended else None,
    )


def fetch_endpoint_prices(
    config: LoompaConfig,
    model_ids: Iterable[str],
    *,
    client: httpx.Client | None = None,
    timeout: float = 20.0,
) -> dict[str, EndpointPrices]:
    """The providers' prices of each model, one public request per model (no key). A model the
    request fails for is left out: a notice missed today is told on the next refresh."""
    provider = config.providers.get(PROVIDER)
    if provider is None or not provider.base_url:
        return {}
    base = provider.base_url.rstrip("/")
    out: dict[str, EndpointPrices] = {}
    cli = client or httpx.Client(timeout=timeout)
    try:
        for model in model_ids:
            try:
                resp = cli.get(f"{base}/models/{model}/endpoints")
                resp.raise_for_status()
                out[model] = parse_endpoints(model, resp.json())
            except (httpx.HTTPError, ValueError):
                continue
    finally:
        if client is None:
            cli.close()
    return out


# ----------------------------------------------------------------------------- ranking


@dataclass(frozen=True)
class Policy:
    """The knobs of the ranking."""

    tier1_ceiling: float = 1.25  # blended USD per 1M tokens the reasoning tier may cost
    tier2_floor: float = 0.80  # fraction of the best quality the execution tier must reach
    picks: int = 3  # candidates per tier (one per vendor, so a fallback is not the same outage)
    min_context: int = 128_000
    expiry_margin_days: int = 60


def unavailable_reason(m: CatalogModel, policy: Policy, today: date) -> str | None:
    """The checks every candidate passes, ranked or not: it must be usable and stay usable."""
    if not m.text_only:
        return "not_text"
    if m.context < policy.min_context:
        return "short_context"
    if m.expires and m.expires < today + timedelta(days=policy.expiry_margin_days):
        return "expiring"
    if m.mandatory_reasoning and not LIMITABLE_EFFORTS & set(m.efforts):
        return "unlimited_reasoning"
    return None


def exclusion_reason(m: CatalogModel, policy: Policy, today: date) -> str | None:
    """Why a model is not ranked. An alias is always one of those reasons.

    A `~vendor/x-latest` id follows whatever the vendor ships next: the model behind it can get
    better, worse or more expensive without anyone approving it. Three pinned candidates per tier
    are already the defence against one of them going down, so the recommendation never proposes
    an alias. A founder who wants one adds it by hand from the full catalogue."""
    if m.alias:
        return "alias"
    if ":" in m.id:
        return "variant"
    if not m.tools:
        return "no_tools"
    if m.input_usd is None or m.output_usd is None or m.input_usd < 0 or m.output_usd < 0:
        return "bad_price"
    if reason := unavailable_reason(m, policy, today):
        return reason
    if m.quality is None:
        return "unrated"
    return None


@dataclass
class Ranking:
    total: int
    eligible: list[CatalogModel]
    excluded: Counter[str]
    best_quality: float
    tier1: list[CatalogModel] = field(default_factory=list)
    tier2: list[CatalogModel] = field(default_factory=list)


def _one_per_vendor(ordered: list[CatalogModel], n: int) -> list[CatalogModel]:
    picked: list[CatalogModel] = []
    seen: set[str] = set()
    for m in ordered:
        if m.vendor not in seen:
            picked.append(m)
            seen.add(m.vendor)
        if len(picked) == n:
            break
    return picked


def rank(models: list[CatalogModel], policy: Policy, today: date) -> Ranking:
    """tier1 is "best quality under a price ceiling"; tier2 is "cheapest above a quality floor".
    A plain quality/price ratio would put the cheapest acceptable model first in both."""
    excluded: Counter[str] = Counter()
    eligible: list[CatalogModel] = []
    for m in models:
        reason = exclusion_reason(m, policy, today)
        if reason:
            excluded[reason] += 1
        else:
            eligible.append(m)
    best = max((m.quality or 0.0 for m in eligible), default=0.0)
    ranking = Ranking(len(models), eligible, excluded, best)
    under = [m for m in eligible if (m.blended or 0.0) <= policy.tier1_ceiling]
    ranking.tier1 = _one_per_vendor(
        sorted(under, key=lambda m: (-(m.quality or 0.0), m.blended or 0.0, m.id)), policy.picks
    )
    good = [m for m in eligible if (m.quality or 0.0) >= policy.tier2_floor * best]
    ranking.tier2 = _one_per_vendor(
        sorted(good, key=lambda m: (m.blended or 0.0, -(m.quality or 0.0), m.id)), policy.picks
    )
    return ranking


# ---------------------------------------------------------------------------- proposal


@dataclass
class Proposal:
    """A new set of OpenRouter candidates per tier, plus the prices they must be billed at.

    `base` is what the tiers looked like when it was made: applying refuses to overwrite a
    configuration the founder edited in the meantime."""

    tiers: dict[str, list[dict[str, Any]]]
    base: dict[str, list[dict[str, Any]]]
    pricing: dict[str, dict[str, float]]
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    gone: list[str] = field(default_factory=list)  # configured, no longer in the catalogue
    expiring: list[str] = field(default_factory=list)  # configured, leaving soon
    summary: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    considered: int = 0
    eligible: int = 0
    excluded: dict[str, int] = field(default_factory=dict)
    mode: str = "pinned"  # the ranking only ever proposes pinned ids (see `exclusion_reason`)
    repriced: list[str] = field(default_factory=list)  # in use, priced differently in the config
    targets: dict[str, str] = field(default_factory=dict)  # aliases in use -> model behind them now
    clusters: dict[str, dict[str, list[dict[str, Any]]]] = field(default_factory=dict)
    all_models: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return self.tiers != self.base or bool(self.repriced)


def _dump(cands: list[ModelCandidate]) -> list[dict[str, Any]]:
    return [c.model_dump() for c in cands]


def _dump_all(config: LoompaConfig) -> dict[str, list[dict[str, Any]]]:
    return {t: _dump(c) for t, c in config.models.tiers.items()}


def is_free(model_id: str) -> bool:
    return model_id.endswith(":free") or model_id == FREE_ROUTER


def _cost_benefit(score: float | None, cost: float) -> float | None:
    """Benchmark points per US$ 1M tokens. A free model has no ratio: it is off the scale, not
    infinitely good, so tier 3 is ranked by score alone."""
    if score is None or cost <= 0:
        return None
    return round(score / cost, 1)


# The score each cluster ranks a model by. `general` is the one used when a factory turns the
# cluster split off; the settings screen shows whichever the founder is filtering by.
CLUSTER_SCORES: dict[str, Callable[[CatalogModel], float | None]] = {
    "strategy": lambda m: m.score_tier1(),
    "engineering": lambda m: m.score_tier2(),
    "routine": lambda m: m.score_routine(),
    "general": lambda m: m.score_general(),
}


def _model_summary(m: CatalogModel, score: float | None) -> dict[str, Any]:
    """One model as the dashboard reads it: the three benchmarks, the price, and the score and
    cost-benefit of every cluster, so a filter can change which number is shown without another
    round trip. Every number is rounded here — a raw mean reaches the screen as 63.800000000000004."""
    cost = round(m.blended or 0.0, 2)
    s = (
        round(score, 1)
        if score is not None
        else (round(m.quality, 1) if m.quality is not None else None)
    )
    scores = {c: fn(m) for c, fn in CLUSTER_SCORES.items()}
    return {
        "id": m.id,
        "name": m.label,
        "vendor": m.vendor,
        "quality": round(m.quality, 1) if m.quality is not None else None,
        "price": cost,
        "free": is_free(m.id) or cost == 0.0,
        "alias": m.alias,
        "alias_target": m.target or "",
        "context": m.context,
        "created": m.created.isoformat() if m.created else None,
        "coding": round(m.coding, 1) if m.coding is not None else None,
        "agentic": round(m.agentic, 1) if m.agentic is not None else None,
        "intelligence": round(m.intelligence, 1) if m.intelligence is not None else None,
        "score": s,
        "cost_benefit": _cost_benefit(s, cost),
        "scores": scores,
        "cost_benefits": {c: _cost_benefit(v, cost) for c, v in scores.items()},
    }


def rank_cluster(
    eligible: list[CatalogModel],
    free_eligible: list[CatalogModel],
    score_fn: Callable[[CatalogModel], float | None],
    policy: Policy,
) -> dict[str, list[dict[str, Any]]]:
    # Paid scored models (cost > 0)
    paid_scored = [
        (m, score_fn(m) or 0.0) for m in eligible if not is_free(m.id) and (m.blended or 0.0) > 0.0
    ]

    # Tier 1: highest score under ceiling
    t1_candidates = [m for m, _ in paid_scored if (m.blended or 0.0) <= policy.tier1_ceiling]
    t1_picks = _one_per_vendor(
        sorted(t1_candidates, key=lambda m: (-(score_fn(m) or 0.0), m.blended or 0.0, m.id)),
        policy.picks,
    )

    # Tier 2: highest cost-benefit (score / cost) under ceiling
    t2_candidates = [m for m, _ in paid_scored if (m.blended or 0.0) <= policy.tier1_ceiling]
    t2_picks = _one_per_vendor(
        sorted(
            t2_candidates,
            key=lambda m: (
                -((score_fn(m) or 0.0) / (m.blended or 1.0)),
                -(score_fn(m) or 0.0),
                m.id,
            ),
        ),
        policy.picks,
    )

    # Tier 3: highest score among free models
    free_candidates = [m for m in free_eligible]
    t3_picks = _one_per_vendor(
        sorted(free_candidates, key=lambda m: (-(score_fn(m) or 0.0), m.id)),
        policy.picks,
    )

    return {
        "tier1": [_model_summary(m, score_fn(m)) for m in t1_picks],
        "tier2": [_model_summary(m, score_fn(m)) for m in t2_picks],
        "tier3": [_model_summary(m, score_fn(m)) for m in t3_picks],
    }


def _same_price(a: Price | None, b: Price) -> bool:
    return a is not None and all(
        abs(x - y) <= 1e-6
        for x, y in zip(a.model_dump().values(), b.model_dump().values(), strict=True)
    )


def build_proposal(
    config: LoompaConfig, models: list[CatalogModel], policy: Policy, today: date
) -> Proposal:
    ranking = rank(models, policy, today)
    by_id = {m.id: m for m in models}
    configured = {
        c.model for cands in config.models.tiers.values() for c in cands if c.provider == PROVIDER
    }
    if not configured:
        raise CatalogError("esta fábrica não usa a OpenRouter em nenhum tier")

    picked = {"tier1": ranking.tier1, "tier2": ranking.tier2}
    tiers: dict[str, list[ModelCandidate]] = {}
    summary: dict[str, list[dict[str, Any]]] = {}
    for tier, current in config.models.tiers.items():
        if not any(c.provider == PROVIDER for c in current) or not picked.get(tier):
            tiers[tier] = list(current)  # nothing to say about this tier: leave it as it is
            continue
        mine = {c.model: c for c in current if c.provider == PROVIDER}
        block = [
            mine.get(m.id) or ModelCandidate(provider=PROVIDER, model=m.id) for m in picked[tier]
        ]
        paid = [c for c in current if c.provider == PROVIDER and not is_free(c.model)]
        if {c.model for c in block} == {c.model for c in paid}:
            block = paid  # same models: the order is the founder's, near-ties are not worth a swap
        for free in (c for c in current if c.provider == PROVIDER and is_free(c.model)):
            live = by_id.get(free.model)
            if live and live.tools and not unavailable_reason(live, policy, today):
                block.append(free)  # a free model still on offer stays as the last fallback
        merged: list[ModelCandidate] = []
        placed = False
        for c in current:  # the OpenRouter block takes the slot of the first OpenRouter candidate
            if c.provider != PROVIDER:
                merged.append(c)
            elif not placed:
                merged.extend(block)
                placed = True
        tiers[tier] = merged
        summary[tier] = [
            _model_summary(m, m.score_tier1() if tier == "tier1" else m.score_tier2())
            for m in picked[tier]
        ]

    free_eligible = [
        m
        for m in models
        if (is_free(m.id) or (m.blended is not None and m.blended == 0.0))
        and m.tools
        and not unavailable_reason(m, policy, today)
        and m.quality is not None
    ]
    if free_eligible:
        summary["tier3_free"] = [
            _model_summary(m, m.score_routine())
            for m in sorted(free_eligible, key=lambda m: (-(m.quality or 0.0), m.id))[
                : policy.picks
            ]
        ]

    used_after = {c.model for cands in tiers.values() for c in cands if c.provider == PROVIDER}
    pricing = {
        mid: by_id[mid].price().model_dump()
        for mid in sorted(used_after)
        if mid in by_id and not is_free(mid)
    }
    repriced = [
        mid
        for mid, price in pricing.items()
        if not _same_price(config.pricing.get(mid), Price(**price))
    ]
    clusters = {
        name: rank_cluster(ranking.eligible, free_eligible, score_fn, policy)
        for name, score_fn in CLUSTER_SCORES.items()
    }
    # Everything the catalogue has, recommended or not: the extended search lets the founder pick
    # a model the filters rejected, so each row carries why it is not in the ranking.
    all_models = []
    for m in sorted(models, key=lambda m: (-(m.quality or -1.0), m.id)):
        row = _model_summary(m, m.quality)
        reason = exclusion_reason(m, policy, today)
        row["eligible"] = reason is None
        row["excluded"] = REASONS.get(reason, reason) if reason else ""
        all_models.append(row)
    return Proposal(
        tiers={t: _dump(c) for t, c in tiers.items()},
        base={t: _dump(c) for t, c in config.models.tiers.items()},
        pricing=pricing,
        added=sorted(used_after - configured),
        removed=sorted(configured - used_after),
        gone=sorted(m for m in configured if m not in by_id),
        expiring=sorted(
            m
            for m in configured
            if m in by_id
            and by_id[m].expires
            and by_id[m].expires < today + timedelta(days=policy.expiry_margin_days)
        ),
        summary=summary,
        considered=ranking.total,
        eligible=len(ranking.eligible),
        excluded=dict(ranking.excluded),
        mode="pinned",
        repriced=repriced,
        targets={
            m: by_id[m].target_id for m in sorted(configured) if m in by_id and by_id[m].alias
        },
        clusters=clusters,
        all_models=all_models,
    )


def apply_proposal(config: LoompaConfig, proposal: Proposal) -> bool:
    """Write the proposed tiers, matrix and prices into `config`. False (and nothing written) when the
    tiers are no longer what the proposal was made from."""
    if _dump_all(config) != proposal.base:
        return False
    # Tiers first: assigning them mirrors one list into every cluster, which would undo a matrix
    # written before it. Assigning the matrix afterwards leaves the tiers alone.
    config.models.tiers = {
        t: [ModelCandidate.model_validate(c) for c in cands] for t, cands in proposal.tiers.items()
    }
    if proposal.clusters:
        matrix: dict[str, dict[str, list[ModelCandidate]]] = {}
        for cluster_name, tier_map in proposal.clusters.items():
            matrix[cluster_name] = {}
            for t_name, cands_list in tier_map.items():
                matrix[cluster_name][t_name] = [
                    ModelCandidate(
                        provider=PROVIDER,
                        model=m["id"] if isinstance(m, dict) else getattr(m, "id", str(m)),
                    )
                    for m in cands_list
                ]
        config.models.matrix = matrix
    for model_id, price in proposal.pricing.items():
        config.pricing[model_id] = Price.model_validate(price)
    return True
