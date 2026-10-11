"""The design gate's files (plan Phase 15, ADR-0039).

Everything the UX Loompa draws and the founder approves lives under `.loompa/design/` in the
factory's main checkout, never in a story's branch:

    design/
      references/index.json, R-001.png …   what the founder sent: images, sites, Figma, text (15.2)
      tokens.json, tokens.css, board.html  the approved visual base (15.3)
      base/round-N/alt-K/…                 the proposals of the visual base, kept for the history
      S-012/round-N/manifest.json          one round of a story's screens (15.4)
      S-012/round-N/<screen>/<state>.html  + <state>.desktop.png and <state>.mobile.png
      S-012/approved/                      the round the founder approved: the contract (15.5)

A screen is static HTML in the factory's stack, with example data and no logic, so the approved
screen is already in the final medium and is the picture the built page is compared with (15.6).
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DESIGN_DIR = "design"
STATES = ("main", "empty", "error", "loading")
VIEWPORT_NAMES = ("desktop", "mobile")
_SAFE = re.compile(r"[^a-z0-9-]+")
TOKENS_MARK = 'data-loompa="tokens"'

GITIGNORE_LINES = (
    ".loompa/design/*/round-*/",  # every round of a story's screens; `approved/` is kept
    ".loompa/design/base/",  # the visual base's proposals; the approved one sits beside them
)


def safe_name(text: str, fallback: str = "tela") -> str:
    """A file-system name from what a model called a screen or a state."""
    return _SAFE.sub("-", (text or "").strip().lower()).strip("-")[:40] or fallback


def design_root(factory_root: Path) -> Path:
    return Path(factory_root) / ".loompa" / DESIGN_DIR


@dataclass(frozen=True)
class DesignPaths:
    root: Path  # .loompa/design

    @classmethod
    def of(cls, factory_root: Path) -> DesignPaths:
        return cls(design_root(factory_root))

    @property
    def references(self) -> Path:
        return self.root / "references"

    @property
    def reference_index(self) -> Path:
        return self.references / "index.json"

    @property
    def tokens(self) -> Path:
        return self.root / "tokens.json"

    @property
    def tokens_css(self) -> Path:
        return self.root / "tokens.css"

    @property
    def board(self) -> Path:
        return self.root / "board.html"

    @property
    def base(self) -> Path:
        return self.root / "base"

    def base_round(self, n: int) -> Path:
        return self.base / f"round-{n}"

    def story(self, story_id: str) -> Path:
        return self.root / story_id

    def round(self, story_id: str, n: int) -> Path:
        return self.story(story_id) / f"round-{n}"

    def approved(self, story_id: str) -> Path:
        return self.story(story_id) / "approved"

    def contains(self, path: Path) -> bool:
        try:
            Path(path).resolve().relative_to(self.root.resolve())
        except ValueError:
            return False
        return True


# --------------------------------------------------------------------------- tokens


def has_base(factory_root: Path) -> bool:
    return DesignPaths.of(factory_root).tokens.is_file()


def load_tokens(factory_root: Path) -> dict[str, Any]:
    p = DesignPaths.of(factory_root).tokens
    if not p.is_file():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _flat(prefix: str, value: Any) -> list[tuple[str, str]]:
    if isinstance(value, dict):
        out: list[tuple[str, str]] = []
        for k, v in value.items():
            out += _flat(f"{prefix}-{safe_name(str(k), 'x')}" if prefix else safe_name(str(k)), v)
        return out
    if isinstance(value, list):
        return [(f"{prefix}-{i + 1}", str(v)) for i, v in enumerate(value)]
    return [(prefix, str(value))]


def tokens_css(tokens: dict[str, Any]) -> str:
    """The tokens as CSS custom properties: `{"color": {"primary": "#123"}}` gives
    `--color-primary: #123`. Written by code, so a screen can never carry a value the tokens
    file does not have."""
    lines = [f"  --{name}: {value};" for name, value in _flat("", tokens) if name and value]
    return ":root {\n" + "\n".join(lines) + "\n}\n"


def strip_scripts(html: str) -> str:
    """Screens carry no logic: a script a model wrote anyway is removed before the page opens."""
    return re.sub(r"<script\b.*?</script>", "", html, flags=re.S | re.I)


def with_tokens(html: str, css: str) -> str:
    """The screen with the tokens inlined in its head, replacing any copy already there, so each
    file opens on its own (in the browser, in the panel and for the Worker)."""
    style = f"<style {TOKENS_MARK}>\n{css}</style>"
    html = strip_scripts(html)
    html = re.sub(rf"<style {TOKENS_MARK}>.*?</style>\s*", "", html, flags=re.S)
    m = re.search(r"<head[^>]*>", html, re.I)
    if m:
        return html[: m.end()] + "\n" + style + html[m.end() :]
    return f"<!doctype html>\n<html><head><meta charset='utf-8'>{style}</head><body>\n{html}\n</body></html>\n"


_HEX = re.compile(r"#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b")
_RGB = re.compile(
    r"\brgba?\(\s*[\d.]+%?\s*,?\s*[\d.]+%?\s*,?\s*[\d.]+%?(?:\s*[,/]\s*[\d.]+%?)?\s*\)"
)


def normal_colour(text: str) -> str:
    """`#ABC` and `#aabbcc` are the same colour; rgb() is kept as written, without spaces."""
    t = text.strip().lower()
    if re.fullmatch(r"#[0-9a-f]{3}", t):
        return "#" + "".join(c * 2 for c in t[1:])
    if re.fullmatch(r"#[0-9a-f]{4}", t):
        return "#" + "".join(c * 2 for c in t[1:])
    return re.sub(r"\s+", "", t)


def colours_in(text: str) -> set[str]:
    return {normal_colour(m) for m in _HEX.findall(text) + _RGB.findall(text)}


def token_colours(tokens: dict[str, Any]) -> set[str]:
    return {c for _, v in _flat("", tokens) for c in colours_in(v)}


# ----------------------------------------------------------------------- manifests


def read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def rounds(factory_root: Path, story_id: str) -> list[int]:
    folder = DesignPaths.of(factory_root).story(story_id)
    if not folder.is_dir():
        return []
    out = []
    for p in folder.glob("round-*"):
        with_n = p.name.removeprefix("round-")
        if with_n.isdigit():
            out.append(int(with_n))
    return sorted(out)


def manifest(factory_root: Path, story_id: str, n: int | None = None) -> dict[str, Any]:
    """A round's manifest; with no round, the approved one (or {} when none is)."""
    paths = DesignPaths.of(factory_root)
    folder = paths.approved(story_id) if n is None else paths.round(story_id, n)
    return read_json(folder / "manifest.json")


def approved_manifest(factory_root: Path, story_id: str) -> dict[str, Any]:
    return manifest(factory_root, story_id)


def approve_round(factory_root: Path, story_id: str, n: int, *, alternative: str = "") -> Path:
    """Copy round `n` to `approved/`, keeping only `alternative` where a screen came in two."""
    paths = DesignPaths.of(factory_root)
    src, dst = paths.round(story_id, n), paths.approved(story_id)
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    data = read_json(dst / "manifest.json")
    screens = data.get("screens") or []
    if alternative:
        keep = []
        for s in screens:
            alt = str(s.get("alt") or "")
            if alt and alt != alternative:
                shutil.rmtree(dst / str(s.get("dir") or ""), ignore_errors=True)
                continue
            keep.append(s)
        screens = keep
    data["screens"] = screens
    data["approved_round"] = n
    data["alternative"] = alternative
    write_json(dst / "manifest.json", data)
    return dst


def screen_files(folder: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    """Each screen state of a manifest with its files: html, desktop and mobile pictures."""
    out = []
    for s in data.get("screens") or []:
        d = folder / str(s.get("dir") or "")
        for st in s.get("states") or []:
            name = safe_name(str(st), "main")
            out.append(
                {
                    "screen": s.get("id"),
                    "title": s.get("title"),
                    "alt": s.get("alt") or "",
                    "route": s.get("route") or "",
                    "state": name,
                    "html": d / f"{name}.html",
                    "desktop": d / f"{name}.desktop.png",
                    "mobile": d / f"{name}.mobile.png",
                }
            )
    return out


def approved_html(factory_root: Path, story_id: str, max_chars: int = 12_000) -> str:
    """The approved screens' HTML, for the Architect and the Worker (15.5 §8): the contract the
    story is built and checked against. Empty when the story has none."""
    paths = DesignPaths.of(factory_root)
    data = approved_manifest(factory_root, story_id)
    if not data:
        return ""
    parts, left = [], max_chars
    for f in screen_files(paths.approved(story_id), data):
        if not f["html"].is_file() or left <= 0:
            continue
        body = re.sub(
            rf"<style {TOKENS_MARK}>.*?</style>\s*", "", f["html"].read_text("utf-8"), flags=re.S
        )
        body = body[:left]
        left -= len(body)
        route = f" (route `{f['route']}`)" if f["route"] else ""
        parts.append(f"### {f['title'] or f['screen']} · {f['state']}{route}\n```html\n{body}\n```")
    if not parts:
        return ""
    head = (
        "## Approved screens (the founder approved these; build to match them)\n"
        "The CSS variables they use are the design tokens: use the same variables, never new "
        "colours or sizes. The example data in them is illustrative.\n"
    )
    return head + "\n\n".join(parts) + "\n\n"


def ensure_gitignore(root: Path) -> None:
    gi = Path(root) / ".gitignore"
    existing = gi.read_text(encoding="utf-8") if gi.is_file() else ""
    missing = [ln for ln in GITIGNORE_LINES if ln not in existing.splitlines()]
    if missing:
        sep = "" if not existing or existing.endswith("\n") else "\n"
        gi.write_text(existing + sep + "\n".join(missing) + "\n", encoding="utf-8")


# -------------------------------------------------------------------- contrast (WCAG)


def _luminance(hex_colour: str) -> float | None:
    c = normal_colour(hex_colour)
    if not re.fullmatch(r"#[0-9a-f]{6}([0-9a-f]{2})?", c):
        return None
    rgb = [int(c[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def contrast(a: str, b: str) -> float | None:
    la, lb = _luminance(a), _luminance(b)
    if la is None or lb is None:
        return None
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


CONTRAST_PAIRS = (("text", "bg"), ("text", "surface"), ("on-primary", "primary"))
MIN_CONTRAST = 4.5


def contrast_problems(tokens: dict[str, Any]) -> list[str]:
    """The pairs the base promises to keep readable, measured (WCAG AA for body text)."""
    colours = tokens.get("color") if isinstance(tokens.get("color"), dict) else {}
    out = []
    for fg, bg in CONTRAST_PAIRS:
        if fg in colours and bg in colours:
            ratio = contrast(str(colours[fg]), str(colours[bg]))
            if ratio is not None and ratio < MIN_CONTRAST:
                out.append(
                    f"colour `{fg}` on `{bg}` has a contrast of {ratio:.1f}:1, below "
                    f"{MIN_CONTRAST}:1"
                )
    return out


# ------------------------------------------------------------ the constitution's section

FRONTEND_HEADING = "## Front-end"
FRONTEND_RULES = (
    "- Every colour, font, size, spacing, corner and shadow comes from the design tokens "
    "(`.loompa/design/tokens.json`), used through their CSS variables; the Inspector refuses a "
    "colour outside them.",
    "- A story with approved screens is built to match them; a change to an approved screen is a "
    "new round of screens, never an adjustment hidden in the code.",
    "- Never move, resize or hide layout, nor shape data, only to pass a check (a test, a "
    "screenshot, a size limit): fix what the check found, or say it cannot be done.",
)


def frontend_section(tokens: dict[str, Any]) -> str:
    colours = tokens.get("color") if isinstance(tokens.get("color"), dict) else {}
    fonts = tokens.get("font") if isinstance(tokens.get("font"), dict) else {}
    lines = [FRONTEND_HEADING, "", *FRONTEND_RULES, ""]
    if colours:
        lines.append(
            "Colours: " + ", ".join(f"`--color-{safe_name(k)}` {v}" for k, v in colours.items())
        )
    if fonts:
        lines.append("Type: " + ", ".join(f"`--font-{safe_name(k)}` {v}" for k, v in fonts.items()))
    return "\n".join(lines).rstrip() + "\n"


def write_frontend_section(constitution: Path, tokens: dict[str, Any]) -> None:
    """Put the approved base in the constitution, replacing an older front-end section."""
    text = constitution.read_text(encoding="utf-8") if constitution.is_file() else ""
    section = frontend_section(tokens)
    pattern = re.compile(rf"^{re.escape(FRONTEND_HEADING)}\s*$.*?(?=^## |\Z)", re.M | re.S)
    if pattern.search(text):
        text = pattern.sub(section + "\n", text, count=1)
    else:
        text = text.rstrip() + "\n\n" + section
    constitution.write_text(text, encoding="utf-8")
