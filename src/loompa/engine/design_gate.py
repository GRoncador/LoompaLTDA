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
from loompa.design.render import file_url, look_at
from loompa.engine.context import EngineContext
from loompa.engine.phases import Phase, advance, goto, register
from loompa.engine.state import TERMINAL, BlockedReason, Stage, StoryState
from loompa.factory_kind import design_gate

DESIGN_KEY = "design"  # state.extra: the story's progress through the gate
BASE_KEY = "design_base"  # state.extra: "asked" while this story's base proposal waits
UX_TRIES = 3  # the founder's rule: three attempts before the founder (ADR-0016)
ROUNDS_NOTE_FROM = 3
SENDER = "UX Loompa"
DESIGN_REASONS = (BlockedReason.DESIGN_BASE, BlockedReason.DESIGN_QUESTIONS, BlockedReason.DESIGN)


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
    lines = [f"**{a['key']}. {a['name']}** — {a['idea']}" for a in alts]
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
                "\n\nO que eu decidi sozinho: " + "; ".join(proposal["assumptions"])
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
    )
    problems, shots = await _check_round(ctx, state, folder, result)
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
        parts.append("O que eu decidi sozinho: " + "; ".join(assumptions[:8]) + ".")
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
    if reason == BlockedReason.DESIGN_QUESTIONS:
        d["answers"] = _answers(msg, answer, use_defaults=key == "defaults")
        goto(state, "design")
        return
    # BlockedReason.DESIGN: the approval
    n = int(d.get("round") or 1)
    if key == "approve" or key.startswith("alt_"):
        alt = key.removeprefix("alt_").upper() if key.startswith("alt_") else ""
        approve_round(ctx.root, state.story_id, n, alternative=alt)
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
    goto(state, "design")


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
