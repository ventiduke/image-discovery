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
import re
import sqlite3
import sys
import time
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import catalog as catalog_mod
from . import colors, scan
from .library import Library

SCHEMA_VERSION = "2"
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
-- context: names of related items (a chroma's skin and weapon, an ability's
-- character) and simple field values, so searching "Gekko" finds his abilities.
CREATE VIRTUAL TABLE entries_fts USING fts5(
    key UNINDEXED, name, description, other_names, ident, context,
    tokenize = 'unicode61 remove_diacritics 2', prefix = '2 3'
);
-- Colour shares per image (see colors.py), filled in the background after
-- indexing; "analyzed" lists the images done, including those with no colours.
CREATE TABLE image_colors (path TEXT, bin INTEGER, weight REAL, PRIMARY KEY (path, bin));
CREATE INDEX image_colors_bin ON image_colors(bin, weight);
CREATE TABLE analyzed (path TEXT PRIMARY KEY);
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
        stale = set(known)  # files whose colours must be worked out again
        for n, image in enumerate(scan.walk(library.root, [f.name for f in library.folders]), 1):
            old = known.get(image.path)
            if old is not None and old["size"] == image.size and old["mtime_ns"] == image.mtime_ns:
                image.width, image.height = old["width"], old["height"]
                stale.discard(image.path)
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
            for table in ("image_colors", "analyzed"):
                conn.executemany(f"DELETE FROM {table} WHERE path = ?", ((p,) for p in stale))
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
    for table in ("entries", "entries_fts"):
        conn.execute(f"DELETE FROM {table}")
    if cat is None:
        return
    rows, fts = [], []
    for e in cat.entries.values():
        rows.append((e.key, e.id, e.label, e.section, e.kind, e.parent, e.name, e.description,
                     json.dumps(e.names, ensure_ascii=False),
                     json.dumps(e.fields, ensure_ascii=False),
                     json.dumps(e.raw, ensure_ascii=False)))
        fts.append((e.key, e.name, e.description, " ".join(e.names.values()),
                    " ".join(x for x in (e.id, e.label) if x), " ".join(_context(cat, e))))
    conn.executemany("INSERT INTO entries VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    conn.executemany("INSERT INTO entries_fts VALUES (?,?,?,?,?,?)", fts)


def references(cat: catalog_mod.Catalog, e: catalog_mod.Entry) -> list[tuple[str, catalog_mod.Entry]]:
    """Fields of an entry that name another entry, e.g. a skin's weaponId."""
    out = []
    for name, value in e.fields.items():
        if isinstance(value, str) and (target := cat.find(value)) is not None and target.key != e.key:
            out.append((name, target))
    return out


def _context(cat: catalog_mod.Catalog, e: catalog_mod.Entry) -> list[str]:
    """Words that describe an entry without being its name: its parents' names,
    the names of entries it refers to, and its plain field values."""
    out: list[str] = []
    seen = {e.key}
    node: catalog_mod.Entry | None = e
    while node is not None:
        if node is not e:
            out.append(node.name)
        for _field, target in references(cat, node):
            if target.key not in seen:
                seen.add(target.key)
                out.append(target.name)
        for value in node.fields.values():
            if isinstance(value, str) and cat.find(value) is None:
                out.append(value)
        node = cat.entries.get(node.parent) if node.parent and node.parent not in seen else None
        if node is not None:
            seen.add(node.key)
    return [x for x in out if x]


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



# -- browsing and searching ---------------------------------------------------


SORTS = {
    # Images without a catalog name sort by filename among the named ones.
    "name": "COALESCE(NULLIF(e.name, ''), i.stem) COLLATE NOCASE, i.variant, i.path",
    "folder": "i.folder COLLATE NOCASE, COALESCE(NULLIF(e.name, ''), i.stem) COLLATE NOCASE, i.variant, i.path",
    "size": "i.size DESC, i.path",
}
FROM = "FROM images i LEFT JOIN entries e ON e.key = i.entry_key"
BASE_VARIANT = "base"  # how the filters name images with no variant


@dataclass
class Filters:
    """What the grid shows: images in these folders that match everything else.

    Several colours must all match; several kinds or variants match
    any of them.
    """

    folders: list[str]
    q: str = ""
    all_languages: bool = False
    colors: list[int] = field(default_factory=list)
    kinds: list[str] = field(default_factory=list)
    variants: list[str] = field(default_factory=list)


def fts_query(q: str, all_languages: bool = False) -> str | None:
    """An FTS5 query matching every word of ``q`` as a prefix."""
    words = re.findall(r"[^\W_]+", q)
    if not words:
        return None
    columns = "name description context ident" + (" other_names" if all_languages else "")
    return "{%s} : (%s)" % (columns, " ".join(f'"{w}"*' for w in words))


def _where(f: Filters, skip: str = "") -> tuple[str, list]:
    """SQL conditions for the filters, except the one named by ``skip`` (so a
    filter's own counts aren't narrowed by its own selection)."""
    conds = [f"i.folder IN ({','.join('?' * len(f.folders))})"] if f.folders else ["0"]
    args: list = list(f.folders)
    if f.q.strip():
        like = "%" + f.q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = fts_query(f.q, f.all_languages)
        if query:
            conds.append("(i.entry_key IN (SELECT key FROM entries_fts WHERE entries_fts MATCH ?) "
                         "OR i.path LIKE ? ESCAPE '\\')")
            args += [query, like]
        else:
            conds.append("i.path LIKE ? ESCAPE '\\'")
            args.append(like)
    if skip != "colors":
        for bin_id in f.colors:
            conds.append("EXISTS (SELECT 1 FROM image_colors c WHERE c.path = i.path "
                         "AND c.bin = ? AND c.weight >= ?)")
            args += [bin_id, colors.MATCH_SHARE]
    if f.kinds and skip != "kinds":
        conds.append(f"e.kind IN ({','.join('?' * len(f.kinds))})")
        args += f.kinds
    if f.variants and skip != "variants":
        conds.append(f"i.variant IN ({','.join('?' * len(f.variants))})")
        args += ["" if v == BASE_VARIANT else v for v in f.variants]
    return " AND ".join(conds), args


def list_images(conn: sqlite3.Connection, f: Filters, sort: str = "name",
                offset: int = 0, limit: int = 200) -> tuple[int, list[dict]]:
    """One page of matching images, and how many match in all.

    With a colour filter, the images with the most of those colours come first.
    """
    where, args = _where(f)
    total = conn.execute(f"SELECT COUNT(*) {FROM} WHERE {where}", args).fetchone()[0]
    order = SORTS.get(sort, SORTS["name"])
    order_args: list = []
    if f.colors:
        order = (f"(SELECT SUM(weight) FROM image_colors c WHERE c.path = i.path "
                 f"AND c.bin IN ({','.join('?' * len(f.colors))})) DESC, {order}")
        order_args = list(f.colors)
    rows = conn.execute(
        f"SELECT i.path, i.folder, i.stem, i.variant, i.entry_key, i.size, i.mtime_ns, i.width, "
        f"i.height, e.name, e.kind {FROM} WHERE {where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*args, *order_args, limit, offset],
    ).fetchall()
    return total, [dict(row) for row in rows]


def facets(conn: sqlite3.Connection, f: Filters) -> dict:
    """Counts for the filter controls, over the images the other filters match.

    Colours count images with at least ``colors.MATCH_SHARE`` of the colour.
    """
    where, args = _where(f, skip="colors")
    color_counts = dict(conn.execute(
        f"SELECT c.bin, COUNT(*) {FROM} JOIN image_colors c ON c.path = i.path AND c.weight >= ? "
        f"WHERE {where} GROUP BY c.bin", [colors.MATCH_SHARE, *args]).fetchall())
    where, args = _where(f, skip="kinds")
    kinds = conn.execute(
        f"SELECT e.kind, COUNT(*) {FROM} WHERE {where} AND e.kind IS NOT NULL "
        f"GROUP BY e.kind ORDER BY 2 DESC", args).fetchall()
    where, args = _where(f, skip="variants")
    variants = conn.execute(
        f"SELECT i.variant, COUNT(*) {FROM} WHERE {where} GROUP BY i.variant ORDER BY 2 DESC",
        args).fetchall()
    where, args = _where(Filters(f.folders))
    analyzed, in_folders = conn.execute(
        f"SELECT COUNT(a.path), COUNT(*) {FROM} LEFT JOIN analyzed a ON a.path = i.path WHERE {where}",
        args).fetchone()
    return {
        "colors": [{"id": b.id, "name": b.name, "hex": b.hex, "count": color_counts.get(b.id, 0)}
                   for b in colors.BINS],
        "colors_analyzed": analyzed,
        "colors_total": in_folders,
        "kinds": [{"kind": k, "count": n} for k, n in kinds],
        "variants": [{"variant": v or BASE_VARIANT, "count": n} for v, n in variants],
    }


IMAGE_COLUMNS = "i.path, i.folder, i.variant, i.size, i.mtime_ns, i.width, i.height"


def _entry_summary(conn: sqlite3.Connection, key: str) -> dict | None:
    """An entry's name and kind, and its first image (base variant first)."""
    row = conn.execute("SELECT key, name, kind FROM entries WHERE key = ?", (key,)).fetchone()
    if row is None:
        return None
    image = conn.execute(f"SELECT {IMAGE_COLUMNS} FROM images i WHERE i.entry_key = ? "
                         f"ORDER BY i.variant != '', i.variant, i.path LIMIT 1", (key,)).fetchone()
    return {**dict(row), "image": dict(image) if image else None}


def image_detail(conn: sqlite3.Connection, path: str) -> dict | None:
    """Everything known about one image, for the details panel."""
    image = conn.execute(f"SELECT {IMAGE_COLUMNS}, i.stem, i.entry_key FROM images i WHERE i.path = ?",
                         (path,)).fetchone()
    if image is None:
        return None
    detail: dict = {"image": dict(image), "entry": None}
    detail["colors"] = [
        {"id": b, "name": colors.BY_ID[b].name, "hex": colors.BY_ID[b].hex, "share": w}
        for b, w in conn.execute("SELECT bin, weight FROM image_colors WHERE path = ? ORDER BY weight DESC",
                                 (path,))
    ]
    detail["analyzed"] = conn.execute("SELECT 1 FROM analyzed WHERE path = ?", (path,)).fetchone() is not None
    key = image["entry_key"]
    if not key:
        return detail
    e = conn.execute("SELECT * FROM entries WHERE key = ?", (key,)).fetchone()
    fields = json.loads(e["fields_json"])
    refs = []
    for name, value in fields.items():
        if isinstance(value, str) and value.upper() != key:
            target = conn.execute("SELECT key FROM entries WHERE key = ? OR upper(label) = ?",
                                  (value.upper(), value.upper())).fetchone()
            if target:
                refs.append({"field": name, **_entry_summary(conn, target["key"])})
    detail["entry"] = {
        "key": e["key"], "id": e["id"], "label": e["label"], "section": e["section"], "kind": e["kind"],
        "name": e["name"], "description": e["description"], "fields": fields,
        "names": json.loads(e["names_json"]), "raw": json.loads(e["raw_json"]),
    }
    detail["variants"] = [dict(r) for r in conn.execute(
        f"SELECT {IMAGE_COLUMNS} FROM images i WHERE i.entry_key = ? ORDER BY i.variant != '', i.variant, i.path",
        (key,))]
    detail["parent"] = _entry_summary(conn, e["parent"]) if e["parent"] else None
    detail["references"] = refs
    children = [r["key"] for r in conn.execute(
        "SELECT key FROM entries WHERE parent = ? ORDER BY rowid", (key,))]
    detail["children"] = [_entry_summary(conn, k) for k in children[:60]]
    detail["children_total"] = len(children)
    # Entries that point at this one, e.g. the skins of a weapon.
    needles = [x for x in (e["id"], e["label"]) if x]
    referrers = []
    for needle in needles:
        referrers += [r["key"] for r in conn.execute(
            "SELECT key FROM entries WHERE instr(fields_json, ?) > 0 AND key != ? ORDER BY name",
            (json.dumps(needle), key))]
    referrers = list(dict.fromkeys(referrers))
    detail["referenced_by"] = [_entry_summary(conn, k) for k in referrers[:60]]
    detail["referenced_by_total"] = len(referrers)
    return detail


def catalog_overview(conn: sqlite3.Connection) -> list[dict]:
    """What the catalog holds for each kind of entry: which fields are filled
    in, how often, with a few example values."""
    kinds: dict[str, dict] = {}
    for kind, description, names_json, fields_json in conn.execute(
            "SELECT kind, description, names_json, fields_json FROM entries ORDER BY rowid"):
        k = kinds.setdefault(kind, {"kind": kind, "entries": 0, "name_missing": 0,
                                    "description": 0, "other_languages": 0, "fields": {}})
        k["entries"] += 1
        k["description"] += bool(description)
        k["other_languages"] += names_json != "{}"
        for name, value in json.loads(fields_json).items():
            f = k["fields"].setdefault(name, {"field": name, "count": 0, "examples": []})
            f["count"] += 1
            if len(f["examples"]) < 3 and value not in f["examples"]:
                f["examples"].append(value)
    for row in conn.execute("SELECT kind, COUNT(*) FROM entries WHERE name = '' GROUP BY kind"):
        kinds[row[0]]["name_missing"] = row[1]
    with_images = dict(conn.execute(
        "SELECT e.kind, COUNT(DISTINCT e.key) FROM entries e JOIN images i ON i.entry_key = e.key "
        "GROUP BY e.kind").fetchall())
    out = []
    for kind in sorted(kinds):
        k = kinds[kind]
        k["with_images"] = with_images.get(kind, 0)
        k["fields"] = sorted(k["fields"].values(), key=lambda f: -f["count"])
        out.append(k)
    return out


def image(conn: sqlite3.Connection, path: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT path, size, mtime_ns, width, height FROM images WHERE path = ?", (path,)
    ).fetchone()


def all_images(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    """Every image, folder by folder, for making thumbnails ahead of time."""
    return conn.execute(
        "SELECT i.path, i.size, i.mtime_ns, i.width, i.height, a.path IS NOT NULL AS analyzed "
        "FROM images i LEFT JOIN analyzed a ON a.path = i.path "
        "ORDER BY i.folder COLLATE NOCASE, i.path"
    ).fetchall()


def save_colors(conn: sqlite3.Connection, results: list[tuple[str, dict[int, float]]]) -> None:
    with conn:
        for path, shares in results:
            conn.execute("DELETE FROM image_colors WHERE path = ?", (path,))
            conn.executemany("INSERT INTO image_colors VALUES (?, ?, ?)",
                             ((path, b, w) for b, w in shares.items()))
            conn.execute("INSERT OR IGNORE INTO analyzed VALUES (?)", (path,))
