"""The design gate: screens approved before code (plan Phase 15, ADR-0039).

A story that changes what a person sees runs, after its spec, two more phases:

    spec -> spec_review -> design -> design_review -> plan -> …

* `design` (owner: the UX Loompa). The factory's visual base comes first, once: two alternatives
  of tokens and style board, the founder picks one (another story reaching `design` meanwhile is
  parked until then). Then the front-end questions, all in one decision, asked once per story and
  only when something is open. Then the screens, as static HTML at 1280 and 390 px, checked in
  code before anyone sees them: every criterion shows on a screen or is declared invisible, no
  colour outside the tokens, no page that scrolls sideways or has an element sticking out, no
  serious accessibility fault, and — with a vision model — no problem the UX itself sees in the
  pictures. What fails goes back to the UX, three times at most (the founder's rule of three
  attempts); what still fails after that travels to the founder with the screens.
* `design_review` (reviewer: the founder). One decision with the pictures, the assumptions and
  the choice: approve, pick an alternative, or ask for adjustments, which make a new round. From
  the third round on the message says how many there were. The approved round is copied to
  `approved/`: the contract the Architect plans on, the Worker builds to and the Inspector checks
  the built page against (15.6).

Every wait is the story's own (`block`): the rest of the line keeps moving.
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from loompa.agents.ux import UXAgent
from loompa.comms import FounderAnswer, FounderMessage, MessageKind, Option, Question
from loompa.dependencies import WAIT_KEY
from loompa.design import (
    DesignPaths,
    approve_round,
    approved_manifest,
    colours_in,
    contrast_problems,
    ensure_gitignore,
    load_tokens,
    read_json,
    screen_files,
    token_colours,
    write_frontend_section,
    write_json,
)
from loompa.design.references import get_reference, list_references
from loompa.design.render import file_url, look_at
from loompa.engine.context import EngineContext
from loompa.engine.phases import Phase, advance, goto, register
from loompa.engine.state import TERMINAL, BlockedReason, Stage, StoryState
from loompa.factory_kind import design_gate, profile

DESIGN_KEY = "design"  # state.extra: the story's progress through the gate
BASE_KEY = "design_base"  # state.extra: "asked" while this story's base proposal waits
UX_TRIES = 3  # the founder's rule: three attempts before the founder (ADR-0016)
ROUNDS_NOTE_FROM = 3
SENDER = "UX Loompa"
DESIGN_REASONS = (
    BlockedReason.DESIGN_BASE,
    BlockedReason.DESIGN_LOGO,
    BlockedReason.DESIGN_QUESTIONS,
    BlockedReason.DESIGN,
)


FRONTEND_SUFFIXES = {".html", ".htm", ".css", ".scss", ".jsx", ".tsx", ".vue", ".svelte"}
FRONTEND_FRAMEWORKS = {
    "react", "vue", "svelte", "angular", "next", "nuxt", "vite", "phaser", "flutter",
    "react-native", "expo", "tailwind", "solid", "astro", "remix",
}  # fmt: skip
SKIP_DIRS = {".git", ".loompa", "node_modules", ".venv", "venv", "__pycache__", "dist", "build"}


def has_screen(ctx: EngineContext, limit: int = 4000) -> bool:
    """The product has a screen (web, mobile, game): a front-end framework in its stack, a
    front-end file in its repository, or a visual base already chosen. A command-line tool or a
    library never goes through the gate, whatever a story says."""
    if load_tokens(ctx.root):
        return True
    frameworks = {f.lower() for f in ctx.config.stack.frameworks}
    if any(any(k in f for k in FRONTEND_FRAMEWORKS) for f in frameworks):
        return True
    seen = 0
    for path in ctx.root.rglob("*"):
        rel = path.relative_to(ctx.root).parts
        if any(part in SKIP_DIRS for part in rel):
            continue
        if path.suffix.lower() in FRONTEND_SUFFIXES:
            return True
        seen += 1
        if seen > limit:
            break
    return False


def gated(ctx: EngineContext, screen: bool) -> str:
    """The gate's mode for a story the Master classified (`screen`: it changes what a person
    sees), or "" when it does not pass through the gate."""
    mode = design_gate(ctx.config)
    return mode if screen and mode != "off" and has_screen(ctx) else ""


def opens_area(ctx: EngineContext, state: StoryState, mode: str) -> bool:
    """The first screen of a new area comes in two alternatives (full mode only): a story of an
    epic none of whose stories has approved screens yet."""
    if mode != "full" or not state.epic:
        return False
    for row in ctx.store.list_stories(ctx.slug):
        if row["id"] == state.story_id:
            continue
        st = row.get("state") or {}
        if st.get("epic") == state.epic and (st.get("extra") or {}).get(DESIGN_KEY, {}).get(
            "approved"
        ):
            return False
    return True


def _progress(state: StoryState) -> dict[str, Any]:
    return state.extra.setdefault(DESIGN_KEY, {"mode": "light"})


# ------------------------------------------------------------------- the visual base


def base_owner(ctx: EngineContext, me: str) -> str | None:
    """Another live story whose base proposal waits for the founder."""
    for row in ctx.store.list_stories(ctx.slug):
        if row["id"] == me or row["stage"] in {s.value for s in TERMINAL}:
            continue
        if ((row.get("state") or {}).get("extra") or {}).get(BASE_KEY) == "asked":
            return str(row["id"])
    return None


def base_pending(rows: dict[str, dict[str, Any]]) -> bool:
    """Some live story's base proposal still waits (what a story parked on it waits for)."""
    return any(
        ((r.get("state") or {}).get("extra") or {}).get(BASE_KEY) == "asked"
        and r["stage"] not in {s.value for s in TERMINAL}
        for r in rows.values()
    )


def _base_rounds(ctx: EngineContext) -> int:
    base = DesignPaths.of(ctx.root).base
    return len(list(base.glob("round-*"))) if base.is_dir() else 0


async def _propose_base(ctx: EngineContext, state: StoryState) -> StoryState:
    from loompa.engine.graph import block

    d = _progress(state)
    n = _base_rounds(ctx) + 1
    proposal = await UXAgent(ctx).propose_base(state, n, d.pop("base_feedback", ""))
    alts = proposal["alternatives"]
    folder = DesignPaths.of(ctx.root).base_round(n)
    if not alts:
        raise RuntimeError("the UX Loompa proposed no visual base")
    looks = await look_at(
        [(file_url(folder / a["dir"] / "board.html"), folder / a["dir"] / "board") for a in alts]
    )
    screens = []
    for a, look in zip(alts, looks or [None] * len(alts), strict=False):
        tokens = read_json(folder / a["dir"] / "tokens.json")
        a["problems"] = contrast_problems(tokens) + (look.problems if look else [])
        screens.append(
            {
                "title": a["name"],
                "state": "",
                "alt": a["key"],
                "html": str((folder / a["dir"] / "board.html").relative_to(_root(ctx))),
                **{
                    v: str(p.relative_to(_root(ctx)))
                    for v, p in ((look.shots if look else {}) or {}).items()
                },
            }
        )
    write_json(folder / "proposal.json", proposal)
    state.extra[BASE_KEY] = "asked"
    d["base_round"] = n
    lines = [f"{a['key']}. {a['name']}: {a['idea']}" for a in alts]
    msg = FounderMessage(
        factory=ctx.slug,
        story_id=state.story_id,
        kind=MessageKind.DECISION,
        sender=SENDER,
        title="Escolha a cara do produto antes das primeiras telas",
        context=(
            "Antes de desenhar as telas, preparei duas propostas de cores, letras e espaçamentos, "
            "com uma prancha que mostra botões, campos e cartões em cada uma. A escolhida passa a "
            "valer para todas as telas e para o código.\n\n"
            + "\n".join(lines)
            + (
                "\n\nO que eu decidi sozinho: " + _sentences(proposal["assumptions"])
                if proposal["assumptions"]
                else ""
            )
        ),
        impact="As telas desta e das próximas histórias esperam essa escolha; o resto segue.",
        options=[
            *(
                Option(key=a["key"], label=f"Usar a proposta {a['key']}", recommended=i == 0)
                for i, a in enumerate(alts)
            ),
            Option(
                key="adjust",
                label="Pedir outra rodada",
                description="Escreva o que mudar; volto com duas propostas novas.",
            ),
        ],
        screens=screens,
    )
    return await block(
        ctx,
        state,
        BlockedReason.DESIGN_BASE,
        f"visual base round {n}: {len(alts)} alternatives",
        resume="design",
        message=msg,
    )


def adopt_base(ctx: EngineContext, n: int, key: str) -> dict[str, Any]:
    """Make alternative `key` of base round `n` the factory's visual base."""
    paths = DesignPaths.of(ctx.root)
    src = paths.base_round(n) / f"alt-{key}"
    for name in ("tokens.json", "tokens.css", "board.html"):
        shutil.copyfile(src / name, paths.root / name)
    for v in ("desktop", "mobile"):
        if (src / f"board.{v}.png").is_file():
            shutil.copyfile(src / f"board.{v}.png", paths.root / f"board.{v}.png")
    tokens = load_tokens(ctx.root)
    write_frontend_section(ctx.factory.paths.constitution, tokens)
    ctx.emit("design.base_adopted", round=n, alternative=key)
    return tokens


# ------------------------------------------------------------------------ the phases


def _sentences(items: list[str]) -> str:
    """Short sentences joined into one: "a; b; c." whatever punctuation each one brought."""
    parts = [i.strip().rstrip(".;").strip() for i in items if i.strip()]
    return "; ".join(parts) + "." if parts else ""


def _root(ctx: EngineContext) -> Path:
    return DesignPaths.of(ctx.root).root


def _rel(ctx: EngineContext, p: Path) -> str:
    return str(p.relative_to(_root(ctx)))


async def node_design(ctx: EngineContext, state: StoryState) -> StoryState:
    from loompa.engine.graph import block

    ensure_gitignore(ctx.root)
    d = _progress(state)
    if not load_tokens(ctx.root):
        owner = base_owner(ctx, state.story_id)
        if owner:
            state.extra[WAIT_KEY] = {"design_base": owner}
            ctx.emit("story.waiting", story_id=state.story_id, design_base=owner)
            return state
        return await _propose_base(ctx, state)
    if wants_logo(ctx) and not has_logo(ctx):
        owner = base_owner(ctx, state.story_id)
        if owner:
            state.extra[WAIT_KEY] = {"design_base": owner}
            ctx.emit("story.waiting", story_id=state.story_id, design_base=owner)
            return state
        return await _logo_step(ctx, state)
    state.extra.pop(BASE_KEY, None)
    ux = UXAgent(ctx)
    if not d.get("asked"):
        d["asked"] = True
        found = await ux.questions(state)
        d["assumptions"] = found["assumptions"]
        if found["questions"]:
            return await block(
                ctx,
                state,
                BlockedReason.DESIGN_QUESTIONS,
                f"{len(found['questions'])} front-end questions",
                resume="design",
                message=_questions_message(ctx, state, found["questions"]),
            )
    n = int(d.get("round") or 1)
    d["round"] = n
    folder = DesignPaths.of(ctx.root).round(state.story_id, n)
    if folder.exists():
        shutil.rmtree(folder)  # a new try of the same round replaces the last one
    result = await ux.draw(
        state,
        n,
        mode=str(d.get("mode") or "light"),
        answers=list(d.get("answers") or []),
        feedback=str(d.get("feedback") or ""),
        alternatives=bool(d.get("alternatives")),
        pieces=4 if pictures_allowed(ctx) else 0,
    )
    made = await _pieces(ctx, state, result)
    problems, shots = await _check_round(ctx, state, folder, result)
    problems += [f"piece {m['name']}: {pr}" for m in made for pr in m.get("problems") or []]
    seen = await ux.look(state, shots) if shots else None
    problems += [f"{i.get('screen')} ({i.get('state')}): {i.get('problem')}" for i in (seen or [])]
    tries = int(d.get("tries") or 0) + 1
    d["tries"] = tries
    ctx.emit(
        "design.round",
        story_id=state.story_id,
        round=n,
        attempt=tries,
        screens=len(result["screens"]),
        problems=len(problems),
        looked=seen is not None,
    )
    if problems and tries < UX_TRIES:
        d["feedback"] = (
            "The factory's checks found these problems in your screens; fix them and keep the "
            "rest:\n" + "\n".join(f"- {p}" for p in problems[:20])
        )
        return goto(state, "design")
    result["review"] = {"problems": problems[:20], "looked": seen is not None}
    result["pieces"] = made
    result["assumptions"] = list(
        dict.fromkeys([*(d.get("assumptions") or []), *result["assumptions"]])
    )
    write_json(folder / "manifest.json", result)
    d["feedback"] = ""
    d["tries"] = 0
    if not result["screens"]:
        raise RuntimeError("the UX Loompa drew no screen")
    return advance(state)


async def _check_round(
    ctx: EngineContext, state: StoryState, folder: Path, result: dict[str, Any]
) -> tuple[list[str], list[tuple[str, Path]]]:
    """What code sees in a round: coverage of the criteria, colours outside the tokens, and the
    pages photographed at both widths with their layout and accessibility faults."""
    problems: list[str] = []
    if not result["screens"]:
        return ["no screen was drawn"], []
    covered = {c for s in result["screens"] for c in s.get("criteria") or []}
    covered |= set(result.get("not_visible") or [])
    missing = [i for i in range(1, len(state.acceptance) + 1) if i not in covered]
    if missing and result.get("mode") != "light":
        problems.append(
            "criteria not shown on any screen nor listed as not visible: "
            + ", ".join(str(i) for i in missing)
        )
    allowed = token_colours(load_tokens(ctx.root)) | {"#00000000"}
    files = screen_files(folder, result)
    for f in files:
        html = f["html"].read_text(encoding="utf-8") if f["html"].is_file() else ""
        body = html.split("</style>", 1)[-1] if 'data-loompa="tokens"' in html else html
        if stray := sorted(colours_in(body) - allowed):
            problems.append(
                f"{f['screen']} ({f['state']}): colours outside the tokens: {', '.join(stray[:6])}"
            )
    looks = await look_at([(file_url(f["html"]), f["html"].with_suffix("")) for f in files])
    shots: list[tuple[str, Path]] = []
    for f, look in zip(files, looks or [], strict=False):
        problems += [f"{f['screen']} ({f['state']}): {p}" for p in look.problems]
        for v, p in look.shots.items():
            width = "computer, 1280 px" if v == "desktop" else "phone, 390 px"
            shots.append((f"{f['title']} · {f['state']} · {width}", p))
    if looks is None:
        ctx.emit("design.not_rendered", story_id=state.story_id)
    return problems, shots


def _questions_message(
    ctx: EngineContext, state: StoryState, questions: list[dict[str, Any]]
) -> FounderMessage:
    return FounderMessage(
        factory=ctx.slug,
        story_id=state.story_id,
        kind=MessageKind.DECISION,
        sender=SENDER,
        title=f"Antes de desenhar “{state.title}”: {len(questions)} pergunta(s) rápida(s)",
        context=(
            "Responda tudo de uma vez: marquei a opção que eu escolheria, e você pode escrever "
            "outra resposta em qualquer uma. Com as respostas, desenho as telas."
        ),
        impact="Só esta história espera; as demais seguem normalmente.",
        options=[
            Option(key="answer", label="Enviar as respostas", recommended=True),
            Option(
                key="defaults",
                label="Usar as opções recomendadas",
                description="Sigo com a opção marcada em cada pergunta.",
            ),
        ],
        questions=[
            Question(
                id=q["id"],
                text=q["text"],
                why=q["why"],
                options=[
                    Option(key=str(i), label=o, recommended=i == q["recommended"])
                    for i, o in enumerate(q["options"])
                ],
            )
            for q in questions
        ],
    )


async def node_design_review(ctx: EngineContext, state: StoryState) -> StoryState:
    """The founder approves the round (ADR-0039 §7). The answer resumes at `plan`."""
    from loompa.engine.graph import block

    d = _progress(state)
    n = int(d.get("round") or 1)
    folder = DesignPaths.of(ctx.root).round(state.story_id, n)
    data = read_json(folder / "manifest.json")
    screens = []
    for f in screen_files(folder, data):
        item = {
            "title": f["title"],
            "screen": f["screen"],
            "state": f["state"],
            "alt": f["alt"],
            "html": _rel(ctx, f["html"]),
        }
        for v in ("desktop", "mobile"):
            if f[v].is_file():
                item[v] = _rel(ctx, f[v])
        screens.append(item)
    pieces_dir = DesignPaths.of(ctx.root).story(state.story_id) / "pieces"
    for piece in data.get("pieces") or []:
        if (pieces_dir / str(piece.get("file"))).is_file():
            screens.append(
                {
                    "title": f"Peça: {piece['name']}",
                    "state": "",
                    "desktop": _rel(ctx, pieces_dir / str(piece["file"])),
                }
            )
    alts = sorted({str(s.get("alt")) for s in data.get("screens") or [] if s.get("alt")})
    assumptions = list(data.get("assumptions") or [])
    left = list((data.get("review") or {}).get("problems") or [])
    parts = [
        f"Desenhei {len(screens)} tela(s) para esta história, no computador e no celular. "
        "Aprovadas, elas viram o contrato: o código é feito e conferido contra elas."
    ]
    if n >= ROUNDS_NOTE_FROM:
        parts.append(
            f"Esta é a rodada {n}. Se preferir, aprove esta versão e os detalhes viram outra "
            "história."
        )
    if assumptions:
        parts.append("O que eu decidi sozinho: " + _sentences(assumptions[:8]))
    if left:
        parts.append(
            f"A conferência automática ainda aponta {len(left)} ponto(s) que não consegui "
            "resolver em três tentativas; veja nas telas antes de aprovar."
        )
    options = [Option(key="approve", label="Aprovar as telas", recommended=not alts)]
    options += [
        Option(key=f"alt_{a}", label=f"Aprovar com a alternativa {a}", recommended=i == 0)
        for i, a in enumerate(alts)
    ]
    for ref in list_references(ctx.root, state.story_id):
        if ref.get("kind") == "figma" and ref.get("file"):
            options.append(
                Option(
                    key=f"figma_{ref['id']}",
                    label=f"Aprovar com o quadro do Figma {ref['id']} no lugar da tela principal",
                    description="A foto do Figma vira a tela aprovada; o código é conferido com ela.",
                )
            )
    options.append(
        Option(
            key="adjust",
            label="Pedir ajustes",
            description="Escreva o que mudar em cada tela; volto com uma nova rodada.",
        )
    )
    msg = FounderMessage(
        factory=ctx.slug,
        story_id=state.story_id,
        kind=MessageKind.DECISION,
        sender=SENDER,
        title=f"Telas de “{state.title}” para aprovar",
        context="\n\n".join(parts),
        impact="O código desta história começa depois da sua aprovação; as demais seguem.",
        options=options,
        screens=screens,
    )
    return await block(
        ctx,
        state,
        BlockedReason.DESIGN,
        f"design round {n}: {len(screens)} screen states",
        resume="plan",
        message=msg,
    )


# ----------------------------------------------------------------------- the answers


def apply_design_answer(
    ctx: EngineContext,
    state: StoryState,
    msg: FounderMessage,
    answer: FounderAnswer,
    key: str,
    guidance: str,
) -> None:
    """What a founder's answer to the gate does. Pure state logic (no model)."""
    d = _progress(state)
    reason = state.blocked_reason
    text = (answer.text or "").strip()
    if reason == BlockedReason.DESIGN_BASE:
        n = int(d.get("base_round") or _base_rounds(ctx))
        alt_dir = DesignPaths.of(ctx.root).base_round(n) / f"alt-{key}"
        if key and alt_dir.is_dir():
            adopt_base(ctx, n, key)
            state.extra.pop(BASE_KEY, None)
            if text:
                state.note(f"Sobre a base visual: {text}")
        else:  # another round, with what the founder wrote
            d["base_feedback"] = text or guidance
            state.extra.pop(BASE_KEY, None)
        goto(state, "design")
        return
    if reason == BlockedReason.DESIGN_LOGO:
        apply_logo_answer(ctx, d, key, text or guidance)
        goto(state, "design")
        return
    if reason == BlockedReason.DESIGN_QUESTIONS:
        d["answers"] = _answers(msg, answer, use_defaults=key == "defaults")
        goto(state, "design")
        return
    # BlockedReason.DESIGN: the approval
    n = int(d.get("round") or 1)
    if key == "approve" or key.startswith(("alt_", "figma_")):
        alt = key.removeprefix("alt_").upper() if key.startswith("alt_") else ""
        approved = approve_round(ctx.root, state.story_id, n, alternative=alt)
        if key.startswith("figma_"):
            use_figma_frame(ctx, state.story_id, approved, key.removeprefix("figma_"))
        if text:
            state.note(f"Ao aprovar as telas: {text}")
        d["approved"] = n
        ctx.emit("design.approved", story_id=state.story_id, round=n, alternative=alt)
        goto(state, state.next_phase("design_review") or "plan")
        return
    d["feedback"] = "The founder asked for these adjustments to the previous round:\n" + (
        text or guidance
    )
    d["round"] = n + 1
    d["tries"] = 0
    d["redo_pieces"] = True  # the adjustment may be about a picture: they are made again
    goto(state, "design")


def use_figma_frame(ctx: EngineContext, story_id: str, approved: Path, ref_id: str) -> None:
    """A Figma frame stands in for the main screen's computer picture (15.9): the built page is
    compared with it. The UX's HTML stays, for the structure the Worker reuses."""
    ref = get_reference(ctx.root, ref_id)
    if not ref or not ref.get("file"):
        return
    picture = DesignPaths.of(ctx.root).references / str(ref["file"])
    data = read_json(approved / "manifest.json")
    main = next((f for f in screen_files(approved, data) if f["state"] == "main"), None)
    if main is None or not picture.is_file():
        return
    shutil.copyfile(picture, main["desktop"])
    data["figma"] = {"reference": ref_id, "screen": main["screen"]}
    write_json(approved / "manifest.json", data)
    ctx.emit("design.figma_approved", story_id=story_id, reference=ref_id)


def _answers(msg: FounderMessage, answer: FounderAnswer, *, use_defaults: bool) -> list[str]:
    """Each question with the founder's answer, in plain words for the UX prompt."""
    out = []
    for q in msg.questions:
        given = (answer.answers.get(q.id) or "").strip()
        option = next((o for o in q.options if o.key == given), None)
        if option is None and (use_defaults or not given):
            option = next((o for o in q.options if o.recommended), None)
            given = ""
        reply = option.label if option is not None else given
        if reply:
            out.append(f"{q.text} → {reply}")
    return out


# --------------------------------------------------------- the Designer (ADR-0040)


def pictures_allowed(ctx: EngineContext) -> bool:
    return ctx.config.design.images.per_story > 0 and bool(ctx.config.design.images.draft)


def wants_logo(ctx: EngineContext) -> bool:
    """The logo is part of the visual base on a commercial product (`design.images.logo`)."""
    setting = ctx.config.design.images.logo
    if setting == "off" or not pictures_allowed(ctx):
        return False
    return setting == "on" or profile(ctx.config).growth


def _brand(ctx: EngineContext) -> Path:
    return DesignPaths.of(ctx.root).root / "brand"


def has_logo(ctx: EngineContext) -> bool:
    """A logo exists, or the founder said the product goes without one."""
    b = _brand(ctx)
    return (b / "logo-512.png").is_file() or (b / "skipped").is_file()


async def _logo_step(ctx: EngineContext, state: StoryState) -> StoryState:
    """Flat drafts → the founder picks (or adjusts once over a pick) → the chosen one is traced
    into a vector in code; only when the trace fails its check does the vector model redraw it."""
    from loompa.agents.designer import DesignerAgent
    from loompa.engine.graph import block

    d = _progress(state)
    designer = DesignerAgent(ctx)
    drafts_dir = _brand(ctx) / "drafts"
    if d.get("logo_pick"):
        chosen = drafts_dir / str(d.pop("logo_pick"))
        await _finish_logo(ctx, state, designer, chosen)
        return goto(state, "design")
    if d.get("logo_adjust"):
        adj = d.pop("logo_adjust")
        drafts = await designer.logo_variants(
            state, drafts_dir / adj["file"], adj["note"], drafts_dir
        )
    else:
        drafts = await designer.logo_drafts(
            state, drafts_dir, feedback=str(d.pop("logo_feedback", ""))
        )
    if not drafts:
        raise RuntimeError("the Designer Loompa made no logo draft")
    state.extra[BASE_KEY] = "asked"
    write_json(drafts_dir / "drafts.json", {"drafts": drafts})
    screens = [
        {
            "title": f"{i}. {x['direction']}".strip(),
            "state": "",
            "desktop": _rel(ctx, drafts_dir / x["file"]),
        }
        for i, x in enumerate(drafts, 1)
    ]
    directions = list(dict.fromkeys(x["direction"] for x in drafts if x.get("direction")))
    msg = FounderMessage(
        factory=ctx.slug,
        story_id=state.story_id,
        kind=MessageKind.DECISION,
        sender="Designer Loompa",
        title="Escolha um rascunho para a logo",
        context=(
            f"Fiz {len(drafts)} rascunhos chapados"
            + (f" em {len(directions)} direções ({', '.join(directions)})" if directions else "")
            + ". Escolha um: ele vira a logo em vetor e os tamanhos do produto. Se quiser mudar "
            "algo num deles, escolha “Ajustar” e diga qual e o quê. A busca de marcas parecidas e "
            "o registro no INPI ficam com você."
        ),
        impact="As telas esperam a logo; o resto da fábrica segue.",
        options=[
            *(
                Option(key=x["key"], label=f"Usar o {i}", recommended=i == 1)
                for i, x in enumerate(drafts, 1)
            ),
            Option(
                key="adjust",
                label="Ajustar um deles",
                description="Escreva o número e o que mudar, por exemplo: “3, letras mais grossas”.",
            ),
            Option(key="redo", label="Outros rascunhos", description="Diga o que procurar."),
            Option(key="no_logo", label="Seguir sem logo"),
        ],
        screens=screens,
    )
    return await block(
        ctx,
        state,
        BlockedReason.DESIGN_LOGO,
        f"logo: {len(drafts)} drafts",
        resume="design",
        message=msg,
    )


def apply_logo_answer(ctx: EngineContext, d: dict[str, Any], key: str, text: str) -> None:
    drafts_dir = _brand(ctx) / "drafts"
    drafts = read_json(drafts_dir / "drafts.json").get("drafts") or []
    by_key = {x["key"]: x for x in drafts}
    if key in by_key:
        d["logo_pick"] = by_key[key]["file"]
    elif key == "no_logo":
        _brand(ctx).mkdir(parents=True, exist_ok=True)
        (_brand(ctx) / "skipped").write_text("the founder chose no logo\n", encoding="utf-8")
    elif key == "adjust" and drafts:
        import re

        m = re.match(r"\s*(\d+)\s*[,:.-]?\s*(.*)", text or "", re.S)
        i = int(m.group(1)) if m else 1
        base = drafts[min(max(i, 1), len(drafts)) - 1]
        d["logo_adjust"] = {"file": base["file"], "note": (m.group(2) if m else text) or text}
    else:
        d["logo_feedback"] = text


async def _finish_logo(ctx: EngineContext, state: StoryState, designer: Any, chosen: Path) -> None:
    from loompa.design.brand import export_brand, trace

    brand = _brand(ctx)
    traced = await trace(chosen, brand / "logo.svg")
    note = ""
    svg: Path | None = traced.svg if traced.ok else None
    if not traced.ok:
        try:
            svg = await designer.vector_redraw(state, chosen, brand / "logo.svg")
            note = "redesenhada pelo modelo de vetor (confira se ficou igual ao rascunho)"
        except Exception:  # noqa: BLE001 - the PNGs still make a usable brand
            svg = None
            note = "sem vetor: o traçado falhou e o modelo de vetor não respondeu"
    written = export_brand(chosen, svg, brand)
    drafts = read_json(brand / "drafts" / "drafts.json").get("drafts") or []
    picked = next((x for x in drafts if x.get("file") == chosen.name), {})
    write_json(
        brand / "logo.json",
        {
            "from": chosen.name,
            "prompt": picked.get("prompt", ""),
            "model": picked.get("model", ""),
            "seed": picked.get("seed"),
            "trace": {"ok": traced.ok, "paths": traced.paths, "moved": traced.moved},
            "note": note,
            "files": written,
        },
    )
    _note_brand(ctx, written, note)
    ctx.emit("design.logo", story_id=state.story_id, traced=traced.ok, note=note or None)


def _note_brand(ctx: EngineContext, files: list[str], note: str) -> None:
    """The logo's files in the company's brand document (13.2), for every role that reads it."""
    company = ctx.factory.paths.company
    company.mkdir(parents=True, exist_ok=True)
    doc = company / "brand.md"
    text = doc.read_text(encoding="utf-8") if doc.is_file() else "# Marca\n"
    section = (
        "## Logo\n\nArquivos em `.loompa/design/brand/`: "
        + ", ".join(f"`{f}`" for f in files)
        + (f". Observação: {note}." if note else ".")
        + "\n"
    )
    import re

    pattern = re.compile(r"^## Logo\s*$.*?(?=^## |\Z)", re.M | re.S)
    text = (
        pattern.sub(section + "\n", text, count=1)
        if pattern.search(text)
        else (text.rstrip() + "\n\n" + section)
    )
    doc.write_text(text, encoding="utf-8")


async def _pieces(ctx: EngineContext, state: StoryState, result: dict[str, Any]) -> list[dict]:
    """The pictures the UX asked for, made once per founder round (internal tries reuse them)."""
    from loompa.agents.designer import DesignerAgent, ImageBudgetSpent, piece_specs

    specs = piece_specs(result.get("pieces"))
    d = _progress(state)
    folder = DesignPaths.of(ctx.root).story(state.story_id) / "pieces"
    if d.pop("redo_pieces", False) and folder.exists():
        shutil.rmtree(folder)
    if not specs:
        return []
    known = read_json(folder / "pieces.json").get("pieces") or []
    have = {k["name"]: k for k in known if (folder / str(k.get("file"))).is_file()}
    missing = [sp for sp in specs if sp.name not in have]
    made: list[dict] = []
    if missing:
        screens_note = "\n".join(f"- {s['title']} ({s.get('route')})" for s in result["screens"])
        try:
            made = await DesignerAgent(ctx).pieces(state, missing, folder, screens_note)
        except ImageBudgetSpent as exc:
            ctx.emit("designer.budget", story_id=state.story_id, why=str(exc))
    out = [have[sp.name] for sp in specs if sp.name in have] + made
    write_json(folder / "pieces.json", {"pieces": out})
    return out


def place_pieces(ctx: EngineContext, state: StoryState, wt: Any) -> list[str]:
    """Copy the approved pieces into the story's worktree at their targets and commit them:
    the Worker never generates a picture, it builds around the approved files."""
    data = approved_manifest(ctx.root, state.story_id)
    d = state.extra.get(DESIGN_KEY) or {}
    if not data or d.get("pieces_placed"):
        return []
    folder = DesignPaths.of(ctx.root).story(state.story_id) / "pieces"
    placed = []
    for piece in data.get("pieces") or []:
        src = folder / str(piece.get("file") or "")
        target = str(piece.get("target") or "")
        if not target or not src.is_file():
            continue
        dst = Path(wt.path) / target
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        placed.append(target)
    if placed:
        ctx.worktrees.commit_all(wt, f"feat({state.story_id.lower()}): peças visuais aprovadas")
        state.allowed_paths = list(dict.fromkeys([*state.allowed_paths, *placed]))
        ctx.emit("design.pieces_placed", story_id=state.story_id, files=placed)
    d["pieces_placed"] = True
    return placed


register(
    Phase(
        "design",
        node_design,
        Stage.SPEC,
        owner="ux",
        description="screens as static HTML, checked in code before the founder",
    )
)
register(
    Phase(
        "design_review",
        node_design_review,
        Stage.SPEC,
        owner="ux",
        reviewer="founder",
        description="the founder approves the screens; the approved round is the contract",
    )
)

__all__ = [
    "DESIGN_KEY",
    "DESIGN_REASONS",
    "apply_design_answer",
    "base_pending",
    "gated",
]
