"""Pictures in code: the logo's vector, the brand's files, pixel art and the pieces' checks
(plan 15.14, 15.15; ADR-0040).

* **Vector, in code first.** The chosen flat logo is traced locally with vtracer (MIT): a faithful
  copy, free, that does not reinterpret the drawing. The trace is checked in code — the SVG is
  rendered back in the headless browser and compared with the chosen picture, and a noisy trace
  (hundreds of paths) is refused. Only when it fails does the configured vector model redraw it,
  and the founder is told the drawing was reinterpreted.
* **Brand files.** `design/brand/`: `logo.svg`, PNGs at the sizes a product uses, `favicon.ico`.
* **Pixel art.** Models imitate the style without keeping a pixel grid: the picture is shrunk to
  the grid (nearest neighbour), held to the palette, and grown back by whole pixels.
* **Checks of a piece.** Size, format, transparency where the background must be clear, file
  weight, and a valid SVG.
"""

from __future__ import annotations

import base64
import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_PATHS = 300
TRACE_TOLERANCE = 0.06  # share of moved pixels the rendered trace may differ by
PNG_SIZES = (16, 32, 48, 180, 192, 512)
MAX_PIECE_BYTES = 600_000


@dataclass
class TraceResult:
    ok: bool
    svg: Path | None = None
    paths: int = 0
    moved: float | None = None  # difference between the rendered trace and the picture
    reason: str = ""


def count_paths(svg_text: str) -> int:
    return len(re.findall(r"<(?:path|polygon|rect|circle|ellipse)\b", svg_text))


def valid_svg(svg_text: str) -> bool:
    try:
        root = ET.fromstring(svg_text)
    except ET.ParseError:
        return False
    return root.tag.endswith("svg")


async def rasterize_svg(svg: Path, size: int, out: Path) -> Path | None:
    """The SVG drawn by the headless browser at `size` px square, or None without a browser."""
    from loompa.smoke import browser_page, find_chrome

    chrome = find_chrome()
    if not chrome:
        return None
    page_html = out.with_suffix(".html")
    data = base64.b64encode(svg.read_bytes()).decode()
    page_html.write_text(
        "<!doctype html><html><body style='margin:0;background:transparent'>"
        f"<img src='data:image/svg+xml;base64,{data}' width='{size}' height='{size}' "
        "style='display:block;object-fit:contain'></body></html>",
        encoding="utf-8",
    )
    async with browser_page(chrome) as page:
        if page is None:
            return None
        await page.viewport(size, size, False)
        await page.call(
            "Emulation.setDefaultBackgroundColorOverride", color={"r": 0, "g": 0, "b": 0, "a": 0}
        )
        await page.call("Page.navigate", url=page_html.resolve().as_uri())
        await page.settle(0.8)
        shot = await page.call(
            "Page.captureScreenshot",
            format="png",
            clip={"x": 0, "y": 0, "width": size, "height": size, "scale": 1},
        )
    page_html.unlink(missing_ok=True)
    if not shot.get("data"):
        return None
    out.write_bytes(base64.b64decode(shot["data"]))
    return out


def _flatten(png: Path, out: Path, size: int = 512) -> Path:
    """The picture on white, square, at `size`: what both sides of the comparison are put on."""
    from PIL import Image

    img = Image.open(png).convert("RGBA")
    img.thumbnail((size, size))
    canvas = Image.new("RGBA", (size, size), (255, 255, 255, 255))
    canvas.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
    canvas.convert("RGB").save(out)
    return out


async def trace(png: Path, svg: Path) -> TraceResult:
    """vtracer over the chosen picture, then the checks (paths, and the picture drawn back)."""
    try:
        import vtracer
    except ImportError:
        return TraceResult(False, reason="vtracer is not installed")
    svg.parent.mkdir(parents=True, exist_ok=True)
    vtracer.convert_image_to_svg_py(
        str(png),
        str(svg),
        colormode="color",
        hierarchical="stacked",
        mode="spline",
        filter_speckle=8,
        color_precision=6,
        layer_difference=24,
        corner_threshold=60,
        length_threshold=4.0,
        splice_threshold=45,
        path_precision=3,
    )
    text = svg.read_text(encoding="utf-8")
    paths = count_paths(text)
    if not valid_svg(text):
        return TraceResult(False, svg, paths, reason="the trace is not a valid SVG")
    if paths > MAX_PATHS:
        return TraceResult(False, svg, paths, reason=f"{paths} paths: a noisy trace")
    drawn = await rasterize_svg(svg, 512, svg.with_suffix(".check.png"))
    if drawn is None:
        return TraceResult(True, svg, paths, reason="not drawn back: no browser to check with")
    from loompa.design.compare import difference

    a = _flatten(png, svg.with_suffix(".src.png"))
    b = _flatten(drawn, svg.with_suffix(".back.png"))
    moved = difference(a, b)
    for p in (a, b, drawn):
        p.unlink(missing_ok=True)
    if moved > TRACE_TOLERANCE:
        return TraceResult(False, svg, paths, moved, f"the trace differs in {moved:.0%}")
    return TraceResult(True, svg, paths, moved)


def export_brand(png: Path, svg: Path | None, folder: Path) -> list[str]:
    """`logo.svg`, `logo-<size>.png` and `favicon.ico` in `folder`. Returns the names written."""
    from PIL import Image

    folder.mkdir(parents=True, exist_ok=True)
    written = []
    if svg is not None and svg.is_file() and svg.resolve() != (folder / "logo.svg").resolve():
        (folder / "logo.svg").write_text(svg.read_text(encoding="utf-8"), encoding="utf-8")
        written.append("logo.svg")
    elif svg is not None and svg.is_file():
        written.append("logo.svg")
    src = Image.open(png).convert("RGBA")
    for size in PNG_SIZES:
        img = src.copy()
        img.thumbnail((size, size), Image.Resampling.LANCZOS)
        canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        canvas.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
        canvas.save(folder / f"logo-{size}.png")
        written.append(f"logo-{size}.png")
    src.save(folder / "favicon.ico", sizes=[(16, 16), (32, 32), (48, 48)])
    written.append("favicon.ico")
    return written


def pixelate(png: Path, out: Path, *, grid: int, palette: list[str] | None, scale: int) -> Path:
    """Hold a picture to a pixel grid of `grid` px and to `palette` (hex colours; an adaptive
    16-colour palette when none), then grow it back `scale` times with hard edges."""
    from PIL import Image

    img = Image.open(png).convert("RGBA")
    small = img.resize(
        (grid, round(grid * img.height / max(1, img.width))), Image.Resampling.NEAREST
    )
    alpha = small.getchannel("A").point(lambda a: 255 if a >= 128 else 0)
    rgb = small.convert("RGB")
    colours = [c for c in (palette or []) if re.fullmatch(r"#[0-9a-fA-F]{6}", c)]
    if colours:
        pal = Image.new("P", (1, 1))
        flat: list[int] = []
        for c in colours[:256]:
            flat += [int(c[i : i + 2], 16) for i in (1, 3, 5)]
        flat += flat[-3:] * (256 - len(colours[:256]))
        pal.putpalette(flat)
        quant = rgb.quantize(palette=pal, dither=Image.Dither.NONE)
    else:
        quant = rgb.quantize(colors=16, dither=Image.Dither.NONE)
    held = quant.convert("RGBA")
    held.putalpha(alpha)
    big = held.resize((held.width * scale, held.height * scale), Image.Resampling.NEAREST)
    out.parent.mkdir(parents=True, exist_ok=True)
    big.save(out)
    return out


@dataclass
class PieceSpec:
    name: str
    purpose: str = ""
    width: int = 512
    height: int = 512
    background: str = "transparent"  # transparent | solid
    format: str = "png"  # png | svg | jpg | webp
    pixel_art: bool = False
    grid: int = 32
    target: str = ""  # where it goes in the product, relative to the repository root
    extra: dict[str, Any] = field(default_factory=dict)


def piece_problems(spec: PieceSpec, path: Path) -> list[str]:
    """What code can check of a piece before anyone looks at it."""
    if not path.is_file():
        return ["the file was not written"]
    problems = []
    if path.stat().st_size > MAX_PIECE_BYTES:
        problems.append(f"{path.stat().st_size // 1000} kB, above {MAX_PIECE_BYTES // 1000} kB")
    if spec.format == "svg":
        if not valid_svg(path.read_text(encoding="utf-8", errors="replace")):
            problems.append("not a valid SVG")
        return problems
    from PIL import Image

    try:
        img = Image.open(path)
        img.load()
    except Exception:  # noqa: BLE001 - a broken picture is the finding
        return ["not a picture that opens"]
    if (img.width, img.height) != (spec.width, spec.height):
        problems.append(f"{img.width}x{img.height}, not {spec.width}x{spec.height}")
    if spec.background == "transparent":
        if "A" not in img.getbands() or img.getchannel("A").getextrema()[0] == 255:
            problems.append("the background is not transparent")
    return problems


def fit(data: bytes, spec: PieceSpec, out: Path) -> Path:
    """A generated picture made into the piece: resized to the size asked (cover, centred),
    in its format; transparent backgrounds keep their alpha."""
    from PIL import Image, ImageOps

    img = Image.open(io.BytesIO(data)).convert("RGBA")
    if (img.width, img.height) != (spec.width, spec.height):
        img = ImageOps.fit(img, (spec.width, spec.height), Image.Resampling.LANCZOS)
    out.parent.mkdir(parents=True, exist_ok=True)
    if spec.format in ("jpg", "jpeg"):
        img.convert("RGB").save(out, format="JPEG", quality=88)
    elif spec.format == "webp":
        img.save(out, format="WEBP", quality=88)
    else:
        img.save(out, format="PNG", optimize=True)
    return out


def safe_target(target: str) -> str:
    """A repository-relative path for a piece, or "" when it would leave the repository or land
    in the factory's records."""
    t = target.strip().replace("\\", "/").lstrip("/")
    parts = [p for p in t.split("/") if p]
    if not parts or any(p in ("..", ".git") for p in parts) or parts[0] == ".loompa":
        return ""
    return "/".join(parts)
