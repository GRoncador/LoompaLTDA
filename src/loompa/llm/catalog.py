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

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, timedelta
import json
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

# Why a model is out of the ranking, in the order the checks run. `unrated` is last on purpose:
# it counts only models that would otherwise have qualified.
REASONS = {
    "alias": "apelido (-latest); esta fábrica usa ids fixos",
    "pinned": "id fixo; esta fábrica usa apelidos -latest",
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

    Caches the parsed payload locally in ~/.loompa/cache/ by month (YYYY_MM) so opening the
    dashboard or switching models does not make repeated remote requests."""
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
    month_str = date.today().strftime("%Y_%m")
    cache_file = target_dir / f"openrouter_catalog_{month_str}.json"

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
            for fallback_file in sorted(target_dir.glob("openrouter_catalog_*.json"), reverse=True):
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


# ----------------------------------------------------------------------------- ranking


@dataclass(frozen=True)
class Policy:
    """The knobs of the ranking. Defaults follow the real catalogue: at a $5 ceiling the best
    tier1 models are the ones the OpenRouter preset already uses, and at 80% of the best index
    tier2 keeps GLM 5.3 Flash, the model validated live."""

    tier1_ceiling: float = 5.0  # blended USD per 1M tokens the reasoning tier may cost
    tier2_floor: float = 0.80  # fraction of the best quality the execution tier must reach
    picks: int = 3  # candidates per tier (one per vendor, so a fallback is not the same outage)
    min_context: int = 128_000
    expiry_margin_days: int = 60
    ids: str = "auto"  # alias | pinned | auto (follow what the factory already uses)


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


def exclusion_reason(
    m: CatalogModel, policy: Policy, today: date, mode: str = "pinned"
) -> str | None:
    if m.alias != (mode == "alias"):
        return "pinned" if mode == "alias" else "alias"
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


def rank(models: list[CatalogModel], policy: Policy, today: date, mode: str = "pinned") -> Ranking:
    """tier1 is "best quality under a price ceiling"; tier2 is "cheapest above a quality floor".
    A plain quality/price ratio would put the cheapest acceptable model first in both."""
    excluded: Counter[str] = Counter()
    eligible: list[CatalogModel] = []
    for m in models:
        reason = exclusion_reason(m, policy, today, mode)
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
    mode: str = "pinned"  # alias | pinned: which kind of ids the ranking was made from
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


def _model_summary(m: CatalogModel, score: float | None) -> dict[str, Any]:
    return {
        "id": m.id,
        "name": m.label,
        "vendor": m.vendor,
        "quality": round(m.quality or 0.0, 1),
        "price": round(m.blended or 0.0, 2),
        "coding": round(m.coding, 1) if m.coding is not None else None,
        "agentic": round(m.agentic, 1) if m.agentic is not None else None,
        "intelligence": round(m.intelligence, 1) if m.intelligence is not None else None,
        "score": score,
    }


def rank_cluster(
    eligible: list[CatalogModel],
    score_fn: Callable[[CatalogModel], float | None],
    policy: Policy,
) -> dict[str, list[dict[str, Any]]]:
    scored = [(m, score_fn(m) or 0.0) for m in eligible]
    best_score = max((s for _, s in scored), default=0.0)

    # Tier 1: highest score under ceiling
    t1_candidates = [m for m, _ in scored if (m.blended or 0.0) <= policy.tier1_ceiling]
    t1_picks = _one_per_vendor(
        sorted(t1_candidates, key=lambda m: (-(score_fn(m) or 0.0), m.blended or 0.0, m.id)),
        policy.picks,
    )

    # Tier 2: lowest cost above floor
    t2_candidates = [m for m, s in scored if s >= policy.tier2_floor * best_score]
    t2_picks = _one_per_vendor(
        sorted(t2_candidates, key=lambda m: (m.blended or 0.0, -(score_fn(m) or 0.0), m.id)),
        policy.picks,
    )

    # Tier 3: highest score among free models
    t3_candidates = [
        m for m, _ in scored if is_free(m.id) or (m.blended is not None and m.blended == 0.0)
    ]
    t3_picks = _one_per_vendor(
        sorted(t3_candidates, key=lambda m: (-(score_fn(m) or 0.0), m.id)),
        policy.picks,
    )

    return {
        "tier1": [_model_summary(m, score_fn(m)) for m in t1_picks],
        "tier2": [_model_summary(m, score_fn(m)) for m in t2_picks],
        "tier3": [_model_summary(m, score_fn(m)) for m in t3_picks],
    }


def detect_mode(config: LoompaConfig) -> str:
    """A factory whose OpenRouter candidates are `~vendor/x-latest` aliases keeps using aliases."""
    aliased = any(
        c.model.startswith("~")
        for cands in config.models.tiers.values()
        for c in cands
        if c.provider == PROVIDER
    )
    return "alias" if aliased else "pinned"


def _same_price(a: Price | None, b: Price) -> bool:
    return a is not None and all(
        abs(x - y) <= 1e-6
        for x, y in zip(a.model_dump().values(), b.model_dump().values(), strict=True)
    )


def build_proposal(
    config: LoompaConfig, models: list[CatalogModel], policy: Policy, today: date
) -> Proposal:
    mode = detect_mode(config) if policy.ids == "auto" else policy.ids
    ranking = rank(models, policy, today, mode)
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
            {
                "id": m.id,
                "name": m.label,
                "vendor": m.vendor,
                "quality": round(m.quality or 0.0, 1),
                "price": round(m.blended or 0.0, 2),
                "coding": round(m.coding, 1) if m.coding is not None else None,
                "agentic": round(m.agentic, 1) if m.agentic is not None else None,
                "intelligence": round(m.intelligence, 1) if m.intelligence is not None else None,
                "score": m.score_tier1() if tier == "tier1" else m.score_tier2(),
            }
            for m in picked[tier]
        ]

    free_cands = [m for m in ranking.eligible if is_free(m.id) or (m.blended is not None and m.blended == 0.0)]
    if free_cands:
        summary["tier3_free"] = [
            {
                "id": m.id,
                "name": m.label,
                "vendor": m.vendor,
                "quality": round(m.quality or 0.0, 1),
                "price": 0.0,
                "coding": round(m.coding, 1) if m.coding is not None else None,
                "agentic": round(m.agentic, 1) if m.agentic is not None else None,
                "intelligence": round(m.intelligence, 1) if m.intelligence is not None else None,
                "score": m.score_routine(),
            }
            for m in sorted(free_cands, key=lambda m: (-(m.quality or 0.0), m.id))[: policy.picks]
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
        "strategy": rank_cluster(ranking.eligible, lambda m: m.score_tier1(), policy),
        "engineering": rank_cluster(ranking.eligible, lambda m: m.score_tier2(), policy),
        "routine": rank_cluster(ranking.eligible, lambda m: m.score_routine(), policy),
    }
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
        mode=mode,
        repriced=repriced,
        targets={
            m: by_id[m].target_id for m in sorted(configured) if m in by_id and by_id[m].alias
        },
        clusters=clusters,
        all_models=[
            _model_summary(m, m.quality)
            for m in sorted(ranking.eligible, key=lambda m: (-(m.quality or 0.0), m.id))
        ],
    )


def apply_proposal(config: LoompaConfig, proposal: Proposal) -> bool:
    """Write the proposed tiers and prices into `config`. False (and nothing written) when the
    tiers are no longer what the proposal was made from."""
    if _dump_all(config) != proposal.base:
        return False
    config.models.tiers = {
        t: [ModelCandidate.model_validate(c) for c in cands] for t, cands in proposal.tiers.items()
    }
    for model_id, price in proposal.pricing.items():
        config.pricing[model_id] = Price.model_validate(price)
    return True
