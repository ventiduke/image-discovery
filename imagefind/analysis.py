"""Background work after indexing: thumbnails, colours and measurements.

Each large image is decoded once: its thumbnail is made, and colours and
measurements are read from the thumbnail (or from the image itself when it's
already thumbnail-sized). Results are written to the index in batches. Used
by the page (in the background) and by the command line (waiting for it).
"""

from __future__ import annotations

import threading
from contextlib import closing
from pathlib import Path

from PIL import Image

from . import colors, index, measure, thumbs

BATCH = 200


def start(root: Path, db_path: Path, thumbs_dir: Path, workers: int | None = None) -> thumbs.Warmer:
    """Start the work that's missing; returns at once."""
    with closing(index.connect(db_path)) as conn:
        rows = index.all_images(conn)
    jobs = []
    for row in rows:
        target = None
        if thumbs.needs_thumbnail(row["width"], row["height"]):
            target = thumbs.thumb_path(thumbs_dir, row["path"], row["size"], row["mtime_ns"])
        missing = target is not None and not target.exists()
        if missing or not row["analyzed"] or not row["measured"]:
            jobs.append((row["path"], root / row["path"], target, not row["analyzed"], not row["measured"]))

    pending: list = []
    lock = threading.Lock()
    conn = index.connect(db_path)

    def flush(final: bool = False) -> None:
        with lock:
            batch = pending[:]
            pending.clear()
            if batch:
                index.save_analysis(conn, batch)
            if final:
                conn.close()

    def process(job) -> None:
        path, source, target, want_colors, want_measures = job
        im = None
        if target is not None and not target.exists():
            im = thumbs.shrink(source)
            thumbs.save(im, target)
        if not (want_colors or want_measures):
            return
        if im is None:
            with Image.open(target if target is not None else source) as opened:
                im = opened.convert("RGBA")
        result = (path, colors.analyze(im) if want_colors else None,
                  measure.measure(im) if want_measures else None)
        with lock:
            pending.append(result)
            full = len(pending) >= BATCH
        if full:
            flush()

    return thumbs.Warmer(jobs, total=len(jobs), process=process, workers=workers,
                         on_finish=lambda: flush(final=True))
