"""A content folder: one catalog JSON at the top, image folders below it."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif"})
PREFERRED_CATALOG = "PublicContentCatalog.json"
# The version is the catalog's first key; reading the whole 15 MB file just
# for it would slow down opening a folder.
_VERSION_RE = re.compile(rb'"version"\s*:\s*"([^"]*)"')
_VERSION_PEEK = 64 * 1024


class LibraryError(ValueError):
    pass


@dataclass
class Folder:
    name: str
    images: int
    other_files: int


@dataclass
class Library:
    root: Path
    catalog: Path | None
    version: str | None
    folders: list[Folder] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def images(self) -> int:
        return sum(f.images for f in self.folders)

    def summary(self) -> dict:
        return {
            "path": str(self.root),
            "catalog": self.catalog.name if self.catalog else None,
            "version": self.version,
            "images": self.images,
            "folders": [vars(f) for f in self.folders],
            "warnings": self.warnings,
        }


def resolve_folder(raw: str) -> Path:
    # Windows "Copy as path" wraps the path in quotes.
    cleaned = raw.strip().strip('"').strip("'").strip()
    if not cleaned:
        raise LibraryError("No path given.")
    path = Path(cleaned).expanduser()
    try:
        path = path.resolve(strict=True)
    except (OSError, RuntimeError):
        raise LibraryError(f"Folder not found: {cleaned}") from None
    if not path.is_dir():
        raise LibraryError(f"Not a folder: {path}")
    return path


def open_library(raw: str | Path) -> Library:
    root = resolve_folder(str(raw))
    warnings: list[str] = []
    folders: list[Folder] = []
    catalogs: list[Path] = []
    loose_images = 0
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                folders.append(_count(entry.path, entry.name))
            elif entry.is_file():
                suffix = Path(entry.name).suffix.lower()
                if suffix == ".json":
                    catalogs.append(Path(entry.path))
                elif suffix in IMAGE_SUFFIXES:
                    loose_images += 1
    folders.sort(key=lambda f: f.name.casefold())

    catalog = _pick_catalog(catalogs)
    if catalog is None:
        warnings.append("No catalog JSON file in this folder, so images will have no metadata.")
    elif len(catalogs) > 1:
        others = ", ".join(sorted(c.name for c in catalogs if c != catalog))
        warnings.append(f"Using {catalog.name} as the catalog; ignoring {others}.")
    if not any(f.images for f in folders):
        warnings.append("No image folders found. Images are expected in sub-folders.")
    if loose_images:
        warnings.append(
            f"{loose_images} image{'s' if loose_images != 1 else ''} sit directly in this "
            "folder rather than in a sub-folder, and are left out."
        )
    return Library(
        root=root,
        catalog=catalog,
        version=_read_version(catalog) if catalog else None,
        folders=folders,
        warnings=warnings,
    )


def _count(path: str, name: str) -> Folder:
    """Count images in a folder and everything below it."""
    images = other = 0
    for _dirpath, _dirnames, filenames in os.walk(path):
        for filename in filenames:
            if filename.startswith("."):
                continue
            if Path(filename).suffix.lower() in IMAGE_SUFFIXES:
                images += 1
            else:
                other += 1
    return Folder(name=name, images=images, other_files=other)


def _pick_catalog(candidates: list[Path]) -> Path | None:
    if not candidates:
        return None
    for path in candidates:
        if path.name.casefold() == PREFERRED_CATALOG.casefold():
            return path
    return max(candidates, key=lambda p: p.stat().st_size)


def _read_version(catalog: Path) -> str | None:
    try:
        with catalog.open("rb") as f:
            head = f.read(_VERSION_PEEK)
    except OSError:
        return None
    match = _VERSION_RE.search(head)
    return match.group(1).decode("utf-8", "replace") if match else None
