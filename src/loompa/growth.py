"""Growth, first rung: pieces the founder reviews (plan Phase 17, ADR-0042).

Only on a commercial product (16.1). Four kinds of piece, each a draft until the founder answers:

* **landing** (17.1): not a document but a code story. It goes through the Product Owner's door
  like any quick story (ADR-0017) and, being a screen, through the design gate (Phase 15).
* **plan** (17.3): the marketing and acquisition plan; approved, it becomes `company/marketing.md`
  and every role that reads the company documents sees it.
* **copy** (17.2): landing and store texts, or an e-mail sequence, as files.
* **posts** (17.4): one text per network and its art in the network's format, made by the
  Designer with the brand as reference.

Every piece lives in `.loompa/design/growth/G-0XX/` (served by the design file endpoint) with a
manifest, and reaches the founder as one decision: approve, adjust (a new round with the note) or
discard. Approved pieces are delivered as files. Nothing is ever sent, published or paid by the
factory: there is no code path that could.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loompa.comms import FounderAnswer, FounderMessage, MessageKind, Option
from loompa.design import DesignPaths, read_json, write_json
from loompa.factory_kind import profile

if TYPE_CHECKING:
    from loompa.engine.context import EngineContext

REF = "growth:"  # + piece id, in `technical_ref`
KINDS = ("plan", "copy", "emails", "posts")
# Each network's art format (px). Feed posts are square; stories vertical; link cards wide.
FORMATS: dict[str, tuple[int, int, str]] = {
    "instagram": (1080, 1080, "Instagram (feed)"),
    "instagram_story": (1080, 1920, "Instagram (stories)"),
    "facebook": (1200, 630, "Facebook"),
    "linkedin": (1200, 627, "LinkedIn"),
    "x": (1600, 900, "X"),
    "ad_square": (1080, 1080, "Anúncio quadrado"),
}


class GrowthError(ValueError):
    pass


def growth_dir(root: Path) -> Path:
    return DesignPaths.of(root).root / "growth"


def _require(ctx: EngineContext) -> None:
    if not profile(ctx.config).growth:
        raise GrowthError("as peças de marketing são para produtos comerciais (Configurações)")


def _new_id(root: Path) -> str:
    folder = growth_dir(root)
    nums = [int(m.group(1)) for p in folder.glob("G-*") if (m := re.fullmatch(r"G-(\d+)", p.name))]
    return f"G-{max(nums, default=0) + 1:03d}"


def pieces(root: Path) -> list[dict[str, Any]]:
    folder = growth_dir(root)
    if not folder.is_dir():
        return []
    out = [read_json(p / "manifest.json") for p in sorted(folder.glob("G-*")) if p.is_dir()]
    return [m for m in out if m]


def _phase(ctx: EngineContext) -> str:
    from loompa.lifecycle import Lifecycle

    return Lifecycle(ctx).brief()


def _rel(root: Path, p: Path) -> str:
    return str(p.relative_to(DesignPaths.of(root).root))


async def landing(ctx: EngineContext) -> dict[str, Any]:
    """17.1: the landing page is a story; the Product Owner reads it first (ADR-0017)."""
    from loompa.agents import Conversations

    _require(ctx)
    out = await Conversations(ctx).quick_story(
        "Landing page do produto",
        "Uma página pública que apresenta o produto ao cliente ideal, com a proposta de valor, "
        "três benefícios e uma chamada para ação, no posicionamento e na voz da marca dos "
        "documentos da empresa. Passa pelo portão de design.",
        2,
    )
    return {"story_id": out.story_id, "refused": out.story_id is None}


async def make(
    ctx: EngineContext,
    kind: str,
    brief: str = "",
    networks: list[str] | None = None,
    *,
    piece_id: str | None = None,
    note: str = "",
) -> dict[str, Any]:
    """Write (or rewrite, with the founder's note) one piece and ask the founder about it."""
    from loompa.agents.storyteller import GrowthWriter

    _require(ctx)
    if kind not in KINDS:
        raise GrowthError(f"tipo de peça desconhecido: {kind}")
    root = ctx.root
    gid = piece_id or _new_id(root)
    folder = growth_dir(root) / gid
    old = read_json(folder / "manifest.json")
    n = int(old.get("round") or 0) + 1
    brief = brief or str(old.get("brief") or "")
    networks = networks or list(old.get("networks") or [])
    if note:
        brief = f"{brief}\n\nThe founder asked to change the previous version: {note}".strip()
    writer = GrowthWriter(ctx)
    phase = _phase(ctx)
    folder.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "id": gid,
        "kind": kind,
        "brief": str(old.get("brief") or brief),
        "networks": networks,
        "round": n,
        "status": "draft",
        "updated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "files": [],
        "images": [],
    }
    if kind == "plan":
        data = await writer.plan(phase, brief)
        (folder / "plano-de-marketing.md").write_text(data["plan"], encoding="utf-8")
        manifest |= {"title": data["title"], "files": ["plano-de-marketing.md"]}
        context = data["plan"][:3500]
    elif kind in ("copy", "emails"):
        what = "e-mail sequence" if kind == "emails" else "landing and store texts"
        data = await writer.copy(what, brief, phase)
        lines = []
        for p in data["pieces"]:
            head = f"## {p['name']}" + (f" (dia {p['day']})" if kind == "emails" else "")
            extra = f"Assunto: {p['subject']}\nPrévia: {p['preview']}\n\n" if p["subject"] else ""
            lines.append(f"{head}\n\n{extra}{p['body']}\n")
        name = "emails.md" if kind == "emails" else "textos.md"
        (folder / name).write_text("\n".join(lines), encoding="utf-8")
        manifest |= {"title": data["title"], "files": [name], "notes": data["notes"]}
        context = "\n".join(lines)[:3500]
    else:
        networks = [n_ for n_ in networks if n_ in FORMATS] or ["instagram", "linkedin"]
        manifest["networks"] = networks
        data = await writer.posts(networks, brief, phase)
        await _art(ctx, gid, folder, data["posts"])
        text = "\n\n".join(
            f"## {FORMATS[p['network']][2]}\n\n{p['text']}\n\n"
            + " ".join(f"#{h.lstrip('#')}" for h in p["hashtags"])
            for p in data["posts"]
        )
        (folder / "posts.md").write_text(text, encoding="utf-8")
        manifest |= {
            "title": data["title"],
            "files": ["posts.md"],
            "images": [p["image"] for p in data["posts"] if p.get("image")],
            "notes": data["notes"],
        }
        context = text[:3000]
    write_json(folder / "manifest.json", manifest)
    msg = _ask(ctx, manifest, context)
    manifest["message_id"] = msg.id
    write_json(folder / "manifest.json", manifest)
    return manifest


async def _art(ctx: EngineContext, gid: str, folder: Path, posts: list[dict[str, Any]]) -> None:
    from loompa.agents.designer import DesignerAgent, ImageBudgetSpent
    from loompa.design.brand import PieceSpec
    from loompa.engine.state import StoryState

    specs = [
        PieceSpec(
            name=p["network"].replace("_", "-"),
            purpose=p["art"],
            width=FORMATS[p["network"]][0],
            height=FORMATS[p["network"]][1],
            background="solid",
            format="png",
        )
        for p in posts
        if p.get("art")
    ]
    holder = StoryState(story_id=gid, title="growth")  # the per-piece picture cap counts here
    try:
        made = await DesignerAgent(ctx).pieces(holder, specs, folder, "social posts")
    except ImageBudgetSpent:
        made = []
    files = {m["name"]: m["file"] for m in made}
    for p in posts:
        f = files.get(p["network"].replace("_", "-"))
        if f:
            p["image"] = f


def _ask(ctx: EngineContext, manifest: dict[str, Any], context: str) -> FounderMessage:
    root = ctx.root
    folder = growth_dir(root) / manifest["id"]
    label = {
        "plan": "plano de marketing",
        "copy": "textos",
        "emails": "sequência de e-mails",
        "posts": "posts e anúncios",
    }[manifest["kind"]]
    screens = [
        {"title": Path(img).stem, "state": "", "desktop": _rel(root, folder / img)}
        for img in manifest.get("images") or []
    ]
    context = re.sub(r"^#+\s*", "", context, flags=re.M)  # the inbox shows plain text
    msg = FounderMessage(
        factory=ctx.slug,
        kind=MessageKind.DECISION,
        sender="Storyteller Loompa",
        title=f"Rascunho para revisar: {label}",
        context=f"{manifest['id']}, rodada {manifest['round']}.\n\n{context}"
        + (
            f"\n\nObservações: {'; '.join(manifest.get('notes') or [])}"
            if manifest.get("notes")
            else ""
        ),
        impact="Nada é enviado nem publicado pela fábrica: aprovado, vira arquivo para você usar.",
        options=[
            Option(key="approve", label="Aprovar", recommended=True),
            Option(key="adjust", label="Pedir ajustes", description="Escreva o que mudar."),
            Option(key="discard", label="Descartar"),
        ],
        screens=screens,
        technical_ref=REF + manifest["id"],
    )
    return ctx.inbox(msg)


def on_answer(ctx: EngineContext, msg: FounderMessage, answer: FounderAnswer) -> dict | None:
    """Approve, discard, or return the note for a new round (the caller runs it: it calls models)."""
    ref = msg.technical_ref or ""
    if not ref.startswith(REF):
        return None
    gid = ref.removeprefix(REF)
    folder = growth_dir(ctx.root) / gid
    manifest = read_json(folder / "manifest.json")
    if not manifest:
        return None
    key = answer.option_key or ("adjust" if answer.text else "")
    if key == "approve":
        manifest["status"] = "approved"
        if manifest["kind"] == "plan":
            _save_plan(ctx, folder / "plano-de-marketing.md")
    elif key == "discard":
        manifest["status"] = "discarded"
    elif key == "adjust":
        manifest["status"] = "adjusting"
        manifest["pending_note"] = (answer.text or "").strip()
    write_json(folder / "manifest.json", manifest)
    ctx.emit("growth.answered", piece=gid, status=manifest["status"])
    return manifest


def _save_plan(ctx: EngineContext, plan: Path) -> None:
    from loompa.company import company_dir

    folder = company_dir(ctx.factory.paths.loompa)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "marketing.md").write_text(
        "# Plano de marketing e aquisição\n\n" + plan.read_text(encoding="utf-8"), encoding="utf-8"
    )


async def redo_pending(ctx: EngineContext) -> list[str]:
    """Pieces the founder asked to adjust get their new round (models are called here, not in
    the answer, which stays pure)."""
    done = []
    for m in pieces(ctx.root):
        if m.get("status") == "adjusting":
            await make(ctx, m["kind"], piece_id=m["id"], note=str(m.get("pending_note") or ""))
            done.append(m["id"])
    return done
