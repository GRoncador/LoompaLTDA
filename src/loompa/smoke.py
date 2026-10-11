"""A browser smoke test for web products: start the app, load the page, look at it.

tamagotchi Sprint 2, S-015: 108 tests were green and the Inspector passed a page whose screen was
blank. The LCD drew `estadoPet.sprite`, the simulation had no sprite, and no test ever opened the
page. Every check here is code, no model:

1. the app's start command answers on `http://127.0.0.1:<port>/` (the port comes in `PORT`);
2. the page and every module it imports, followed through relative `import`s, answer 200;
3. when a Chrome or Chromium is installed, the page is opened headless through the DevTools
   protocol: a JavaScript error is a problem, a `<canvas>` of one solid colour is a problem (nothing
   was drawn), and a page with no text and no canvas is a problem;
4. the page is laid out at desktop width and at 390 px (a phone), and looked at as a person would
   (ADR-0030): a page that scrolls sideways, or a canvas, image or control sticking out of the box
   that holds it, is a problem — tamagotchi S-054 put the screen half outside its egg-shaped shell
   and S-056 had to fix a page wider than a phone, with every test green. A layout problem the base
   already has is the base's, not the story's (`layout_baseline`), and a screenshot of each width is
   saved for the founder when `shots_dir` is given.

Without a browser the first two still run and the result says the page was not rendered.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shlex
import shutil
import signal
import socket
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx

START_TIMEOUT_S = 30.0
SETTLE_S = 3.0  # the page's own timers run this long before it is looked at
CHROME_PATHS = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)
CHROME_NAMES = ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")
_MODULE_TAG = re.compile(
    r"<script\b[^>]*\btype=[\"']module[\"'][^>]*\bsrc=[\"']([^\"']+)[\"']", re.I
)
_MODULE_TAG_REV = re.compile(
    r"<script\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*\btype=[\"']module[\"']", re.I
)
_IMPORT = re.compile(
    r"""(?:^|[;\s])(?:import|export)\s[^'"]*?from\s*['"](\.{1,2}/[^'"]+)['"]""", re.M
)
_BARE_IMPORT = re.compile(r"""(?:^|[;\s])import\s*['"](\.{1,2}/[^'"]+)['"]""", re.M)
# Run in the page: what a person would see. A canvas whose pixels are all one colour shows nothing.
PAGE_PROBE = """(() => {
  const out = {text: document.body ? document.body.innerText.trim().length : 0, canvases: []};
  for (const c of document.querySelectorAll('canvas')) {
    let colours = -1, edges = {};
    try {
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      const seen = new Set();
      for (let i = 0; i < d.length && seen.size < 2; i += 4) seen.add(d[i] + ',' + d[i+1] + ',' + d[i+2] + ',' + d[i+3]);
      colours = seen.size;
      // pixels unlike the top-left one along each 1-px edge: a drawing cut by the edge
      const W = c.width, H = c.height, bg = d.slice(0, 4).join(',');
      const at = (x, y) => { const i = (y * W + x) * 4; return d.slice(i, i + 4).join(','); };
      const side = (n, f) => { let k = 0; for (let j = 0; j < n; j++) if (at(...f(j)) !== bg) k++; return k / n; };
      edges = {top: side(W, j => [j, 0]), bottom: side(W, j => [j, H - 1]),
               left: side(H, j => [0, j]), right: side(H, j => [W - 1, j])};
    } catch (e) {}
    out.canvases.push({id: c.id || '', width: c.width, height: c.height, colours, edges});
  }
  return JSON.stringify(out);
})()"""


# The widths the page is laid out at: a desktop window and a phone (390 px, the width the
# tamagotchi founder asked the page to fit).
VIEWPORTS = (("desktop", 1280, 900, False), ("mobile", 390, 844, True))
SHOT_MAX_HEIGHT = 2400  # a long page is cut here: the screenshot is for a glance, not a scroll
LAYOUT_SETTLE_S = 0.6
# Run in the page at each width. A canvas, image or control whose box goes past the nearest
# ancestor that draws a box (a background, a border, or clipping its overflow) sticks out of it.
LAYOUT_PROBE = """(() => {
  const name = (e) => {
    if (e.id) return '#' + e.id;
    const c = typeof e.className === 'string' ? e.className.trim().split(/\\s+/)[0] : '';
    return e.tagName.toLowerCase() + (c ? '.' + c : '');
  };
  const draws = (e) => {
    const s = getComputedStyle(e);
    const bg = s.backgroundColor;
    return s.overflowX !== 'visible' || s.overflowY !== 'visible' || s.backgroundImage !== 'none'
      || (bg && bg !== 'transparent' && bg !== 'rgba(0, 0, 0, 0)')
      || ['Top', 'Right', 'Bottom', 'Left'].some((k) => parseFloat(s['border' + k + 'Width']) > 0);
  };
  // the corner radii of a box in px, scaled down as CSS does when they add up past a side
  const radii = (e, b) => {
    const s = getComputedStyle(e);
    const px = (v, len) => v.endsWith('%') ? parseFloat(v) * len / 100 : parseFloat(v) || 0;
    const get = (k) => { const [h, v] = s[k].split(' '); return [px(h, b.width), px(v || h, b.height)]; };
    const r = {tl: get('borderTopLeftRadius'), tr: get('borderTopRightRadius'),
               br: get('borderBottomRightRadius'), bl: get('borderBottomLeftRadius')};
    const f = Math.min(1, b.width / (r.tl[0] + r.tr[0] || 1), b.width / (r.bl[0] + r.br[0] || 1),
                       b.height / (r.tl[1] + r.bl[1] || 1), b.height / (r.tr[1] + r.br[1] || 1));
    for (const k in r) r[k] = [r[k][0] * f, r[k][1] * f];
    return r;
  };
  // is (x, y) inside the rounded box? 2 px of slack, the width of an antialiased edge
  const inside = (x, y, b, r) => {
    const t = 2;
    if (x < b.left - t || x > b.right + t || y < b.top - t || y > b.bottom + t) return false;
    const corners = [[r.tl, b.left + r.tl[0], b.top + r.tl[1], x < b.left + r.tl[0] && y < b.top + r.tl[1]],
                     [r.tr, b.right - r.tr[0], b.top + r.tr[1], x > b.right - r.tr[0] && y < b.top + r.tr[1]],
                     [r.br, b.right - r.br[0], b.bottom - r.br[1], x > b.right - r.br[0] && y > b.bottom - r.br[1]],
                     [r.bl, b.left + r.bl[0], b.bottom - r.bl[1], x < b.left + r.bl[0] && y > b.bottom - r.bl[1]]];
    for (const [[rx, ry], cx, cy, inCorner] of corners) {
      if (!inCorner || rx < 1 || ry < 1) continue;
      const dx = Math.max(0, Math.abs(x - cx) - t) / rx, dy = Math.max(0, Math.abs(y - cy) - t) / ry;
      if (dx * dx + dy * dy > 1) return false;
    }
    return true;
  };
  // the screen's width: on a phone `innerWidth` grows to fit whatever overflows (857 for 390)
  const out = {width: document.documentElement.clientWidth, content: document.documentElement.scrollWidth, sticking: []};
  for (const el of document.querySelectorAll('canvas, img, video, svg, button, input, select, textarea')) {
    const r = el.getBoundingClientRect();
    if (r.width < 1 || r.height < 1 || getComputedStyle(el).position === 'fixed') continue;
    let box = el.parentElement;
    while (box && box !== document.body && box !== document.documentElement && !draws(box)) box = box.parentElement;
    if (!box || box === document.body || box === document.documentElement) continue;
    const b = box.getBoundingClientRect();
    const over = {left: b.left - r.left, right: r.right - b.right, top: b.top - r.top, bottom: r.bottom - b.bottom};
    const sides = Object.entries(over).filter(([, v]) => v > 2).map(([k, v]) => [k, Math.round(v)]);
    if (sides.length) { out.sticking.push({el: name(el), box: name(box), sides}); continue; }
    // inside the rectangle, maybe not inside the shape: an egg-shaped shell (tamagotchi S-054)
    const rr = radii(box, b);
    const cut = [['top-left', r.left, r.top], ['top-right', r.right, r.top],
                 ['bottom-right', r.right, r.bottom], ['bottom-left', r.left, r.bottom]]
      .filter(([, x, y]) => !inside(x, y, b, rr)).map(([k]) => k);
    if (cut.length) out.sticking.push({el: name(el), box: name(box), corners: cut});
  }
  return JSON.stringify(out);
})()"""


def layout_problems(viewport: str, seen: dict) -> dict[str, str]:
    """`{key: problem}` from one LAYOUT_PROBE reading. The key leaves the pixels out, so the same
    problem on the base and on the story is recognised as one."""
    width = int(seen.get("width") or 0)
    out: dict[str, str] = {}
    if width and int(seen.get("content") or 0) > width + 1:
        out[f"{viewport}:scroll"] = (
            f"at {width} px wide the page scrolls sideways: its content is "
            f"{seen['content']} px wide"
        )
    for item in seen.get("sticking") or []:
        el, box = item.get("el"), item.get("box")
        if item.get("corners"):
            where = f"its {', '.join(item['corners'])} corner(s) cross {box}'s rounded edge"
        else:
            where = "by " + " and ".join(
                f"{px} px on the {side}" for side, px in item.get("sides") or []
            )
        out[f"{viewport}:{el}>{box}"] = f"at {width} px wide, {el} sticks out of {box}: {where}"
    return out


@dataclass
class SmokeResult:
    ran: bool = False
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    # every layout problem seen, the base's own included: what a baseline run records
    layout: dict[str, str] = field(default_factory=dict)
    rendered: bool = False
    shots: list[str] = field(default_factory=list)  # file names written under `shots_dir`

    @property
    def ok(self) -> bool:
        return not self.problems

    def report(self) -> str:
        if not self.ran:
            return ""
        head = "[smoke] PASS" if self.ok else "[smoke] FAIL"
        lines = [f"- {p}" for p in self.problems] + [f"  ({n})" for n in self.notes]
        return "\n".join([head, *lines])


def start_command(root: Path, configured: str | None) -> str:
    """The configured command; with none configured (None), `npm start` when the project is a web
    page with a start script; "" turns the smoke test off."""
    if configured is not None:
        return configured.strip()
    pkg = root / "package.json"
    if not (root / "index.html").is_file() or not pkg.is_file():
        return ""
    try:
        scripts = json.loads(pkg.read_text(encoding="utf-8")).get("scripts") or {}
    except (OSError, ValueError):
        return ""
    return "npm start" if scripts.get("start") else ""


def find_chrome() -> str | None:
    if env := os.environ.get("LOOMPA_CHROME"):
        return env if Path(env).exists() else None
    for p in CHROME_PATHS:
        if Path(p).exists():
            return p
    return next((w for n in CHROME_NAMES if (w := shutil.which(n))), None)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _spawn(argv: list[str], cwd: Path, env: dict[str, str]) -> asyncio.subprocess.Process:
    return await asyncio.create_subprocess_exec(
        *argv,
        cwd=cwd,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,  # the whole group goes down with it (npm starts node)
    )


async def _stop(proc: asyncio.subprocess.Process | None) -> str:
    if proc is None:
        return ""
    if proc.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(proc.wait(), 5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
    out = b""
    if proc.stdout is not None:
        with contextlib.suppress(Exception):
            out = await asyncio.wait_for(proc.stdout.read(), 2)
    return out.decode("utf-8", "replace")[-600:]


def module_refs(html_or_js: str, *, html: bool) -> list[str]:
    if html:
        return list(
            dict.fromkeys(_MODULE_TAG.findall(html_or_js) + _MODULE_TAG_REV.findall(html_or_js))
        )
    return list(dict.fromkeys(_IMPORT.findall(html_or_js) + _BARE_IMPORT.findall(html_or_js)))


async def _check_modules(client: httpx.AsyncClient, base: str, page: str, out: SmokeResult) -> None:
    queue = [urljoin(base, ref) for ref in module_refs(page, html=True)]
    seen: set[str] = set()
    while queue and len(seen) < 200:
        url = queue.pop()
        if url in seen or urlparse(url).netloc != urlparse(base).netloc:
            continue
        seen.add(url)
        r = await client.get(url)
        path = urlparse(url).path
        if r.status_code != 200:
            out.problems.append(f"the page imports {path}, which answers {r.status_code}")
            continue
        if "javascript" not in r.headers.get("content-type", ""):
            out.problems.append(
                f"{path} is served as {r.headers.get('content-type') or 'no type'}, not JavaScript: "
                "the browser refuses to run it as a module"
            )
        queue += [urljoin(url, ref) for ref in module_refs(r.text, html=False)]
    if seen:
        out.notes.append(f"{len(seen)} module(s) loaded")


class BrowserPage:
    """One headless Chrome tab driven through the DevTools protocol: `call` sends a command and
    returns its result, `settle` lets the page's own timers run while errors are collected."""

    def __init__(self, ws) -> None:  # type: ignore[no-untyped-def]
        self.ws = ws
        self.errors: list[str] = []
        self._ids = iter(range(1, 1_000_000))

    async def call(self, method: str, **params: object) -> dict:
        n = next(self._ids)
        await self.ws.send(json.dumps({"id": n, "method": method, "params": params}))
        while True:
            msg = json.loads(await asyncio.wait_for(self.ws.recv(), 15))
            _collect(msg, self.errors)
            if msg.get("id") == n:
                return msg.get("result") or {}

    async def settle(self, seconds: float) -> None:
        loop = asyncio.get_running_loop()
        end = loop.time() + seconds
        while (left := end - loop.time()) > 0:
            with contextlib.suppress(TimeoutError):
                _collect(json.loads(await asyncio.wait_for(self.ws.recv(), left)), self.errors)

    async def evaluate(self, expression: str, *, await_promise: bool = False) -> str:
        res = await self.call(
            "Runtime.evaluate",
            expression=expression,
            returnByValue=True,
            awaitPromise=await_promise,
        )
        return str((res.get("result") or {}).get("value") or "")

    async def viewport(self, width: int, height: int, mobile: bool) -> None:
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            width=width,
            height=height,
            deviceScaleFactor=1,
            mobile=mobile,
        )


@contextlib.asynccontextmanager
async def browser_page(chrome: str):  # type: ignore[no-untyped-def]
    """A headless Chrome with one blank tab, stopped and its profile removed on the way out.
    Yields None when the browser did not start."""
    import websockets  # comes with uvicorn[standard], the dashboard's server

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="loompa-smoke-")
    argv = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--allow-file-access-from-files",
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile}",
        "--window-size=900,800",
        "about:blank",
    ]
    proc = await _spawn(argv, Path(profile), dict(os.environ))
    try:
        ws_url = ""
        async with httpx.AsyncClient(timeout=2) as client:
            for _ in range(60):
                with contextlib.suppress(httpx.HTTPError, ValueError):
                    pages = (await client.get(f"http://127.0.0.1:{port}/json")).json()
                    ws_url = next(
                        (p["webSocketDebuggerUrl"] for p in pages if p.get("type") == "page"), ""
                    )
                    if ws_url:
                        break
                await asyncio.sleep(0.25)
        if not ws_url:
            yield None
            return
        async with websockets.connect(ws_url, max_size=2**25) as ws:
            page = BrowserPage(ws)
            await page.call("Runtime.enable")
            await page.call("Log.enable")
            await page.call("Page.enable")
            yield page
    finally:
        await _stop(proc)
        shutil.rmtree(profile, ignore_errors=True)


async def _render(
    chrome: str,
    url: str,
    out: SmokeResult,
    *,
    shots_dir: Path | None = None,
    layout_baseline: set[str] | None = None,
) -> None:
    async with browser_page(chrome) as page:
        if page is None:
            out.notes.append("the browser did not start; the page was not rendered")
            return
        await page.call("Page.navigate", url=url)
        await page.settle(SETTLE_S)
        probe = await page.evaluate(PAGE_PROBE)
        for viewport, width, height, mobile in VIEWPORTS:
            await page.viewport(width, height, mobile)
            await page.settle(LAYOUT_SETTLE_S)
            seen_at = await page.evaluate(LAYOUT_PROBE)
            out.layout.update(layout_problems(viewport, json.loads(seen_at or "{}")))
            if shots_dir is not None:
                await _screenshot(page.call, shots_dir / f"{viewport}.png", width, out)
        out.rendered = True
        errors = page.errors
    out.problems += [f"JavaScript error in the page: {e}" for e in dict.fromkeys(errors)][:5]
    seen = json.loads(probe or "{}")
    canvases = seen.get("canvases") or []
    for c in canvases:
        if c.get("colours") == 1:
            name = f"#{c['id']}" if c.get("id") else "a canvas"
            out.problems.append(
                f"{name} ({c.get('width')}x{c.get('height')}) is one solid colour after "
                f"{SETTLE_S:.0f}s: nothing was drawn on it"
            )
    for c in canvases:
        if cut := clipped_edges(c.get("edges") or {}):
            name = f"#{c['id']}" if c.get("id") else "a canvas"
            out.problems.append(
                f"{name}: the drawing touches the {' and '.join(cut)} edge(s) — something "
                "is cut off or drawn past the canvas"
            )
    if not canvases and not seen.get("text"):
        out.problems.append("the page shows no text and no canvas")
    known = layout_baseline or set()
    out.problems += [text for key, text in out.layout.items() if key not in known]
    if already := [text for key, text in out.layout.items() if key in known]:
        out.notes.append(f"{len(already)} layout problem(s) the base already has")
    out.notes.append(f"rendered in {Path(chrome).name}")


async def _screenshot(call, path: Path, width: int, out: SmokeResult) -> None:  # type: ignore[no-untyped-def]
    """The page as it is laid out now, whole up to SHOT_MAX_HEIGHT."""
    import base64

    metrics = await call("Page.getLayoutMetrics")
    size = metrics.get("cssContentSize") or metrics.get("contentSize") or {}
    height = min(int(size.get("height") or 800), SHOT_MAX_HEIGHT)
    shot = await call(
        "Page.captureScreenshot",
        format="png",
        captureBeyondViewport=True,
        clip={"x": 0, "y": 0, "width": width, "height": height, "scale": 1},
    )
    if data := shot.get("data"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(data))
        out.shots.append(path.name)


def clipped_edges(edges: dict[str, float]) -> list[str]:
    """Edges where part of the drawing reaches the canvas border (tamagotchi S-048: a sprite
    grown past the screen, its hand and feet cut, passed every test). A border drawn all round
    (a frame) or a filled edge is a design; a few pixels on one side are a cut."""
    framed = [k for k, v in edges.items() if v >= 0.9]
    if len(framed) == 4:
        return []
    return [k for k in ("top", "bottom", "left", "right") if 0 < edges.get(k, 0) < 0.9]


def _collect(msg: dict, errors: list[str]) -> None:
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "Runtime.exceptionThrown":
        d = params.get("exceptionDetails") or {}
        text = ((d.get("exception") or {}).get("description") or d.get("text") or "").splitlines()
        errors.append((text[0] if text else "uncaught exception")[:200])
    elif method == "Runtime.consoleAPICalled" and params.get("type") == "error":
        args = params.get("args") or []
        errors.append(" ".join(str(a.get("value", a.get("description", ""))) for a in args)[:200])
    elif method == "Log.entryAdded":
        entry = params.get("entry") or {}
        if entry.get("level") == "error" and entry.get("source") != "network":
            errors.append(str(entry.get("text", ""))[:200])


async def smoke(
    root: Path,
    command: str,
    *,
    chrome: str | None = None,
    shots_dir: Path | None = None,
    layout_baseline: set[str] | None = None,
) -> SmokeResult:
    """Start `command` in `root`, check the page and its modules, render it when a browser is
    found. The app and the browser are always stopped. `layout_baseline` are the layout problems
    the base already has (keys of `SmokeResult.layout`); `shots_dir` gets one screenshot per
    viewport."""
    out = SmokeResult(ran=True)
    port = _free_port()
    env = {**os.environ, "PORT": str(port)}
    base = f"http://127.0.0.1:{port}/"
    server = None
    try:
        server = await _spawn(shlex.split(command), root, env)
        page = ""
        async with httpx.AsyncClient(timeout=5) as client:
            loop = asyncio.get_running_loop()
            end = loop.time() + START_TIMEOUT_S
            while loop.time() < end and server.returncode is None:
                with contextlib.suppress(httpx.HTTPError):
                    r = await client.get(base)
                    if r.status_code == 200:
                        page = r.text
                        break
                await asyncio.sleep(0.3)
            if not page:
                tail = await _stop(server)
                server = None
                out.problems.append(
                    f"`{command}` did not serve the page on {base} within {START_TIMEOUT_S:.0f}s"
                    + (f"; its output ended with: {tail.strip()[-300:]}" if tail.strip() else "")
                )
                return out
            await _check_modules(client, base, page, out)
        browser = chrome if chrome is not None else find_chrome()
        if browser:
            await _render(browser, base, out, shots_dir=shots_dir, layout_baseline=layout_baseline)
        else:
            out.notes.append("no Chrome or Chromium found: the page was loaded but not rendered")
        return out
    finally:
        await _stop(server)
