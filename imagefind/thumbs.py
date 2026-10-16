"""Thumbnails: small WebP copies of the images, kept in the cache folder.

A thumbnail is made the first time the grid asks for it, and a background
:class:`Warmer` makes the rest so scrolling stays smooth. Each is keyed by the
image's path, size and modification time, so a changed image gets a new one.
Images already no bigger than a thumbnail are served as they are.
"""

from __future__ import annotations

import hashlib
import os
import threading
from pathlib import Path
from typing import Callable, Iterable

from PIL import Image, ImageOps

SIZE = 320  # longest side, in pixels
QUALITY = 82


def needs_thumbnail(width: int | None, height: int | None) -> bool:
    # Unknown size (unreadable header): let Pillow try.
    return not (width and height and max(width, height) <= SIZE)


def thumb_path(thumbs_dir: Path, rel_path: str, size: int, mtime_ns: int) -> Path:
    digest = hashlib.sha1(f"{rel_path}\0{size}\0{mtime_ns}".encode("utf-8")).hexdigest()
    return thumbs_dir / digest[:2] / f"{digest}.webp"


def make(source: Path, target: Path) -> Path:
    """Write a thumbnail of ``source`` to ``target`` (atomically) and return it."""
    with Image.open(source) as im:
        im.draft("RGB", (SIZE, SIZE))  # fast downscaled decode for JPEGs
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() or "transparency" in im.info else "RGB")
        im.thumbnail((SIZE, SIZE), Image.Resampling.LANCZOS)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f"{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        im.save(tmp, "WEBP", quality=QUALITY, method=4)
    os.replace(tmp, target)
    return target


class Warmer:
    """Makes missing thumbnails on a few background threads.

    The threads are daemons and stop between images when :meth:`stop` is
    called, so quitting the tool never waits for them.
    """

    def __init__(self, jobs: Iterable[tuple[Path, Path]], total: int, workers: int | None = None,
                 on_error: Callable[[Path, Exception], None] | None = None) -> None:
        self._jobs = iter(jobs)
        self._lock = threading.Lock()
        self._stopped = threading.Event()
        self.total = total
        self.done = 0
        self.failed = 0
        self.on_error = on_error
        count = workers or max(1, min(8, (os.cpu_count() or 2) - 1))
        self._threads = [threading.Thread(target=self._run, daemon=True) for _ in range(count)]
        for t in self._threads:
            t.start()

    @property
    def running(self) -> bool:
        return any(t.is_alive() for t in self._threads)

    def stop(self) -> None:
        self._stopped.set()

    def join(self, timeout: float | None = None) -> None:
        for t in self._threads:
            t.join(timeout)

    def _run(self) -> None:
        while not self._stopped.is_set():
            with self._lock:
                job = next(self._jobs, None)
            if job is None:
                return
            source, target = job
            if not target.exists():
                try:
                    make(source, target)
                except Exception as exc:  # unreadable or unsupported image
                    with self._lock:
                        self.failed += 1
                    if self.on_error:
                        self.on_error(source, exc)
            with self._lock:
                self.done += 1
