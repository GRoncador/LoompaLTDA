"""The Image cluster: which image model fills each slot (plan 15.13, ADR-0040).

Text models are ranked by text benchmarks and billed per token; image models make pictures, are
billed per picture, have their own arena rating and their own API. So they get a cluster of their
own, with two slots:

| slot  | what for                                         | score                              |
|-------|--------------------------------------------------|------------------------------------|
| draft | exploring cheaply (flat pictures, many options)  | generation ELO per dollar          |
| final | the approved piece at high quality               | generation ELO, under a price cap  |

Sources: OpenRouter's image catalog (`GET /images/models` and each model's `/endpoints`, with the
price per picture) and Artificial Analysis' text-to-image and image-editing arenas (ELO), with the
same key as the text ratings. The governance of ADR-0011/0025 holds: this is a suggestion, the
founder applies it in the settings, never in the middle of a sprint.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

import httpx

OPENROUTER = "https://openrouter.ai/api/v1"
AA_PATHS = {"generation": "/data/media/text-to-image", "editing": "/data/media/image-editing"}
TOKENS_PER_IMAGE = 1290  # a 1024x1024 picture billed per output token (OpenAI, Gemini)
CACHE_NAME = "image_catalog.json"
STALE_S = 7 * 24 * 3600


def price_per_image(pricing: list[dict[str, Any]]) -> float | None:
    """The cheapest output picture among a model's price lines (per picture, or per token times a
    1k picture's tokens). None when it bills in a way not listed here."""
    out = []
    for p in pricing or []:
        if p.get("billable") != "output_image" or not isinstance(p.get("cost_usd"), int | float):
            continue
        if p.get("unit") == "image":
            out.append(float(p["cost_usd"]))
        elif p.get("unit") == "token":
            out.append(float(p["cost_usd"]) * TOKENS_PER_IMAGE)
    return round(min(out), 4) if out else None


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def match_elo(model_id: str, name: str, ratings: list[dict[str, Any]]) -> float | None:
    """The arena ELO of an OpenRouter image model: its slug or name, letters and digits only."""
    keys = {_norm(model_id.split("/", 1)[-1]), _norm(name.split(":", 1)[-1])}
    for r in ratings:
        cand = {_norm(str(r.get("slug") or "")), _norm(str(r.get("name") or ""))}
        if keys & cand and isinstance(r.get("elo"), int | float):
            return float(r["elo"])
    return None


async def fetch_openrouter(client: httpx.AsyncClient, base: str = OPENROUTER) -> list[dict]:
    models = (await client.get(f"{base}/images/models")).json().get("data") or []

    async def one(m: dict) -> dict:
        try:
            ends = (await client.get(f"{base}/images/models/{m['id']}/endpoints")).json()
            prices = [price_per_image(e.get("pricing") or []) for e in ends.get("endpoints") or []]
            price = min((p for p in prices if p is not None), default=None)
        except (httpx.HTTPError, ValueError):
            price = None
        params = m.get("supported_parameters") or {}
        return {
            "id": m["id"],
            "name": m.get("name") or m["id"],
            "price": price,
            "references": int((params.get("input_references") or {}).get("max") or 0),
            "params": sorted(params),
            "vector": "vector" in m["id"] or "svg" in json.dumps(params.get("output_format", "")),
        }

    return list(await asyncio.gather(*(one(m) for m in models)))


async def fetch_ratings(client: httpx.AsyncClient, base: str, key: str) -> dict[str, list]:
    out: dict[str, list] = {}
    for kind, path in AA_PATHS.items():
        r = await client.get(base.rstrip("/") + path, headers={"x-api-key": key})
        r.raise_for_status()
        body = r.json()
        out[kind] = body.get("data") if isinstance(body, dict) else body
    return out


def rank(
    catalog: list[dict[str, Any]], ratings: dict[str, list], *, final_ceiling: float
) -> dict[str, list[dict[str, Any]]]:
    """The candidates of each slot, best first. A model with no price or no rating is left out:
    a suggestion is only as good as the numbers it stands on."""
    rows = []
    for m in catalog:
        if m.get("vector"):
            continue  # vectorising is a step in code; the vector model is its configured reserve
        elo = match_elo(m["id"], m["name"], ratings.get("generation") or [])
        if elo is None or not m.get("price"):
            continue
        edit = match_elo(m["id"], m["name"], ratings.get("editing") or [])
        rows.append({**m, "elo": elo, "edit_elo": edit, "value": round(elo / m["price"], 1)})
    draft = sorted(rows, key=lambda r: -r["value"])[:8]
    final = sorted((r for r in rows if r["price"] <= final_ceiling), key=lambda r: -r["elo"])[:8]
    return {"draft": draft, "final": final}


def cache_path(home: Path) -> Path:
    return home / CACHE_NAME


def load(home: Path) -> dict[str, Any]:
    try:
        return json.loads(cache_path(home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


async def refresh(
    home: Path,
    *,
    aa_key: str,
    aa_base: str,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    own = client is None
    client = client or httpx.AsyncClient(timeout=60)
    try:
        catalog = await fetch_openrouter(client)
        ratings = await fetch_ratings(client, aa_base, aa_key) if aa_key else {}
    finally:
        if own:
            await client.aclose()
    saved = {"fetched_at": time.time(), "catalog": catalog, "ratings": ratings}
    cache_path(home).parent.mkdir(parents=True, exist_ok=True)
    cache_path(home).write_text(json.dumps(saved), encoding="utf-8")
    return saved
