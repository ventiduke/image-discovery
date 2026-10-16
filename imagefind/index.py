"""The search index: catalog entries and image files joined on ID, in SQLite.

The index lives outside the content folder (see :func:`cache_dir`) and is
updated rather than rebuilt: the catalog is re-read only when the file
changed, and image headers are re-read only for new or changed files. Every
run still walks the folders and re-matches every file, which takes about a
second for the full dump.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import catalog as catalog_mod
from . import scan
from .library import Library

SCHEMA_VERSION = "1"
REPO_ROOT = Path(__file__).resolve().parent.parent

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE entries (
    key TEXT PRIMARY KEY, id TEXT, label TEXT, section TEXT, kind TEXT, parent TEXT,
    name TEXT, description TEXT, names_json TEXT, fields_json TEXT, raw_json TEXT
);
CREATE INDEX entries_kind ON entries(kind);
CREATE INDEX entries_parent ON entries(parent);
CREATE TABLE images (
    path TEXT PRIMARY KEY, folder TEXT, stem TEXT, variant TEXT, entry_key TEXT,
    size INTEGER, mtime_ns INTEGER, width INTEGER, height INTEGER
);
CREATE INDEX images_folder ON images(folder);
CREATE INDEX images_entry ON images(entry_key);
CREATE VIRTUAL TABLE entries_fts USING fts5(
    key UNINDEXED, name, description, other_names, ident,
    tokenize = 'unicode61 remove_diacritics 2', prefix = '2 3'
);
"""

Progress = Callable[[int, int], None]


def cache_dir(root: Path) -> Path:
    """Where the index and thumbnails for a content folder are kept.

    The repo's own content folder uses the git-ignored ``.cache`` folder next
    to it; any other folder uses the user's cache directory, so nothing is
    ever written next to someone's files.
    """
    root = root.resolve()
    if root.parent == REPO_ROOT:
        base = REPO_ROOT / ".cache"
    elif sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "imagefind"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches" / "imagefind"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "imagefind"
    digest = hashlib.sha1(str(root).casefold().encode("utf-8")).hexdigest()[:8]
    return base / f"{root.name}-{digest}"


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@dataclass
class BuildResult:
    catalog_reloaded: bool
    headers_read: int
    images: int
    seconds: float


def build(library: Library, db_path: Path, *, rebuild: bool = False,
          progress: Progress | None = None) -> BuildResult:
    start = time.monotonic()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if rebuild:
        _remove(db_path)
    with closing(_open(db_path)) as conn:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())

        catalog_sig = _signature(library.catalog) if library.catalog else ""
        reload = catalog_sig != meta.get("catalog_sig")
        if reload:
            cat = catalog_mod.load(library.catalog) if library.catalog else None
            with conn:
                _write_entries(conn, cat)
                _set_meta(conn, catalog_sig=catalog_sig,
                          catalog_version=(cat.version if cat else "") or "")
        lookup = _lookup(conn)

        known = {row["path"]: row for row in conn.execute(
            "SELECT path, size, mtime_ns, width, height FROM images")}
        total = library.images
        rows, read = [], 0
        for n, image in enumerate(scan.walk(library.root, [f.name for f in library.folders]), 1):
            old = known.get(image.path)
            if old is not None and old["size"] == image.size and old["mtime_ns"] == image.mtime_ns:
                image.width, image.height = old["width"], old["height"]
            else:
                dims = scan.dimensions(library.root / image.path)
                image.width, image.height = dims if dims else (None, None)
                read += 1
            entry_key, variant = scan.match(image.stem, lookup.get)
            rows.append((image.path, image.folder, image.stem, variant, entry_key,
                         image.size, image.mtime_ns, image.width, image.height))
            if progress and n % 250 == 0:
                progress(n, max(total, n))
        with conn:
            conn.execute("DELETE FROM images")
            conn.executemany("INSERT INTO images VALUES (?,?,?,?,?,?,?,?,?)", rows)
            _set_meta(conn, built_at=str(int(time.time())))
        if progress:
            progress(len(rows), len(rows))
    return BuildResult(reload, read, len(rows), round(time.monotonic() - start, 2))


def _open(db_path: Path) -> sqlite3.Connection:
    conn = connect(db_path)
    try:
        version = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
    except sqlite3.DatabaseError:
        version = None
    if version is None or version[0] != SCHEMA_VERSION:
        conn.close()
        _remove(db_path)
        conn = connect(db_path)
        with conn:
            conn.executescript(SCHEMA)
            _set_meta(conn, schema=SCHEMA_VERSION)
    return conn


def _remove(db_path: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)


def _signature(path: Path) -> str:
    st = path.stat()
    return f"{path.name}:{st.st_size}:{st.st_mtime_ns}"


def _set_meta(conn: sqlite3.Connection, **values: str) -> None:
    conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", values.items())


def _write_entries(conn: sqlite3.Connection, cat: catalog_mod.Catalog | None) -> None:
    conn.execute("DELETE FROM entries")
    conn.execute("DELETE FROM entries_fts")
    if cat is None:
        return
    rows, fts = [], []
    for e in cat.entries.values():
        rows.append((e.key, e.id, e.label, e.section, e.kind, e.parent, e.name, e.description,
                     json.dumps(e.names, ensure_ascii=False),
                     json.dumps(e.fields, ensure_ascii=False),
                     json.dumps(e.raw, ensure_ascii=False)))
        fts.append((e.key, e.name, e.description, " ".join(e.names.values()),
                    " ".join(x for x in (e.id, e.label) if x)))
    conn.executemany("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO entries_fts VALUES (?,?,?,?,?)", fts)


def _lookup(conn: sqlite3.Connection) -> dict[str, str]:
    lookup: dict[str, str] = {}
    for key, ident, label in conn.execute("SELECT key, id, label FROM entries"):
        for alias in (ident, label):
            if alias:
                lookup.setdefault(alias.upper(), key)
    return lookup


# -- reading ------------------------------------------------------------------


def report(db_path: Path, unmatched_limit: int = 500) -> dict:
    """How well the images and the catalog line up, for the match report."""
    with closing(connect(db_path)) as conn:
        meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
        folders = {}
        for row in conn.execute(
            "SELECT folder, COUNT(*) AS images, COUNT(entry_key) AS matched, SUM(size) AS bytes "
            "FROM images GROUP BY folder ORDER BY folder COLLATE NOCASE"
        ):
            folders[row["folder"]] = {
                "name": row["folder"], "images": row["images"], "matched": row["matched"],
                "unmatched": row["images"] - row["matched"], "bytes": row["bytes"] or 0,
                "variants": {}, "kinds": {},
            }
        for folder, variant, n in conn.execute(
            "SELECT folder, variant, COUNT(*) FROM images WHERE entry_key IS NOT NULL "
            "GROUP BY folder, variant ORDER BY 3 DESC"
        ):
            folders[folder]["variants"][variant or "base"] = n
        for folder, kind, n in conn.execute(
            "SELECT i.folder, e.kind, COUNT(*) FROM images i JOIN entries e ON e.key = i.entry_key "
            "GROUP BY i.folder, e.kind ORDER BY 3 DESC"
        ):
            folders[folder]["kinds"][kind] = n

        coverage = [
            {"kind": kind, "entries": entries, "with_images": with_images}
            for kind, entries, with_images in conn.execute(
                "SELECT e.kind, COUNT(*), COUNT(i.entry_key) FROM entries e "
                "LEFT JOIN (SELECT DISTINCT entry_key FROM images) i ON i.entry_key = e.key "
                "GROUP BY e.kind ORDER BY e.kind"
            )
        ]
        unmatched_total = conn.execute(
            "SELECT COUNT(*) FROM images WHERE entry_key IS NULL").fetchone()[0]
        unmatched = [row[0] for row in conn.execute(
            "SELECT path FROM images WHERE entry_key IS NULL ORDER BY path LIMIT ?",
            (unmatched_limit,))]
        totals = conn.execute(
            "SELECT COUNT(*), COUNT(entry_key), COUNT(DISTINCT entry_key) FROM images").fetchone()
    return {
        "catalog_version": meta.get("catalog_version") or None,
        "built_at": int(meta.get("built_at") or 0),
        "images": totals[0],
        "matched": totals[1],
        "entries_with_images": totals[2],
        "entries": sum(c["entries"] for c in coverage),
        "folders": list(folders.values()),
        "coverage": coverage,
        "unmatched_total": unmatched_total,
        "unmatched": unmatched,
    }
