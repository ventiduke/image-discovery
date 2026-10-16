import json
import os
import sqlite3
from pathlib import Path

import pytest

from imagefind import index
from imagefind.library import open_library

CONTENT = Path(__file__).parent.parent / "content"


def loc(text):
    return {"defaultText": text, "localizedByCulture": {"en-US": text, "fr-FR": text + " (fr)"}}


CATALOG = {
    "version": "1.0",
    "maps": [{"id": "M1", "label": "M1", "name": loc("Ascent")}, {"id": "M2", "label": "M2", "name": loc("Bind")}],
    "characters": [{"id": "C1", "label": "C1", "name": loc("Gekko"), "description": loc("Leads a crew."),
                    "abilities": {"ability1": {"label": "C1_Ability1", "name": loc("Wingman")}}}],
}
PNG = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR" + (64).to_bytes(4, "big") + (32).to_bytes(4, "big")
       + b"\x08\x06\x00\x00\x00" + b"\0" * 4)


@pytest.fixture
def content(tmp_path):
    root = tmp_path / "content"
    root.mkdir()
    (root / "PublicContentCatalog.json").write_text(json.dumps(CATALOG))
    for rel in ["Maps/M1.png", "Maps/M1_splash.png", "Abilities/C1_Ability1.png", "Maps/mystery.png"]:
        (root / rel).parent.mkdir(exist_ok=True)
        (root / rel).write_bytes(PNG)
    return root


def db_of(tmp_path):
    return tmp_path / "cache" / "index.sqlite"


def rows(db, sql):
    with sqlite3.connect(db) as conn:
        return conn.execute(sql).fetchall()


def test_build_matches_images_to_entries(content, tmp_path):
    db = db_of(tmp_path)
    result = index.build(open_library(content), db)
    assert result.catalog_reloaded and result.images == 4 and result.headers_read == 4
    assert rows(db, "SELECT path, entry_key, variant, width, height FROM images ORDER BY path") == [
        ("Abilities/C1_Ability1.png", "C1_ABILITY1", "", 64, 32),
        ("Maps/M1.png", "M1", "", 64, 32),
        ("Maps/M1_splash.png", "M1", "splash", 64, 32),
        ("Maps/mystery.png", None, "", 64, 32),
    ]


def test_report(content, tmp_path):
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    r = index.report(db)
    assert (r["catalog_version"], r["images"], r["matched"], r["entries"], r["entries_with_images"]) == \
        ("1.0", 4, 3, 4, 2)
    maps = next(f for f in r["folders"] if f["name"] == "Maps")
    assert (maps["images"], maps["matched"], maps["unmatched"]) == (3, 2, 1)
    assert maps["variants"] == {"base": 1, "splash": 1}
    assert maps["kinds"] == {"maps": 2}
    assert {c["kind"]: (c["entries"], c["with_images"]) for c in r["coverage"]} == {
        "characters": (1, 0), "characters.abilities": (1, 1), "maps": (2, 1),
    }
    assert (r["unmatched_total"], r["unmatched"]) == (1, ["Maps/mystery.png"])


def test_search_table(content, tmp_path):
    db = db_of(tmp_path)
    index.build(open_library(content), db)

    def search(q):
        return sorted(k for (k,) in rows(db, f"SELECT key FROM entries_fts WHERE entries_fts MATCH '{q}'"))

    assert search("gekko") == ["C1"]
    assert search("wing*") == ["C1_ABILITY1"]
    assert search("crew") == ["C1"]  # descriptions
    assert search("other_names:fr") == ["C1", "C1_ABILITY1", "M1", "M2"]
    assert search("ident:M2") == ["M2"]


def test_rebuild_is_incremental(content, tmp_path):
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    again = index.build(open_library(content), db)
    assert (again.catalog_reloaded, again.headers_read, again.images) == (False, 0, 4)

    (content / "Maps" / "M2.png").write_bytes(PNG)
    os.utime(content / "Maps" / "M1.png", ns=(1, 1))  # changed on disk
    (content / "Maps" / "mystery.png").unlink()
    result = index.build(open_library(content), db)
    assert (result.catalog_reloaded, result.headers_read, result.images) == (False, 2, 4)
    assert rows(db, "SELECT COUNT(*) FROM images WHERE entry_key = 'M2'") == [(1,)]
    assert rows(db, "SELECT COUNT(*) FROM images WHERE path = 'Maps/mystery.png'") == [(0,)]


def test_catalog_change_rematches_files(content, tmp_path):
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    catalog = json.loads((content / "PublicContentCatalog.json").read_text())
    catalog["maps"].append({"id": "mystery", "label": "mystery", "name": loc("Mystery")})
    (content / "PublicContentCatalog.json").write_text(json.dumps(catalog))
    result = index.build(open_library(content), db)
    assert result.catalog_reloaded and result.headers_read == 0
    assert index.report(db)["unmatched_total"] == 0


def test_forced_rebuild_and_schema_change(content, tmp_path):
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    assert index.build(open_library(content), db, rebuild=True).headers_read == 4
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE meta SET value = 'old' WHERE key = 'schema'")
    assert index.build(open_library(content), db).catalog_reloaded


def test_without_catalog_everything_is_unmatched(content, tmp_path):
    (content / "PublicContentCatalog.json").unlink()
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    r = index.report(db)
    assert (r["images"], r["matched"], r["entries"], r["catalog_version"]) == (4, 0, 0, None)


def test_progress_is_reported(content, tmp_path):
    seen = []
    index.build(open_library(content), db_of(tmp_path), progress=lambda d, t: seen.append((d, t)))
    assert seen[-1] == (4, 4)


def test_sample_content_all_matches(tmp_path):
    db = db_of(tmp_path)
    index.build(open_library(CONTENT), db)
    r = index.report(db)
    assert r["images"] == r["matched"] == sum(f.images for f in open_library(CONTENT).folders)


def test_cache_dir(tmp_path, monkeypatch):
    assert index.cache_dir(CONTENT).parent == index.REPO_ROOT / ".cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    other = index.cache_dir(tmp_path / "drop")
    assert other.name.startswith("drop-") and str(other).startswith(str(tmp_path))
    assert index.cache_dir(tmp_path / "drop") == other
    assert index.cache_dir(tmp_path / "drop2") != other


def test_unreadable_catalog_raises(content, tmp_path):
    from imagefind.catalog import CatalogError

    (content / "PublicContentCatalog.json").write_text("{oops")
    with pytest.raises(CatalogError):
        index.build(open_library(content), db_of(tmp_path))


def test_content_folder_is_never_written(content, tmp_path):
    before = sorted(p.relative_to(content) for p in content.rglob("*"))
    index.build(open_library(content), db_of(tmp_path))
    assert sorted(p.relative_to(content) for p in content.rglob("*")) == before


def test_list_images_sorts_pages_and_filters(content, tmp_path):
    db = db_of(tmp_path)
    (content / "Abilities" / "big.png").write_bytes(PNG + b"\0" * 1000)
    index.build(open_library(content), db)
    with index.connect(db) as conn:
        total, rows = index.list_images(conn, ["Maps", "Abilities"])
        assert total == 5
        # By English name, unmatched files by filename, variants after the base image.
        assert [(r["name"] or r["stem"], r["variant"]) for r in rows] == [
            ("Ascent", ""), ("Ascent", "splash"), ("big", ""), ("mystery", ""), ("Wingman", ""),
        ]
        assert rows[0]["kind"] == "maps" and rows[2]["entry_key"] is None
        assert [r["path"] for r in index.list_images(conn, ["Maps", "Abilities"], offset=1, limit=2)[1]] == \
            ["Maps/M1_splash.png", "Abilities/big.png"]
        assert index.list_images(conn, ["Maps", "Abilities"], sort="size")[1][0]["path"] == "Abilities/big.png"
        assert [r["folder"] for r in index.list_images(conn, ["Maps", "Abilities"], sort="folder")[1]] == \
            ["Abilities", "Abilities", "Maps", "Maps", "Maps"]
        assert index.list_images(conn, []) == (0, [])
        assert index.list_images(conn, ["Nope"])[0] == 0
        assert index.image(conn, "Maps/M1.png")["width"] == 64
        assert index.image(conn, "../secret.png") is None
        assert len(index.all_images(conn)) == 5
