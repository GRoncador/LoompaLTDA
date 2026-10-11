"""The Inspector's look at front-end code against the design (plan 15.3, 15.6, 15.7).

Two checks, both run on every story once the factory has a visual base:

1. **Tokens, in code.** A colour written into a style file, a page or a component template that
   is not one of the design tokens fails the story: the base the founder chose is the only
   palette (15.3). Scripts are left out on purpose: a game draws its sprites with colours in code.
2. **The built page against the approved screen.** For each approved screen with a route, the
   product is started (the smoke's command), the route is photographed at both widths and each
   picture is compared in code with the approved one (`design.compare`). Past the factory's
   threshold, the UX Loompa looks at both side by side with the diff and says whether the page
   deviates from what was approved, or bends layout or data only to pass a check (15.7). A
   deviation or a workaround fails the story with the two pictures as evidence; a difference
   nobody could judge (no vision model) is a finding for the founder. The accessibility faults
   axe-core reports on those pages are findings too.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from loompa.design import (
    DesignPaths,
    approved_manifest,
    colours_in,
    load_tokens,
    screen_files,
    token_colours,
)
from loompa.design.compare import difference, side_by_side
from loompa.design.render import look_at
from loompa.hygiene import _files, is_test_path
from loompa.smoke import served

if TYPE_CHECKING:
    from loompa.agents.ux import UXAgent
    from loompa.engine.state import StoryState

STYLE_SUFFIXES = (".css", ".scss", ".sass", ".less", ".html", ".htm", ".vue", ".svelte")
ALWAYS_ALLOWED = {"#00000000", "transparent"}
COMPARE_PREFIX = "compare-"


@dataclass
class DesignCheck:
    problems: list[str] = field(default_factory=list)  # blocking: back to the Worker
    findings: list[dict[str, str]] = field(default_factory=list)  # for the founder
    shots: list[str] = field(default_factory=list)  # side-by-side pictures, in the shots folder
    notes: list[str] = field(default_factory=list)

    def report(self) -> str:
        if not (self.problems or self.notes):
            return ""
        head = "[design] FAIL" if self.problems else "[design] PASS"
        return "\n".join(
            [head, *(f"- {p}" for p in self.problems), *(f"  ({n})" for n in self.notes)]
        )


def stray_colours(diff: str, tokens: dict) -> list[tuple[str, str]]:
    """Colours the diff adds to style files and page templates that are not design tokens."""
    allowed = token_colours(tokens) | ALWAYS_ALLOWED
    out: list[tuple[str, str]] = []
    for f in _files(diff):
        if f.deleted or not f.path.lower().endswith(STYLE_SUFFIXES) or is_test_path(f.path):
            continue
        if f.path.startswith(".loompa/"):
            continue
        for line in f.added or []:
            for colour in sorted(colours_in(line) - allowed):
                out.append((f.path, colour))
    return list(dict.fromkeys(out))


async def check_design(
    root: Path,
    worktree: Path,
    state: StoryState,
    diff: str,
    *,
    smoke_cmd: str,
    shots_dir: Path,
    threshold: float,
    ux: UXAgent | None,
) -> DesignCheck:
    out = DesignCheck()
    tokens = load_tokens(root)
    if tokens:
        stray = stray_colours(diff, tokens)
        for path, colour in stray[:8]:
            out.problems.append(
                f"{path} uses the colour {colour}, which is not one of the design tokens: use the "
                "token's CSS variable (see the constitution's Front-end section)"
            )
    data = approved_manifest(root, state.story_id)
    if not data or not smoke_cmd:
        return out
    paths = DesignPaths.of(root)
    screens = [
        f
        for f in screen_files(paths.approved(state.story_id), data)
        if f["state"] == "main" and f["route"]
    ]
    if not screens:
        out.notes.append("the approved screens name no route to compare with")
        return out
    async with served(worktree, smoke_cmd) as app:
        if not app.base:
            out.notes.append("the product did not start: the screens were not compared")
            return out
        targets = [
            (
                app.base.rstrip("/") + "/" + f["route"].lstrip("/"),
                shots_dir / f"built-{f['screen']}",
            )
            for f in screens
        ]
        looks = await look_at(targets)
    if looks is None:
        out.notes.append("no browser: the screens were not compared")
        return out
    for f, look in zip(screens, looks, strict=False):
        for v in ("desktop", "mobile"):
            built, approved = look.shots.get(v), f[v]
            if built is None or not approved.is_file():
                continue
            moved = difference(approved, built)
            if moved <= threshold:
                continue
            name = f"{COMPARE_PREFIX}{f['screen']}-{v}.png"
            picture = side_by_side(approved, built, shots_dir / name)
            out.shots.append(name)
            width = "computer" if v == "desktop" else "phone"
            label = f"{f['title']} at {width} width: approved on the left, built on the right"
            seen = await ux.compare(state, picture, label, _front_diff(diff)) if ux else None
            where = f"“{f['title']}” ({f['route']}, {width})"
            if seen is None:
                out.findings.append(
                    {
                        "severity": "medium",
                        "prefix": "UX",
                        "text": f"{where} differs from the approved screen in {moved:.0%} of the "
                        "picture; nobody with vision compared them",
                    }
                )
            elif seen["deviation"] or seen["workaround"]:
                why = "; ".join(seen["changes"]) or "differs from the approved screen"
                kind = "bends layout or data to pass a check" if seen["workaround"] else "deviates"
                out.problems.append(f"{where} {kind}: {why}")
        for v in look.a11y[:3]:
            out.findings.append(
                {
                    "severity": "medium",
                    "prefix": "UX",
                    "text": f"accessibility on {f['route']}: {v.get('help')} "
                    f"({v.get('nodes')} element(s))",
                }
            )
    return out


def _front_diff(diff: str, limit: int = 12_000) -> str:
    """The diff's front-end files only: what the visual reviewer reads for workarounds."""
    keep, on = [], False
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            on = any(
                line.lower().endswith(s) for s in (*STYLE_SUFFIXES, ".js", ".jsx", ".ts", ".tsx")
            )
        if on:
            keep.append(line)
    return "\n".join(keep)[:limit]
