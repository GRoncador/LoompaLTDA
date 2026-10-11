"""The panel's growth endpoints (plan Phase 17): drafts the founder reviews, never published."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from loompa.growth import FORMATS, GrowthError, landing, make, pieces


class PieceBody(BaseModel):
    kind: str
    brief: str = ""
    networks: list[str] = []


def register(app: FastAPI, hub: Any) -> None:
    @app.get("/api/factories/{slug}/growth")
    def growth(slug: str) -> dict[str, Any]:
        ctx = hub.get(slug).ctx
        return {
            "pieces": pieces(ctx.root),
            "networks": [
                {"key": k, "label": v[2], "size": f"{v[0]}x{v[1]}"} for k, v in FORMATS.items()
            ],
        }

    @app.post("/api/factories/{slug}/growth/pieces")
    async def new_piece(slug: str, body: PieceBody) -> dict[str, Any]:
        ctx = hub.get(slug).ctx
        try:
            return await make(ctx, body.kind, body.brief, body.networks)
        except GrowthError as exc:
            raise HTTPException(400, str(exc)) from None

    @app.post("/api/factories/{slug}/growth/landing")
    async def landing_page(slug: str) -> dict[str, Any]:
        from loompa.conversations import ConversationError

        ctx = hub.get(slug).ctx
        try:
            return await landing(ctx)
        except (GrowthError, ConversationError) as exc:
            raise HTTPException(400, str(exc)) from None
