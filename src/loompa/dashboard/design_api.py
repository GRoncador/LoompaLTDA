"""The panel's design endpoints (plan 15.2, 15.5, 15.9; ADR-0039).

* the files of `.loompa/design/` (pictures and the static screens), and nothing outside it;
* the founder's references: a picture, a site (photographed in the headless browser), a Figma
  frame (read through its REST API) or words, for the product or for one story;
* the design view: the visual base, the approvals waiting, and each story's rounds.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from loompa.design import (
    DesignPaths,
    load_tokens,
    read_json,
    rounds,
    screen_files,
)
from loompa.design.references import (
    add_reference,
    list_references,
    memory_text,
    remove_reference,
    set_picture,
)

if TYPE_CHECKING:
    from loompa.engine.context import EngineContext

MEDIA = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp", ".gif": "image/gif"}
# a static screen opens with no scripts and no network: it is a picture the founder can scroll
SCREEN_CSP = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:"
STORY_ID = re.compile(r"S-\d+[A-Z]?")


def _screens(ctx: EngineContext, folder, data: dict[str, Any]) -> list[dict[str, Any]]:
    root = DesignPaths.of(ctx.root).root
    out = []
    for f in screen_files(folder, data):
        item: dict[str, Any] = {k: f[k] for k in ("screen", "title", "alt", "route", "state")}
        for k in ("html", "desktop", "mobile"):
            if f[k].is_file():
                item[k] = str(f[k].relative_to(root))
        out.append(item)
    return out


def story_design(ctx: EngineContext, story_id: str) -> dict[str, Any]:
    """Every round of a story's screens, newest first, and the approved one."""
    paths = DesignPaths.of(ctx.root)
    out = []
    for n in reversed(rounds(ctx.root, story_id)):
        folder = paths.round(story_id, n)
        data = read_json(folder / "manifest.json")
        out.append(
            {
                "round": n,
                "assumptions": data.get("assumptions") or [],
                "problems": (data.get("review") or {}).get("problems") or [],
                "screens": _screens(ctx, folder, data),
            }
        )
    approved = read_json(paths.approved(story_id) / "manifest.json")
    return {
        "story_id": story_id,
        "rounds": out,
        "approved": {
            "round": approved.get("approved_round"),
            "alternative": approved.get("alternative") or "",
            "screens": _screens(ctx, paths.approved(story_id), approved),
        }
        if approved
        else None,
    }


def overview(ctx: EngineContext) -> dict[str, Any]:
    from loompa.engine.design_gate import DESIGN_REASONS

    paths = DesignPaths.of(ctx.root)
    reasons = {r.value for r in DESIGN_REASONS}
    pending = []
    for m in ctx.store.list_messages(ctx.slug, status="pending"):
        if not m.story_id or not m.screens and not m.questions:
            continue
        row = ctx.store.get_story(m.story_id) or {}
        state = row.get("state") or {}
        if state.get("blocked_reason") not in reasons:
            continue
        pending.append(
            {
                "message": m.model_dump(mode="json"),
                "story_id": m.story_id,
                "title": row.get("title", ""),
                "reason": state.get("blocked_reason"),
            }
        )
    base = None
    if load_tokens(ctx.root):
        base = {
            "tokens": load_tokens(ctx.root),
            "board": "board.html" if paths.board.is_file() else "",
            **{
                v: f"board.{v}.png"
                for v in ("desktop", "mobile")
                if (paths.root / f"board.{v}.png").is_file()
            },
        }
    stories = (
        sorted(p.name for p in paths.root.glob("S-*") if p.is_dir() and STORY_ID.fullmatch(p.name))
        if paths.root.is_dir()
        else []
    )
    return {
        "base": base,
        "references": list_references(ctx.root),
        "pending": pending,
        "stories": stories,
        "gate": ctx.config.design.gate,
        "sees": bool(ctx.config.models.vision),
    }


def register(app: FastAPI, hub: Any) -> None:
    @app.get("/api/factories/{slug}/design")
    def design_overview(slug: str) -> dict[str, Any]:
        return overview(hub.get(slug).ctx)

    @app.get("/api/factories/{slug}/stories/{story_id}/design")
    def design_of_story(slug: str, story_id: str) -> dict[str, Any]:
        if not STORY_ID.fullmatch(story_id):
            raise HTTPException(404, story_id)
        ctx = hub.get(slug).ctx
        refs = list_references(ctx.root, story_id)
        return {**story_design(ctx, story_id), "references": refs}

    @app.get("/api/factories/{slug}/design/file/{path:path}")
    def design_file(slug: str, path: str) -> FileResponse:
        ctx = hub.get(slug).ctx
        paths = DesignPaths.of(ctx.root)
        target = (paths.root / path).resolve()
        if not paths.contains(target) or not target.is_file():
            raise HTTPException(404, path)
        suffix = target.suffix.lower()
        if suffix in MEDIA:
            return FileResponse(target, media_type=MEDIA[suffix])
        if suffix == ".html":
            return FileResponse(
                target,
                media_type="text/html",
                headers={"Content-Security-Policy": SCREEN_CSP, "Cache-Control": "no-store"},
            )
        raise HTTPException(404, path)

    @app.post("/api/factories/{slug}/design/references")
    async def design_add_reference(
        slug: str,
        kind: str = Form(...),
        title: str = Form(""),
        note: str = Form(""),
        url: str = Form(""),
        story_id: str = Form(""),
        file: UploadFile | None = File(None),
    ) -> dict[str, Any]:
        ctx = hub.get(slug).ctx
        if story_id and not STORY_ID.fullmatch(story_id):
            raise HTTPException(400, "história desconhecida")
        data = await file.read() if file is not None else None
        try:
            ref = add_reference(
                ctx.root,
                kind=kind,
                title=title,
                note=note,
                url=url,
                story_id=story_id or None,
                data=data,
                media_type=(file.content_type or "") if file is not None else "",
            )
            picture_note = await _picture_of(ctx, ref)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from None
        ref = next((r for r in list_references(ctx.root) if r["id"] == ref["id"]), ref)
        try:
            ctx.memory.upsert_document(
                f"design:{ref['id']}",
                memory_text(ref),
                kind="design",
                title=ref["title"] or ref["id"],
            )
        except Exception:  # noqa: BLE001 - memory is best-effort
            pass
        ctx.emit("design.reference", reference=ref["id"], kind=kind, story_id=story_id or None)
        return {"reference": ref, "note": picture_note}

    @app.delete("/api/factories/{slug}/design/references/{ref_id}")
    def design_remove_reference(slug: str, ref_id: str) -> dict[str, Any]:
        ctx = hub.get(slug).ctx
        if not remove_reference(ctx.root, ref_id):
            raise HTTPException(404, ref_id)
        return {"removed": ref_id}


async def _picture_of(ctx: EngineContext, ref: dict[str, Any]) -> str:
    """A site is photographed and a Figma frame downloaded when they are added, so the UX sees
    what the founder saw. Returns a note for the founder when the picture could not be taken."""
    paths = DesignPaths.of(ctx.root)
    if ref["kind"] == "link":
        from loompa.design.render import look_at

        looks = await look_at(
            [(ref["url"], paths.references / ref["id"])], accessibility=False, settle_s=3.0
        )
        shot = (looks or [None])[0]
        if shot is None or "desktop" not in shot.shots:
            return "Guardei o link, mas não consegui abrir o site para tirar a foto."
        set_picture(ctx.root, ref["id"], shot.shots["desktop"].name)
        return ""
    if ref["kind"] == "figma":
        from loompa.design.figma import FigmaError, fetch_frame

        token = ctx.secrets.get(ctx.config.design.figma_token_env) or ""
        try:
            picture, outline = await fetch_frame(ref["url"], token)
        except FigmaError as exc:
            return f"Guardei o link, mas {exc}."
        name = f"{ref['id']}.png"
        (paths.references / name).write_bytes(picture)
        set_picture(ctx.root, ref["id"], name)
        if outline:
            from loompa.design import write_json

            idx = read_json(paths.reference_index)
            for r in idx.get("items") or []:
                if r.get("id") == ref["id"]:
                    r["outline"] = outline[:4000]
            write_json(paths.reference_index, idx)
        return ""
    return ""
