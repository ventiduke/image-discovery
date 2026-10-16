"""Measurements that say what an image is good for, and the rules that use them.

Each image is measured once, from its thumbnail (or itself, when it's that
small), alongside its colours:

- ``transparent``: share of fully or mostly transparent pixels.
- ``fill``: how much of the frame the visible subject's bounding box covers.
- ``brightness`` and ``contrast``: mean and spread of the visible pixels'
  luminance, 0 to 1.
- ``busyness``: how much fine detail there is (mean edge strength, 0 to 1).
  Calm images, or a calm area, leave room for text on top.
- ``calm_side`` (left, center or right), with that third's busyness and
  brightness: the best place for a title, and whether white or dark text
  will read there.

The "good for" rules below are starting points, to be tuned on real images
(``imagefind stats`` prints how a content folder measures).
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageStat

SIDES = ("left", "center", "right")


@dataclass(frozen=True)
class Measures:
    transparent: float
    fill: float
    brightness: float
    contrast: float
    busyness: float
    calm_side: str
    calm_busyness: float
    calm_brightness: float

    @property
    def text_color(self) -> str | None:
        """The text colour that reads best on the calm area of an opaque image."""
        if self.transparent >= 0.3:
            return None
        return "white" if self.calm_brightness < 0.55 else "black"

    @property
    def best_on(self) -> str | None:
        """For a cut-out: the page background it shows up on (a white
        silhouette disappears on a white slide)."""
        if self.transparent < 0.15 or not self.fill:  # opaque, or nothing visible
            return None
        return "dark" if self.brightness > 0.65 else "light" if self.brightness < 0.35 else "any"


def measure(im: Image.Image) -> Measures:
    im = im.convert("RGBA")
    w, h = im.size
    alpha = im.getchannel("A")
    visible = alpha.point(lambda a: 255 if a >= 128 else 0)
    shown = sum(visible.histogram()[255:]) or 0
    transparent = 1 - shown / (w * h)
    box = visible.getbbox()
    fill = ((box[2] - box[0]) * (box[3] - box[1]) / (w * h)) if box else 0.0
    if not shown:
        return Measures(round(transparent, 3), 0.0, 0.0, 0.0, 0.0, "center", 0.0, 0.0)

    # Transparent areas count as mid grey, so they read as calm, not as black.
    flat = Image.new("RGBA", im.size, (128, 128, 128, 255))
    flat.alpha_composite(im)
    gray = flat.convert("L")
    stat = ImageStat.Stat(gray, visible)
    brightness, contrast = stat.mean[0] / 255, stat.stddev[0] / 255
    edges = gray.filter(ImageFilter.FIND_EDGES)
    # The filter marks the image's own border; leave it out.
    inner = (1, 1, max(2, w - 1), max(2, h - 1))
    edges, gray_in, mask = edges.crop(inner), gray.crop(inner), visible.crop(inner)
    busyness = _mean(edges, mask)

    thirds = []
    iw = edges.width
    for i, side in enumerate(SIDES):
        part = (iw * i // 3, 0, max(iw * (i + 1) // 3, iw * i // 3 + 1), edges.height)
        full = Image.new("L", (part[2] - part[0], part[3] - part[1]), 255)
        # Text can sit over transparent areas too, so the whole third counts.
        thirds.append((_mean(edges.crop(part), full), _mean(gray_in.crop(part), full), side))
    calm_busy, calm_bright, calm_side = min(thirds, key=lambda t: (t[0], SIDES.index(t[2]) != 1))
    return Measures(
        transparent=round(transparent, 3), fill=round(fill, 3),
        brightness=round(brightness, 3), contrast=round(contrast, 3), busyness=round(busyness, 4),
        calm_side=calm_side, calm_busyness=round(calm_busy, 4), calm_brightness=round(calm_bright, 3),
    )


def _mean(im: Image.Image, mask: Image.Image) -> float:
    if not mask.getbbox():
        return 0.0
    return ImageStat.Stat(im, mask).mean[0] / 255


# -- "good for" rules -----------------------------------------------------------
#
# SQL over images ``i`` and their measures ``m``. Not exclusive: an image can be
# good for several things.

USES: dict[str, tuple[str, str, str]] = {
    # name: (label, description, SQL condition)
    "background": (
        "Slide background",
        "Opaque, landscape (about 4:3 to 21:9), at least 1600 px wide, not too busy.",
        "m.transparent < 0.02 AND i.width >= 1600 AND i.width >= 1.3 * i.height "
        "AND i.width <= 2.4 * i.height AND m.busyness <= 0.14",
    ),
    "header": (
        "Section header",
        "A wide banner (2.5:1 or wider, at least 1000 px), or a large opaque landscape "
        "image with a calm side for the title, to crop into a strip.",
        "m.transparent < 0.3 AND ((i.width >= 2.5 * i.height AND i.width >= 1000) OR "
        "(i.width >= 1600 AND i.width >= 1.3 * i.height AND m.calm_busyness <= 0.10))",
    ),
    "icon": (
        "Icon / spot art",
        "Transparent background, roughly square, subject filling most of the frame, at least 128 px.",
        "m.transparent >= 0.15 AND i.width >= 0.75 * i.height AND i.width <= 1.34 * i.height "
        "AND m.fill >= 0.5 AND i.width >= 128",
    ),
    "cutout": (
        "Cut-out",
        "Transparent background, at least 512 px on the long side and 256 px on the short: "
        "a subject to place on a slide.",
        "m.transparent >= 0.15 AND MAX(i.width, i.height) >= 512 AND MIN(i.width, i.height) >= 256",
    ),
}

SHAPES: dict[str, tuple[str, str]] = {
    # name: (label, SQL condition on i.width / i.height)
    "banner": ("Wide banner (2.5:1+)", "i.width >= 2.5 * i.height"),
    "landscape": ("Landscape", "i.width >= 1.2 * i.height AND i.width < 2.5 * i.height"),
    "square": ("Square-ish", "i.width > 0.8 * i.height AND i.width < 1.2 * i.height"),
    "portrait": ("Portrait", "i.width <= 0.8 * i.height"),
}
