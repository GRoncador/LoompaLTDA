"""Image generation through OpenRouter's image API (plan 15.12, ADR-0040).

`POST {base}/images` with `model`, `prompt` and, for the models that take them, reference pictures
(`input_references`, data URLs) and their own parameters (`aspect_ratio`, `resolution`, `seed`,
`background`, `output_format` … as the model's record in `GET /images/models` lists them). The
answer carries the pictures as base64 (`data[].b64_json`, `media_type`) and what the call cost
(`usage.cost`). Shapes read from OpenRouter's documentation and its public catalog, 11/10/2026.

Only the Designer Loompa generates (its tool profile has `generate_image`, checked when the call
is made). Every picture is metered like a model call: it counts in the story's cap and the
period's budget. With no key, in the dry run and in tests, a local placeholder takes the place of
the picture: a flat image with a label, so the flow runs without the network.
"""

from __future__ import annotations

import base64
import io
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from loompa.llm.providers import LLMError

IMAGE_TOOL = "generate_image"  # the capability in a role's tool profile


@dataclass
class GeneratedImage:
    data: bytes
    media_type: str
    model: str
    cost_usd: float | None = None
    seed: int | None = None
    prompt: str = ""
    duration_ms: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def suffix(self) -> str:
        return {"image/jpeg": ".jpg", "image/webp": ".webp", "image/svg+xml": ".svg"}.get(
            self.media_type, ".png"
        )


class ImageGenerator:
    name = "images"

    async def generate(
        self,
        model: str,
        prompt: str,
        *,
        references: list[str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> GeneratedImage:
        raise NotImplementedError


class OpenRouterImages(ImageGenerator):
    name = "openrouter"

    def __init__(
        self, base_url: str, api_key: str, *, client: httpx.AsyncClient | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.client = client

    async def generate(self, model, prompt, *, references=None, params=None) -> GeneratedImage:
        body: dict[str, Any] = {"model": model, "prompt": prompt, **(params or {})}
        if references:
            body["input_references"] = [
                {"type": "image_url", "image_url": {"url": u}} for u in references
            ]
        own = self.client is None
        client = self.client or httpx.AsyncClient(timeout=180)
        started = time.monotonic()
        try:
            r = await client.post(
                f"{self.base_url}/images",
                json=body,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        except httpx.HTTPError as exc:
            raise LLMError(f"image API unreachable: {exc}", retryable=True) from exc
        finally:
            if own:
                await client.aclose()
        if r.status_code >= 400:
            raise LLMError(
                f"image API answered {r.status_code}: {r.text[:300]}",
                retryable=r.status_code in (408, 429) or r.status_code >= 500,
            )
        data = r.json()
        items = data.get("data") or []
        if not items or not items[0].get("b64_json"):
            raise LLMError("image API returned no picture")
        first = items[0]
        cost = (data.get("usage") or {}).get("cost")
        return GeneratedImage(
            data=base64.b64decode(first["b64_json"]),
            media_type=str(first.get("media_type") or "image/png"),
            model=model,
            cost_usd=float(cost) if isinstance(cost, int | float) else None,
            seed=(params or {}).get("seed"),
            prompt=prompt,
            duration_ms=int((time.monotonic() - started) * 1000),
        )


class PlaceholderImages(ImageGenerator):
    """A flat picture with the prompt's first words: the dry run's stand-in, free and offline."""

    name = "placeholder"

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate(self, model, prompt, *, references=None, params=None) -> GeneratedImage:
        from PIL import Image, ImageDraw

        self.calls.append({"model": model, "prompt": prompt, "references": len(references or [])})
        n = len(self.calls)
        palette = ((37, 99, 235), (219, 39, 119), (5, 150, 105), (217, 119, 6))
        img = Image.new("RGBA", (512, 512), (255, 255, 255, 0))
        draw = ImageDraw.Draw(img)
        draw.ellipse((96, 96, 416, 416), fill=(*palette[n % len(palette)], 255))
        draw.text((24, 470), f"simulação {n}: {prompt[:40]}", fill=(30, 30, 30, 255))
        out = io.BytesIO()
        img.save(out, format="PNG")
        return GeneratedImage(out.getvalue(), "image/png", model, 0.0, prompt=prompt)


def image_generator(ctx: Any) -> ImageGenerator:
    """OpenRouter when its key is set and the engine is live; the placeholder otherwise."""
    cached = getattr(ctx, "_images", None)
    if cached is not None:
        return cached
    provider = ctx.config.providers.get("openrouter")
    key = ctx.secrets.get(provider.api_key_env) if provider and provider.api_key_env else None
    gen: ImageGenerator
    if ctx.dry_run or not provider or not key:
        gen = PlaceholderImages()
    else:
        gen = OpenRouterImages(provider.base_url or "https://openrouter.ai/api/v1", key)
    ctx._images = gen
    return gen


def data_url(data: bytes, media_type: str = "image/png") -> str:
    return f"data:{media_type};base64,{base64.b64encode(data).decode()}"
