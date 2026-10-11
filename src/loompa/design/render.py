"""Pictures of pages at the two widths, with what code can see in them (plan 15.4 §6, 15.6).

The same headless Chrome the smoke uses (ADR-0030): each page is opened, laid out at 1280 and at
390 px, photographed, and checked in code — a page that scrolls sideways or an element sticking
out of its box, and, when axe-core ships with the package, the accessibility rules it reports as
serious or critical. No model looks at anything here.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from loompa.smoke import (
    LAYOUT_PROBE,
    LAYOUT_SETTLE_S,
    VIEWPORTS,
    SmokeResult,
    _screenshot,
    browser_page,
    find_chrome,
    layout_problems,
)

AXE_JS = Path(__file__).with_name("axe.min.js")
AXE_IMPACTS = ("serious", "critical")
PAGE_SETTLE_S = 1.0
AXE_RUN = """(async () => {
  const r = await axe.run(document, {resultTypes: ['violations']});
  return JSON.stringify(r.violations.map(v => ({id: v.id, impact: v.impact, help: v.help,
    nodes: v.nodes.length, target: (v.nodes[0] && v.nodes[0].target || []).join(' ')})));
})()"""


@dataclass
class PageLook:
    url: str
    shots: dict[str, Path] = field(default_factory=dict)  # viewport -> picture
    layout: dict[str, str] = field(default_factory=dict)  # key -> problem, as the smoke reports
    errors: list[str] = field(default_factory=list)  # JavaScript errors in the page
    a11y: list[dict] = field(default_factory=list)  # axe-core violations, serious and critical

    @property
    def problems(self) -> list[str]:
        out = list(self.layout.values())
        out += [f"JavaScript error in the page: {e}" for e in dict.fromkeys(self.errors)][:3]
        out += [
            f"accessibility ({v.get('impact')}): {v.get('help')} — {v.get('nodes')} element(s), "
            f"first at {v.get('target') or 'the page'}"
            for v in self.a11y
        ]
        return out


def axe_available() -> bool:
    return AXE_JS.is_file()


async def look_at(
    pages: list[tuple[str, Path]],
    *,
    chrome: str | None = None,
    accessibility: bool = True,
    settle_s: float = PAGE_SETTLE_S,
) -> list[PageLook] | None:
    """Open each `(url, picture prefix)` and photograph it at both widths, as
    `<prefix>.desktop.png` and `<prefix>.mobile.png`. None when there is no browser to look with."""
    browser = chrome if chrome is not None else find_chrome()
    if not browser or not pages:
        return None
    axe_src = AXE_JS.read_text(encoding="utf-8") if accessibility and axe_available() else ""
    out: list[PageLook] = []
    async with browser_page(browser) as page:
        if page is None:
            return None
        for url, prefix in pages:
            look = PageLook(url=url)
            page.errors.clear()
            await page.viewport(*VIEWPORTS[0][1:])
            await page.call("Page.navigate", url=url)
            await page.settle(settle_s)
            for viewport, width, height, mobile in VIEWPORTS:
                await page.viewport(width, height, mobile)
                await page.settle(LAYOUT_SETTLE_S)
                seen = await page.evaluate(LAYOUT_PROBE)
                look.layout.update(layout_problems(viewport, json.loads(seen or "{}")))
                shot = SmokeResult()
                target = prefix.with_name(f"{prefix.name}.{viewport}.png")
                await _screenshot(page.call, target, width, shot)
                if shot.shots:
                    look.shots[viewport] = target
            if axe_src:
                await page.viewport(*VIEWPORTS[0][1:])
                await page.evaluate(axe_src)
                raw = await page.evaluate(AXE_RUN, await_promise=True)
                try:
                    found = json.loads(raw or "[]")
                except ValueError:
                    found = []
                look.a11y = [v for v in found if v.get("impact") in AXE_IMPACTS]
            look.errors = list(page.errors)
            out.append(look)
    return out


def file_url(path: Path) -> str:
    return Path(path).resolve().as_uri()
