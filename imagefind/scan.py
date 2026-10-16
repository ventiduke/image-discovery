"""Walk the image folders, read image sizes, and match filenames to the catalog."""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from .library import IMAGE_SUFFIXES


@dataclass
class ImageFile:
    path: str  # relative to the content folder, with forward slashes
    folder: str  # top-level folder
    stem: str
    size: int
    mtime_ns: int
    width: int | None = None
    height: int | None = None


def walk(root: Path, folders: list[str]) -> Iterator[ImageFile]:
    """Every image under the given top-level folders, in a stable order."""
    for folder in folders:
        base = root / folder
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
            rel_dir = Path(dirpath).relative_to(root).as_posix()
            for filename in sorted(filenames):
                stem, ext = os.path.splitext(filename)
                if filename.startswith(".") or ext.lower() not in IMAGE_SUFFIXES:
                    continue
                try:
                    st = os.stat(os.path.join(dirpath, filename))
                except OSError:
                    continue
                yield ImageFile(
                    path=f"{rel_dir}/{filename}",
                    folder=folder,
                    stem=stem,
                    size=st.st_size,
                    mtime_ns=st.st_mtime_ns,
                )


def match(stem: str, lookup: Callable[[str], str | None]) -> tuple[str | None, str]:
    """Find the catalog entry a filename shows, and its variant.

    The whole name is tried first, so a label such as ``<id>_Ability1`` wins
    over reading ``Ability1`` as a variant of ``<id>``. Then ever shorter
    prefixes split at underscores: ``<id>_splash`` is entry ``<id>``, variant
    ``splash``. Matching ignores case (``lookup`` takes upper-case names).
    Returns ``(None, "")`` when nothing matches.
    """
    parts = stem.upper().split("_")
    for cut in range(len(parts), 0, -1):
        key = lookup("_".join(parts[:cut]))
        if key:
            return key, "_".join(parts[cut:]).lower()
    return None, ""


# -- image dimensions from the file header -------------------------------------

_HEADER = 64 * 1024  # enough for the size in PNG, GIF, WebP and almost all JPEGs


def dimensions(path: Path) -> tuple[int, int] | None:
    try:
        with path.open("rb") as f:
            head = f.read(_HEADER)
    except OSError:
        return None
    try:
        return _dimensions(head)
    except (struct.error, IndexError, ValueError):
        return None


def _dimensions(b: bytes) -> tuple[int, int] | None:
    if b.startswith(b"\x89PNG\r\n\x1a\n") and b[12:16] == b"IHDR":
        return struct.unpack(">II", b[16:24])
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return struct.unpack("<HH", b[6:10])
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        chunk = b[12:16]
        if chunk == b"VP8 ":
            w, h = struct.unpack("<HH", b[26:30])
            return w & 0x3FFF, h & 0x3FFF
        if chunk == b"VP8L":
            bits = int.from_bytes(b[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
        if chunk == b"VP8X":
            return int.from_bytes(b[24:27], "little") + 1, int.from_bytes(b[27:30], "little") + 1
        return None
    if b[:2] == b"\xff\xd8":
        return _jpeg_dimensions(b)
    return None


def _jpeg_dimensions(b: bytes) -> tuple[int, int] | None:
    i = 2
    while i + 9 < len(b):
        if b[i] != 0xFF:
            return None
        marker = b[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # no length field
            i += 2
            continue
        length = struct.unpack(">H", b[i + 2 : i + 4])[0]
        # Start-of-frame markers, except DHT (C4), JPG (C8) and DAC (CC).
        if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
            h, w = struct.unpack(">HH", b[i + 5 : i + 9])
            return w, h
        i += 2 + length
    return None
