"""Rough colour analysis: how much of an image falls in each of 28 colour bins.

Each visible pixel (transparent ones are skipped) goes into one bin: four
neutrals (black, dark grey, light grey, white), or one of twelve hues in a
bright and a dark shade. An image's colours are the share of its pixels in
each bin; shares under 3% are dropped as noise.
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass

from PIL import Image

SAMPLE = 48  # images are shrunk to at most this many pixels a side first
MIN_SHARE = 0.03  # smaller shares aren't stored
MATCH_SHARE = 0.10  # a colour filter needs at least this much of the colour


@dataclass(frozen=True)
class Bin:
    id: int
    name: str
    hex: str


_NEUTRALS = [("Black", "#111111"), ("Dark grey", "#555555"), ("Light grey", "#b4b4b4"), ("White", "#f6f6f6")]
_HUES = ["Red", "Orange", "Yellow", "Lime", "Green", "Teal", "Cyan", "Sky", "Blue", "Purple", "Magenta", "Pink"]
_DARK_NAMES = {"Orange": "Brown", "Yellow": "Olive"}


def _hex(h: float, s: float, v: float) -> str:
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


BINS: list[Bin] = [Bin(i, name, hex_) for i, (name, hex_) in enumerate(_NEUTRALS)]
for _i, _hue in enumerate(_HUES):
    BINS.append(Bin(len(BINS), _hue, _hex(_i / 12, 0.8, 0.95)))
    BINS.append(Bin(len(BINS), _DARK_NAMES.get(_hue, f"Dark {_hue.lower()}"), _hex(_i / 12, 0.85, 0.5)))
BY_ID = {b.id: b for b in BINS}


def classify(r: int, g: int, b: int) -> int:
    h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if v < 0.18:
        return 0
    if s < 0.2:
        return 3 if v > 0.85 else 2 if v > 0.5 else 1
    hue = int((h * 12 + 0.5) % 12)  # 12 bins centred on red, orange, ...
    return 4 + hue * 2 + (0 if v >= 0.6 else 1)


def analyze(im: Image.Image) -> dict[int, float]:
    """Share of the image's visible pixels in each colour bin."""
    small = im.convert("RGBA")
    # Nearest-pixel sampling: smoother resizing would blend neighbouring
    # colours into ones the image doesn't have (red next to blue -> pink).
    small.thumbnail((SAMPLE, SAMPLE), Image.Resampling.NEAREST)
    counts: dict[int, int] = {}
    seen = 0
    pixels = small.get_flattened_data() if hasattr(small, "get_flattened_data") else small.getdata()
    for r, g, b, a in pixels:
        if a < 128:
            continue
        seen += 1
        key = classify(r, g, b)
        counts[key] = counts.get(key, 0) + 1
    if not seen:
        return {}
    shares = {k: n / seen for k, n in counts.items()}
    return {k: round(share, 3) for k, share in shares.items() if share >= MIN_SHARE}
