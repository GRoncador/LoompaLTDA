"""A browser smoke test for web products: start the app, load the page, look at it.

tamagotchi Sprint 2, S-015: 108 tests were green and the Inspector passed a page whose screen was
blank. The LCD drew `estadoPet.sprite`, the simulation had no sprite, and no test ever opened the
page. Every check here is code, no model:

1. the app's start command answers on `http://127.0.0.1:<port>/` (the port comes in `PORT`);
2. the page and every module it imports, followed through relative `import`s, answer 200;
3. when a Chrome or Chromium is installed, the page is opened headless through the DevTools
   protocol: a JavaScript error is a problem, a `<canvas>` of one solid colour is a problem (nothing
   was drawn), and a page with no text and no canvas is a problem.

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
    let colours = -1;
    try {
      const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data;
      const seen = new Set();
      for (let i = 0; i < d.length && seen.size < 2; i += 4) seen.add(d[i] + ',' + d[i+1] + ',' + d[i+2] + ',' + d[i+3]);
      colours = seen.size;
    } catch (e) {}
    out.canvases.push({id: c.id || '', width: c.width, height: c.height, colours});
  }
  return JSON.stringify(out);
})()"""


@dataclass
class SmokeResult:
    ran: bool = False
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

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


async def _render(chrome: str, url: str, out: SmokeResult) -> None:
    import websockets  # comes with uvicorn[standard], the dashboard's server

    port = _free_port()
    profile = tempfile.mkdtemp(prefix="loompa-smoke-")
    argv = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
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
            out.notes.append("the browser did not start; the page was not rendered")
            return
        errors: list[str] = []
        async with websockets.connect(ws_url, max_size=2**24) as ws:
            ids = iter(range(1, 10_000))

            async def call(method: str, **params: object) -> dict:
                n = next(ids)
                await ws.send(json.dumps({"id": n, "method": method, "params": params}))
                while True:
                    msg = json.loads(await asyncio.wait_for(ws.recv(), 15))
                    _collect(msg, errors)
                    if msg.get("id") == n:
                        return msg.get("result") or {}

            await call("Runtime.enable")
            await call("Log.enable")
            await call("Page.enable")
            await call("Page.navigate", url=url)
            loop = asyncio.get_running_loop()
            end = loop.time() + SETTLE_S
            while (left := end - loop.time()) > 0:
                with contextlib.suppress(TimeoutError):
                    _collect(json.loads(await asyncio.wait_for(ws.recv(), left)), errors)
            probe = await call("Runtime.evaluate", expression=PAGE_PROBE, returnByValue=True)
        out.problems += [f"JavaScript error in the page: {e}" for e in dict.fromkeys(errors)][:5]
        value = ((probe.get("result") or {}).get("value")) or "{}"
        seen = json.loads(value)
        canvases = seen.get("canvases") or []
        for c in canvases:
            if c.get("colours") == 1:
                name = f"#{c['id']}" if c.get("id") else "a canvas"
                out.problems.append(
                    f"{name} ({c.get('width')}x{c.get('height')}) is one solid colour after "
                    f"{SETTLE_S:.0f}s: nothing was drawn on it"
                )
        if not canvases and not seen.get("text"):
            out.problems.append("the page shows no text and no canvas")
        out.notes.append(f"rendered in {Path(chrome).name}")
    finally:
        await _stop(proc)
        shutil.rmtree(profile, ignore_errors=True)


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


async def smoke(root: Path, command: str, *, chrome: str | None = None) -> SmokeResult:
    """Start `command` in `root`, check the page and its modules, render it when a browser is
    found. The app and the browser are always stopped."""
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
            await _render(browser, base, out)
        else:
            out.notes.append("no Chrome or Chromium found: the page was loaded but not rendered")
        return out
    finally:
        await _stop(server)
