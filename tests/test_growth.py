"""Growth, first rung (plan Phase 17, ADR-0042): drafts the founder reviews, never published."""

from __future__ import annotations

import asyncio

import pytest

from loompa.comms import FounderAnswer
from loompa.company import filled
from loompa.engine import Scheduler
from loompa.engine.scheduler import SIDE_TASKS
from loompa.factory import Factory
from loompa.growth import GrowthError, growth_dir, landing, make, pieces
from test_engine import factory, make_ctx  # noqa: F401


async def test_growth_is_for_commercial_products(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    with pytest.raises(GrowthError, match="comerciais"):
        await make(ctx, "plan")


async def test_the_plan_becomes_a_company_document_only_when_approved(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    ctx.config.factory.kind = "commercial"
    piece = await make(ctx, "plan", "lançar para síndicos")
    assert piece["id"] == "G-001" and piece["status"] == "draft"
    assert not filled(factory.paths.loompa, "marketing")
    msg = ctx.store.get_message(piece["message_id"])
    assert "nada é enviado" in msg.impact.lower() and not msg.executive_audit()
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="approve"))
    assert "Canais" in filled(factory.paths.loompa, "marketing")
    assert pieces(factory.root)[0]["status"] == "approved"


async def test_posts_get_art_in_each_networks_format_and_an_adjustment_is_a_new_round(
    factory: Factory,
):
    from PIL import Image

    ctx = make_ctx(factory, dry_run=True)
    ctx.config.factory.kind = "commercial"
    piece = await make(ctx, "posts", "promoção de lançamento", ["instagram_story", "linkedin"])
    folder = growth_dir(factory.root) / piece["id"]
    sizes = {Image.open(folder / img).size for img in piece["images"]}
    assert sizes == {(1080, 1920), (1200, 627)}
    msg = ctx.store.get_message(piece["message_id"])
    assert len(msg.screens) == 2
    await Scheduler(ctx).aanswer(msg.id, FounderAnswer(option_key="adjust", text="mais alegre"))
    await asyncio.gather(*list(SIDE_TASKS))
    again = pieces(factory.root)[0]
    assert again["round"] == 2 and again["status"] == "draft"


async def test_emails_are_files_and_the_landing_is_a_story_through_the_po(factory: Factory):
    ctx = make_ctx(factory, dry_run=True)
    ctx.config.factory.kind = "commercial"
    piece = await make(ctx, "emails", "boas-vindas")
    text = (growth_dir(factory.root) / piece["id"] / "emails.md").read_text()
    assert "Assunto: Olá" in text
    out = await landing(ctx)
    assert out["story_id"] and ctx.store.get_story(out["story_id"])["stage"] == "BACKLOG"
