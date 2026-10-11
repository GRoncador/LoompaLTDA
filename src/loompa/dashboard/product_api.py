"""The panel's product endpoints: phases, epics and the phase's progress (plan 16.3-16.7)."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from loompa.factory_kind import KIND_LABEL, profile
from loompa.lifecycle import Lifecycle


class PhaseBody(BaseModel):
    goal: str | None = None
    exit_criterion: str | None = None
    facts: str | None = None
    measure: str | None = None


class EpicBody(BaseModel):
    status: str


def product_view(ctx: Any) -> dict[str, Any]:
    lc = Lifecycle(ctx)
    phases = []
    for p in lc.phases():
        prog = lc.progress(p["key"])
        phases.append(
            {**p, "percent": prog.percent if prog else 0, "cards": prog.total if prog else 0}
        )
    stories = ctx.store.list_stories(ctx.slug)
    epics = []
    for e in lc.epics():
        mine = [s for s in stories if s.get("epic_id") == e["id"] and s["stage"] != "CANCELLED"]
        done = sum(1 for s in mine if s["stage"] == "DONE")
        epics.append({**e, "cards": len(mine), "done": done})
    return {
        "kind": ctx.config.factory.kind,
        "kind_label": KIND_LABEL[ctx.config.factory.kind],
        "discovery": profile(ctx.config).market_discovery,
        "phases": phases,
        "epics": epics,
    }


def phase_chip(ctx: Any) -> dict[str, Any] | None:
    """ "MVP: 60%" for the board's header (16.7)."""
    lc = Lifecycle(ctx)
    prog = lc.progress()
    if prog is None:
        return None
    return {"key": prog.phase, "name": prog.name, "percent": prog.percent, "cards": prog.total}


def register(app: FastAPI, hub: Any) -> None:
    @app.get("/api/factories/{slug}/product")
    def product(slug: str) -> dict[str, Any]:
        return product_view(hub.get(slug).ctx)

    @app.put("/api/factories/{slug}/product/phases/{key}")
    async def set_phase(slug: str, key: str, body: PhaseBody) -> dict[str, Any]:
        from loompa.agents.product_manager import MetricsMeasure

        ctx = hub.get(slug).ctx
        lc = Lifecycle(ctx)
        before = next((p for p in lc.phases() if p["key"] == key), None)
        if before is None:
            raise HTTPException(404, key)
        fields = {k: v for k, v in body.model_dump().items() if v is not None}
        lc.set_phase(key, **fields)
        crit = fields.get("exit_criterion")
        if crit and crit.strip() != before["exit_criterion"] and "measure" not in fields:
            try:  # the Metrics Loompa proposes how to measure it (16.3)
                measure = await MetricsMeasure(ctx).measure(before["name"], crit)
            except Exception:  # noqa: BLE001 - the criterion stands without a proposal
                measure = ""
            if measure:
                lc.set_phase(key, measure=measure)
        return product_view(ctx)

    @app.post("/api/factories/{slug}/product/epics/propose")
    async def propose_epics(slug: str) -> dict[str, Any]:
        from loompa.agents.product_manager import ProductManagerAgent

        ctx = hub.get(slug).ctx
        try:
            proposals = await ProductManagerAgent(ctx).propose_epics()
        except Exception:  # noqa: BLE001
            raise HTTPException(502, "O Product Manager não conseguiu responder agora.") from None
        msg = Lifecycle(ctx).ask_epics(proposals)
        return {"proposed": len(proposals), "message_id": msg.id if msg else None}

    @app.put("/api/factories/{slug}/product/epics/{epic_id}")
    def epic_status(slug: str, epic_id: str, body: EpicBody) -> dict[str, Any]:
        ctx = hub.get(slug).ctx
        try:
            Lifecycle(ctx).set_epic_status(epic_id, body.status)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return product_view(ctx)

    @app.post("/api/factories/{slug}/product/review")
    async def review_phase(slug: str) -> dict[str, Any]:
        from loompa.agents.product_manager import ProductManagerAgent

        ctx = hub.get(slug).ctx
        lc = Lifecycle(ctx)
        try:
            review = await ProductManagerAgent(ctx).review_phase()
        except Exception:  # noqa: BLE001
            raise HTTPException(502, "O Product Manager não conseguiu responder agora.") from None
        asked = (
            lc.propose_move(review["evidence"], review["missing"])
            if review["exit_met"] == "yes"
            else None
        )
        return {**review, "asked": bool(asked)}

    @app.post("/api/factories/{slug}/product/phases/{key}/start")
    def start_phase(slug: str, key: str) -> dict[str, Any]:
        """The founder moves the product to a phase from the panel: the decision is theirs."""
        ctx = hub.get(slug).ctx
        try:
            Lifecycle(ctx).move_to(key)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        return product_view(ctx)
