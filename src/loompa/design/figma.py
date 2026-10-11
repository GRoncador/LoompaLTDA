"""Reading a Figma frame from its link (plan 15.9, the optional bridge).

With a personal token in a secrets file (its variable named by `design.figma_token_env`), the
REST API gives the picture of a frame and its structure. That is enough for the founder or a
designer to send a reference, or a screen already drawn, which can then stand in for the UX
Loompa's picture as the approved one. Nothing is ever written to Figma: writing to the canvas
needs a client listed in Figma's MCP catalog, which the Loompa is not (see the plan, Phase 15).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

import httpx

API = "https://api.figma.com/v1"
_PATH = re.compile(r"/(?:file|design|proto|board)/([A-Za-z0-9]+)")


class FigmaError(ValueError):
    pass


@dataclass(frozen=True)
class FrameRef:
    key: str
    node: str  # "12:34"


def parse_link(url: str) -> FrameRef:
    """`https://www.figma.com/design/AbC123/Name?node-id=12-34` -> key AbC123, node 12:34."""
    u = urlparse(url.strip())
    if not u.netloc.endswith("figma.com"):
        raise FigmaError("o link não é do Figma")
    m = _PATH.search(u.path)
    if not m:
        raise FigmaError("o link não aponta para um arquivo do Figma")
    node = (parse_qs(u.query).get("node-id") or [""])[0].replace("-", ":")
    if not re.fullmatch(r"\d+:\d+", node):
        raise FigmaError("copie o link de um quadro (com node-id), não do arquivo inteiro")
    return FrameRef(m.group(1), node)


def _names(node: dict, depth: int = 0, out: list[str] | None = None) -> list[str]:
    """The frame's layers as an indented outline: names and texts, the structure a person sees."""
    out = [] if out is None else out
    if len(out) >= 120:
        return out
    label = node.get("name") or ""
    if node.get("type") == "TEXT" and node.get("characters"):
        label = f"{label}: “{str(node['characters'])[:80]}”"
    if label:
        out.append("  " * depth + f"- {node.get('type', '').lower()} {label}".rstrip())
    for child in node.get("children") or []:
        _names(child, depth + 1, out)
    return out


async def fetch_frame(
    url: str, token: str, *, client: httpx.AsyncClient | None = None
) -> tuple[bytes, str]:
    """The frame's picture (PNG at 2x) and its outline."""
    if not token:
        raise FigmaError("falta o token do Figma nas chaves")
    ref = parse_link(url)
    headers = {"X-Figma-Token": token}
    own = client is None
    client = client or httpx.AsyncClient(timeout=30)
    try:
        r = await client.get(
            f"{API}/images/{ref.key}",
            params={"ids": ref.node, "format": "png", "scale": 2},
            headers=headers,
        )
        if r.status_code in (401, 403):
            raise FigmaError("o Figma recusou o token: confira a chave nas configurações")
        if r.status_code == 404:
            raise FigmaError("o Figma não achou esse arquivo ou quadro")
        r.raise_for_status()
        image_url = (r.json().get("images") or {}).get(ref.node)
        if not image_url:
            raise FigmaError("o Figma não gerou a imagem desse quadro")
        picture = (await client.get(image_url)).content
        nodes = await client.get(
            f"{API}/files/{ref.key}/nodes", params={"ids": ref.node}, headers=headers
        )
        doc = ((nodes.json().get("nodes") or {}).get(ref.node) or {}).get("document") or {}
        return picture, "\n".join(_names(doc))
    except httpx.HTTPError as exc:
        raise FigmaError("não consegui falar com o Figma agora") from exc
    finally:
        if own:
            await client.aclose()
