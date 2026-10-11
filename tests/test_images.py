"""The Image cluster and the image API client (plan 15.12, 15.13; ADR-0040)."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from loompa.llm import image_catalog
from loompa.llm.images import OpenRouterImages, PlaceholderImages
from loompa.llm.providers import LLMError


def test_the_price_per_picture_reads_both_billing_units():
    assert (
        image_catalog.price_per_image(
            [
                {"billable": "output_image", "unit": "image", "cost_usd": 0.048, "variant": "1k"},
                {"billable": "output_image", "unit": "image", "cost_usd": 0.1, "variant": "2k"},
            ]
        )
        == 0.048
    )
    per_token = image_catalog.price_per_image(
        [
            {"billable": "output_image", "unit": "token", "cost_usd": 3e-05},
            {"billable": "input_text", "unit": "token", "cost_usd": 5e-06},
        ]
    )
    assert per_token == round(3e-05 * image_catalog.TOKENS_PER_IMAGE, 4)
    assert image_catalog.price_per_image([{"billable": "input_image", "unit": "image"}]) is None


def test_the_slots_rank_value_for_drafts_and_quality_under_the_cap():
    catalog = [
        {"id": "a/cheap-1", "name": "A: Cheap 1", "price": 0.02, "vector": False},
        {"id": "b/great-2", "name": "B: Great 2", "price": 0.12, "vector": False},
        {"id": "c/pricey", "name": "C: Pricey", "price": 0.6, "vector": False},
        {"id": "r/thing-vector", "name": "R: Vector", "price": 0.08, "vector": True},
        {"id": "u/unrated", "name": "U", "price": 0.01, "vector": False},
    ]
    ratings = {
        "generation": [
            {"slug": "cheap-1", "name": "Cheap 1", "elo": 1000},
            {"slug": "great-2", "name": "Great 2", "elo": 1200},
            {"slug": "pricey", "name": "Pricey", "elo": 1300},
            {"slug": "thing-vector", "elo": 1250},
        ],
        "editing": [{"slug": "great-2", "elo": 1100}],
    }
    out = image_catalog.rank(catalog, ratings, final_ceiling=0.15)
    assert [m["id"] for m in out["draft"]] == ["a/cheap-1", "b/great-2", "c/pricey"]
    assert [m["id"] for m in out["final"]] == ["b/great-2", "a/cheap-1"]
    assert out["final"][0]["edit_elo"] == 1100


async def test_the_image_api_is_called_with_references_and_metered_cost():
    png = base64.b64encode(b"\x89PNG fake").decode()
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        assert request.headers["Authorization"] == "Bearer k"
        return httpx.Response(
            200,
            json={
                "data": [{"b64_json": png, "media_type": "image/webp"}],
                "usage": {"cost": 0.018},
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        gen = OpenRouterImages("https://openrouter.ai/api/v1", "k", client=client)
        out = await gen.generate(
            "m/x",
            "a flat logo",
            references=["data:image/png;base64,AA"],
            params={"aspect_ratio": "1:1"},
        )
    assert out.data == b"\x89PNG fake" and out.suffix == ".webp" and out.cost_usd == 0.018
    assert seen["input_references"][0]["image_url"]["url"].startswith("data:image/png")
    assert seen["aspect_ratio"] == "1:1" and seen["model"] == "m/x"

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down")

    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse)) as client:
        with pytest.raises(LLMError) as err:
            await OpenRouterImages("https://x.test/api/v1", "k", client=client).generate("m", "p")
    assert err.value.retryable


async def test_the_placeholder_draws_offline():
    import io

    from PIL import Image

    out = await PlaceholderImages().generate("m", "a logo")
    assert Image.open(io.BytesIO(out.data)).size == (512, 512) and out.cost_usd == 0.0
