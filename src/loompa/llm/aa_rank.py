"""Model suggestion from Artificial Analysis alone (plan 9.4, revised with the Founder on 04/10).

The ranking reads every model Artificial Analysis rates, with no regard for which keys the factory
has or whether OpenRouter lists it: finding a route and setting the key is the founder's job. An
AA entry is a model *at an effort* ("GLM-5.3 (Low)", "Claude Opus 5.5 (Max)"), and its rating is
only true at that effort, so every pick is a (model, effort) pair.

* **Composite per cluster.** Each cluster weighs the benchmarks that measure what its roles do
  (`PROFILES`). A benchmark is scaled against the best value any model scored (best = 100), so
  benchmarks of very different difficulty (HLE median 0.10, GPQA 0.71) weigh as intended. A
  missing benchmark redistributes its weight over the present ones; below `MIN_COVERAGE` of the
  weight the entry is left out and counted, never ranked as if it scored 0 (ADR-0011).
* **Tiers.** Tier 1: the best composites whose blended price is within the ceiling. Tier 2: at
  least `floor` (75%) of tier 1's best, ranked by composite per dollar. Tier 3: the best rated
  models some provider offers free (from the OpenRouter catalogue cache, the only provider data
  used, and only to say "free at X"). One entry per base model in each list: its best effort.
* **Price 0 is unknown, not free.** AA gives 0 for 251 entries, paid ones among them; those stay
  out of the ceiling and the cost-benefit and are listed apart.

Only the founder applies a suggestion (ADR-0011). Attribution to Artificial Analysis is shown.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx

URL_PATH = "/data/llms/models"
CACHE_FILE = "artificial_analysis.json"
STALE_S = 7 * 24 * 3600
MIN_COVERAGE = 0.6
PICKS = 5

# What each cluster's roles do, as AA benchmarks (weights sum to 1), decided with the Founder on
# 2026-10-04. Only the suite AA runs on new models: since 2026-08 a release gets HLE, LCR, SciCode
# and Terminal-Bench v4.0 (tau-banking and GPQA for about half), and none of IFBench, tau2,
# LiveCodeBench or Terminal-Bench Hard; a profile on the old suite ranked April models above their
# September successors. Measured on models since 2026-06: Terminal-Bench v4.0 is the most
# independent signal (0.42 with LCR, 0.62 with SciCode); HLE tracks the intelligence index (0.94);
# GPQA tracks LCR (0.91) and is left out as a double count; AA's coding index is Terminal-Bench
# v2.1 (1.0) and is not mixed with its parts.
PROFILES: dict[str, dict[str, float]] = {
    # Worker: an agent in a terminal (the most independent signal), writing correct code, a tool
    # history that grows to 200k characters; Inspector: judging against criteria with tools.
    "engineering": {"terminalbench_v4_0": 0.45, "scicode": 0.25, "lcr": 0.15, "tau_banking": 0.15},
    # Master, Architect, Spec, Product Owner, Analyst: broad reasoning, reading a spec, the repo and
    # the conversation, keeping to rules over many turns (No Invention, meeting ops), repo tools.
    "strategy": {"hle": 0.35, "lcr": 0.30, "tau_banking": 0.20, "terminalbench_v4_0": 0.15},
    # Deployer and support, classify, wording: short, format-bound calls; the price decides more.
    "routine": {"tau_banking": 0.35, "hle": 0.25, "lcr": 0.20, "scicode": 0.20},
}
# The single cluster mixes the three by the factory's own work: each cluster's share of the calls
# in the last 30 days (`work_mix`), this default without data (contas: Worker and Inspector ~78% of
# the cost). Not the intelligence index alone: that is AA's mix, heavier on knowledge and science.
DEFAULT_MIX = {"engineering": 0.65, "strategy": 0.30, "routine": 0.05}
PROFILE_LABEL = {
    "strategy": "Estratégia & Produto (Master, Architect, Spec, PO, Analyst)",
    "engineering": "Engenharia (Worker, Inspector)",
    "routine": "Rotina (Deployer e apoio)",
    "general": "Geral (todos os Loompas, pesado pelo trabalho da fábrica)",
}


def general_weights(mix: dict[str, float] | None = None) -> dict[str, float]:
    """The single cluster's weights: each cluster's profile times its share of the work."""
    mix = {k: v for k, v in (mix or DEFAULT_MIX).items() if k in PROFILES and v > 0}
    total = sum(mix.values()) or 1.0
    out: dict[str, float] = {}
    for cluster, share in mix.items():
        for bench, w in PROFILES[cluster].items():
            out[bench] = out.get(bench, 0.0) + w * share / total
    return {k: round(v, 4) for k, v in out.items()}


def work_mix(store: Any, factory: str, config: Any, days: int = 30) -> dict[str, float] | None:
    """Each cluster's share of the factory's model calls (None without enough calls)."""
    from datetime import UTC, datetime, timedelta

    from loompa.config.schema import CLUSTER_ROLES

    end = datetime.now(UTC)
    rows = store.usage_window(factory, (end - timedelta(days=days)).isoformat(), end.isoformat())
    of = {role: cluster for cluster, roles in CLUSTER_ROLES.items() for role in roles}
    count: dict[str, int] = {}
    for r in rows:
        cluster = of.get(r["role"])
        if cluster in PROFILES:
            count[cluster] = count.get(cluster, 0) + 1
    total = sum(count.values())
    if total < 100:
        return None
    return {k: round(v / total, 3) for k, v in sorted(count.items())}


# AA effort tags -> what the Loompa sends (`ModelCandidate.reasoning_effort`); "" = provider default,
# "none" = thinking off. Xhigh has no Loompa label: it maps to max, the closest above high.
EFFORTS = {
    "minimal": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "max",
    "max": "max",
    "non-reasoning": "none",
    "reasoning": "",
}
_TAG = re.compile(r"\(([^)]*)\)")
_SLUG_EFFORT = re.compile(
    r"-(minimal|low|medium|high|xhigh|max|adaptive|non-reasoning(?:-[a-z-]+)?|reasoning)$"
)


@dataclass
class Entry:
    aa_id: str  # creator/slug
    name: str
    creator: str
    base: str  # creator/slug without the effort suffix: one per list
    effort_tag: str  # as AA names it ("Low", "Max", "Non-reasoning", "" when not said)
    effort: str  # what the Loompa would send
    price: float | None  # blended 3:1 USD per 1M tokens; None when AA gives 0 or nothing
    release: str = ""
    speed: float | None = None  # output tokens per second
    ttft: float | None = None  # seconds to the first token
    bench: dict[str, float] = field(default_factory=dict)

    def composite(self, profile: dict[str, float], best: dict[str, float]) -> tuple[float, float]:
        """(score 0-100, weight covered 0-1)."""
        have = {k: w for k, w in profile.items() if k in self.bench and best.get(k)}
        covered = sum(have.values())
        if not covered:
            return 0.0, 0.0
        score = sum(w * self.bench[k] / best[k] * 100 for k, w in have.items()) / covered
        return round(score, 1), round(covered, 2)


def _effort(name: str, slug: str) -> str:
    for tag in _TAG.findall(name):
        for part in tag.split(","):
            key = part.strip().lower()
            if key in EFFORTS:
                return part.strip()
    return ""


def parse(raw: list[dict[str, Any]]) -> list[Entry]:
    out = []
    for m in raw:
        if not isinstance(m, dict) or not m.get("slug"):
            continue
        creator = str((m.get("model_creator") or {}).get("slug") or "")
        slug = str(m["slug"])
        price = (m.get("pricing") or {}).get("price_1m_blended_3_to_1")
        tag = _effort(str(m.get("name") or ""), slug)
        bench = {
            k: float(v)
            for k, v in (m.get("evaluations") or {}).items()
            if isinstance(v, int | float) and not isinstance(v, bool)
        }
        out.append(
            Entry(
                aa_id=f"{creator}/{slug}",
                name=str(m.get("name") or slug),
                creator=creator,
                base=f"{creator}/{_SLUG_EFFORT.sub('', slug)}",
                effort_tag=tag,
                effort=EFFORTS.get(tag.lower(), ""),
                price=float(price) if isinstance(price, int | float) and price > 0 else None,
                release=str(m.get("release_date") or ""),
                speed=m.get("median_output_tokens_per_second") or None,
                ttft=m.get("median_time_to_first_token_seconds") or None,
                bench=bench,
            )
        )
    return out


# --------------------------------------------------------------------- cache
def _cache_path(cache_dir: Path | None) -> Path:
    from loompa.llm.modelsdev import hub_cache_dir

    return (cache_dir or hub_cache_dir()) / CACHE_FILE


def load(cache_dir: Path | None = None) -> dict[str, Any]:
    try:
        return json.loads(_cache_path(cache_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


async def refresh(
    key: str,
    base_url: str,
    *,
    cache_dir: Path | None = None,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """One call to the free API (it returns every model); the old cache stays on failure."""
    own = client is None
    client = client or httpx.AsyncClient()
    try:
        resp = await client.get(
            base_url.rstrip("/") + URL_PATH, headers={"x-api-key": key}, timeout=60.0
        )
        resp.raise_for_status()
        body = resp.json()
    finally:
        if own:
            await client.aclose()
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        raise ValueError("Artificial Analysis: unexpected shape")
    saved = {"fetched_at": time.time(), "data": data}
    path = _cache_path(cache_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(saved), encoding="utf-8")
    return saved


def stale(cache_dir: Path | None = None) -> bool:
    fetched = load(cache_dir).get("fetched_at")
    return not isinstance(fetched, int | float) or time.time() - fetched > STALE_S


# ------------------------------------------------------------------- ranking
@dataclass
class Pick:
    aa_id: str
    name: str
    creator: str
    effort_tag: str
    effort: str
    score: float
    coverage: float
    price: float | None
    value: float | None = None  # score per dollar (tier 2)
    speed: float | None = None
    ttft: float | None = None
    free_at: list[str] = field(default_factory=list)  # tier 3: where it is offered free

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Ranking:
    profile: str
    tier1: list[Pick]
    tier2: list[Pick]
    tier3: list[Pick]
    floor: float  # the tier-2 minimum score
    considered: int
    partial: int  # left out: less than MIN_COVERAGE of the profile's weight measured
    unpriced: list[Pick]  # rated well but AA has no price: outside ceiling and cost-benefit
    weights: dict[str, float] = field(default_factory=dict)
    mix: dict[str, float] = field(default_factory=dict)  # the single cluster's work mix

    def as_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "label": PROFILE_LABEL.get(self.profile, self.profile),
            "weights": self.weights,
            "tier1": [p.as_dict() for p in self.tier1],
            "tier2": [p.as_dict() for p in self.tier2],
            "tier3": [p.as_dict() for p in self.tier3],
            "floor": self.floor,
            "considered": self.considered,
            "partial": self.partial,
            "unpriced": [p.as_dict() for p in self.unpriced],
            "mix": self.mix,
        }


def _best_per_base(scored: list[tuple[Entry, float, float]]) -> list[tuple[Entry, float, float]]:
    seen: set[str] = set()
    out = []
    for e, s, c in sorted(scored, key=lambda x: (-x[1], x[0].aa_id)):
        if e.base not in seen:
            seen.add(e.base)
            out.append((e, s, c))
    return out


def _pick(e: Entry, s: float, c: float, **extra: Any) -> Pick:
    return Pick(
        aa_id=e.aa_id,
        name=e.name,
        creator=e.creator,
        effort_tag=e.effort_tag,
        effort=e.effort,
        score=s,
        coverage=c,
        price=e.price,
        speed=e.speed,
        ttft=e.ttft,
        **extra,
    )


def rank(
    entries: list[Entry],
    profile: str,
    *,
    ceiling: float = 1.25,
    floor_share: float = 0.75,
    free: dict[str, list[str]] | None = None,
    picks: int = PICKS,
    mix: dict[str, float] | None = None,
) -> Ranking:
    """`free`: AA id -> where it is offered free (see `free_offers`)."""
    weights = PROFILES[profile] if profile in PROFILES else general_weights(mix)
    best = {k: max((e.bench[k] for e in entries if k in e.bench), default=0.0) for k in weights}
    scored, partial = [], 0
    for e in entries:
        s, c = e.composite(weights, best)
        if c < MIN_COVERAGE:
            partial += 1  # measured on too little of this profile (or the retired suite)
            continue
        scored.append((e, s, c))
    priced = [x for x in scored if x[0].price is not None]
    t1 = _best_per_base([x for x in priced if x[0].price <= ceiling])[:picks]
    top = t1[0][1] if t1 else 0.0
    floor = round(top * floor_share, 1)
    # each model at its best-value effort above the floor (a lower effort may be the better buy)
    pool = [x for x in priced if x[0].price <= ceiling and x[1] >= floor]
    seen: set[str] = set()
    t2 = []
    for x in sorted(pool, key=lambda x: (-(x[1] / x[0].price), x[0].aa_id)):
        if x[0].base not in seen:
            seen.add(x[0].base)
            t2.append(x)
    t2 = t2[:picks]
    free = free or {}
    t3 = _best_per_base([x for x in scored if x[0].aa_id in free])[:picks]
    unpriced = _best_per_base([x for x in scored if x[0].price is None and x[1] >= floor])[:picks]
    return Ranking(
        profile=profile,
        tier1=[_pick(e, s, c) for e, s, c in t1],
        tier2=[_pick(e, s, c, value=round(s / e.price, 1)) for e, s, c in t2],
        tier3=[_pick(e, s, c, free_at=free[e.aa_id]) for e, s, c in t3],
        floor=floor,
        considered=len(entries),
        partial=partial,
        unpriced=[_pick(e, s, c) for e, s, c in unpriced],
        weights=weights,
        mix=(mix or DEFAULT_MIX) if profile not in PROFILES else {},
    )


# Creator names that differ between OpenRouter ids and AA's (explicit, checked on 2026-10-04).
OPENROUTER_CREATORS = {
    "z-ai": "zai",
    "qwen": "alibaba",
    "thinkingmachines": "thinking-machines",
    "x-ai": "xai",
    "meta-llama": "meta",
    "mistralai": "mistral",
}


def free_offers(entries: list[Entry], catalog: list[dict[str, Any]]) -> dict[str, list[str]]:
    """AA ids offered free on OpenRouter (`:free` ids of the cached catalogue). The only provider
    data the ranking reads, and only for tier 3. The id is translated by one explicit rule (creator
    table, dots to hyphens), exact or with the creator repeated; nothing else is guessed."""
    by_id = {e.aa_id for e in entries}
    out: dict[str, list[str]] = {}
    for item in catalog:
        oid = str(item.get("id") or "")
        if not oid.endswith(":free"):
            continue
        vendor, _, slug = oid.removesuffix(":free").partition("/")
        vendor = OPENROUTER_CREATORS.get(vendor, vendor)
        slug = slug.replace(".", "-")
        for cand in (f"{vendor}/{slug}", f"{vendor}/{vendor}-{slug}"):
            if cand in by_id:
                out.setdefault(cand, []).append(f"OpenRouter ({oid})")
                break
    return out


def suggest(
    config: Any,
    cache_dir: Path | None = None,
    catalog: list[dict[str, Any]] | None = None,
    *,
    mix: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Every profile's ranking from the cached AA list (no network)."""
    cached = load(cache_dir)
    entries = parse(cached.get("data") or [])
    if catalog is None:
        from loompa.llm.catalog import CATALOG_CACHE_DIR, CATALOG_CACHE_FILE

        try:
            catalog = json.loads((CATALOG_CACHE_DIR / CATALOG_CACHE_FILE).read_text())["data"]
        except (OSError, ValueError, KeyError, TypeError):
            catalog = []
    free = free_offers(entries, catalog)
    ceiling = config.models.tier1_ceiling
    floor = config.models.tier2_floor
    profiles = (
        ["engineering", "strategy", "routine"] if config.models.clusters_enabled else ["general"]
    )
    return {
        "fetched_at": cached.get("fetched_at"),
        "entries": len(entries),
        "ceiling": ceiling,
        "floor_share": floor,
        "attribution": "Notas: Artificial Analysis (artificialanalysis.ai)",
        # AA prices per token; a higher effort spends more reasoning tokens, which AA does not give
        "price_note": "O preço é por token: um esforço mais alto gasta mais tokens de raciocínio, "
        "e a Artificial Analysis não informa quantos. O custo-benefício de uma variante Max é otimista.",
        "profiles": [
            rank(entries, p, ceiling=ceiling, floor_share=floor, free=free, mix=mix).as_dict()
            for p in profiles
        ],
    }
