"""How far a built page is from the approved screen, measured in code (plan 15.6).

The approved screen uses example data, the built page real data, so the two never match pixel
for pixel. Both are shrunk to a small grey picture before comparing: text that differs moves a
few grey levels, a block moved, resized, recoloured or missing moves many. The share of pixels
that moved far is the difference; above the factory's threshold, a model with vision says what
changed (`UXAgent.compare`), and only a change it calls a deviation fails the story.
"""

from __future__ import annotations

from pathlib import Path

COMPARE_WIDTH = 160
PIXEL_TOLERANCE = 48  # grey levels (0-255) a pixel may move and still count as the same


def _small(path: Path, width: int = COMPARE_WIDTH):  # type: ignore[no-untyped-def]
    from PIL import Image

    img = Image.open(path).convert("L")
    w, h = img.size
    height = max(1, round(h * width / max(1, w)))
    return img.resize((width, height), Image.Resampling.BILINEAR)


def difference(approved: Path, built: Path) -> float:
    """Share of the pixels (0-1) that moved past `PIXEL_TOLERANCE` once both pictures are shrunk
    to the same width; the part one picture has and the other lacks counts as moved."""
    a, b = _small(approved), _small(built)
    height = max(a.size[1], b.size[1])
    common = min(a.size[1], b.size[1])
    pa, pb = a.load(), b.load()
    moved = 0
    for y in range(common):
        for x in range(COMPARE_WIDTH):
            if abs(pa[x, y] - pb[x, y]) > PIXEL_TOLERANCE:
                moved += 1
    moved += (height - common) * COMPARE_WIDTH
    return moved / float(height * COMPARE_WIDTH)


def side_by_side(approved: Path, built: Path, out: Path, gap: int = 16) -> Path:
    """One picture with the approved screen on the left and the built page on the right: the
    evidence a failed comparison carries to the Worker and the founder."""
    from PIL import Image

    a, b = Image.open(approved).convert("RGB"), Image.open(built).convert("RGB")
    canvas = Image.new("RGB", (a.width + b.width + gap, max(a.height, b.height)), (255, 255, 255))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + gap, 0))
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    return out
