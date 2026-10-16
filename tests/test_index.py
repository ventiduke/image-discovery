import json
import os
import sqlite3
from contextlib import closing
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

    assert search("gekko") == ["C1", "C1_ABILITY1"]  # the ability through its character
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


def F(*folders, **kw):
    return index.Filters(list(folders), **kw)


def test_list_images_sorts_pages_and_filters(content, tmp_path):
    db = db_of(tmp_path)
    (content / "Abilities" / "big.png").write_bytes(PNG + b"\0" * 1000)
    index.build(open_library(content), db)
    with closing(index.connect(db)) as conn:
        total, rows = index.list_images(conn, F("Maps", "Abilities"))
        assert total == 5
        # By English name, unmatched files by filename, variants after the base image.
        assert [(r["name"] or r["stem"], r["variant"]) for r in rows] == [
            ("Ascent", ""), ("Ascent", "splash"), ("big", ""), ("mystery", ""), ("Wingman", ""),
        ]
        assert rows[0]["kind"] == "maps" and rows[2]["entry_key"] is None
        assert [r["path"] for r in index.list_images(conn, F("Maps", "Abilities"), offset=1, limit=2)[1]] == \
            ["Maps/M1_splash.png", "Abilities/big.png"]
        assert index.list_images(conn, F("Maps", "Abilities"), sort="size")[1][0]["path"] == "Abilities/big.png"
        assert [r["folder"] for r in index.list_images(conn, F("Maps", "Abilities"), sort="folder")[1]] == \
            ["Abilities", "Abilities", "Maps", "Maps", "Maps"]
        assert index.list_images(conn, F()) == (0, [])
        assert index.list_images(conn, F("Nope"))[0] == 0
        assert index.image(conn, "Maps/M1.png")["width"] == 64
        assert index.image(conn, "../secret.png") is None
        assert len(index.all_images(conn)) == 5


def paths(conn, f):
    return sorted(r["path"] for r in index.list_images(conn, f, limit=1000)[1])


def test_search(content, tmp_path):
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    with closing(index.connect(db)) as conn:
        everything = F("Maps", "Abilities")
        search = lambda q, **kw: paths(conn, F("Maps", "Abilities", q=q, **kw))  # noqa: E731
        assert search("asc") == ["Maps/M1.png", "Maps/M1_splash.png"]  # prefix of a name
        assert search("ASCENT") == search("asc")
        assert search("gekko") == ["Abilities/C1_Ability1.png"]  # an ability, by its character
        assert search("crew") == []  # a description belongs to its own item only (Gekko has no image)
        assert search("wing gek") == ["Abilities/C1_Ability1.png"]  # every word must match
        assert search("wing ascent") == []
        assert search("mystery") == ["Maps/mystery.png"]  # unmatched files by filename
        assert search("c1_ab") == ["Abilities/C1_Ability1.png"]  # IDs and labels
        assert search("(fr)") == search("fr") == []  # other languages only when asked
        assert len(search("fr", all_languages=True)) == 3
        assert search('"*:') == []  # punctuation can't break the query
        assert search("100%_") == []
        assert len(paths(conn, everything)) == 4


def test_tags_and_kind_and_variant_filters(content, tmp_path):
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    with closing(index.connect(db)) as conn:
        assert paths(conn, F("Maps", "Abilities", tags=["ascent"])) == ["Maps/M1.png", "Maps/M1_splash.png"]
        assert paths(conn, F("Maps", "Abilities", tags=["Ascent", "wingman"])) == []
        assert paths(conn, F("Maps", "Abilities", kinds=["characters.abilities"])) == ["Abilities/C1_Ability1.png"]
        assert paths(conn, F("Maps", "Abilities", variants=["splash"])) == ["Maps/M1_splash.png"]
        assert len(paths(conn, F("Maps", "Abilities", variants=["base"]))) == 3
        assert len(paths(conn, F("Maps", "Abilities", variants=["base", "splash"]))) == 4


def test_colors_filter_and_sort(content, tmp_path):
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    with closing(index.connect(db)) as conn:
        index.save_colors(conn, [("Maps/M1.png", {4: 0.5, 3: 0.4}), ("Maps/M1_splash.png", {4: 0.8}),
                                 ("Maps/mystery.png", {4: 0.05, 20: 0.9}), ("Abilities/C1_Ability1.png", {})])
        f = F("Maps", "Abilities", colors=[4])
        assert [r["path"] for r in index.list_images(conn, f)[1]] == ["Maps/M1_splash.png", "Maps/M1.png"]
        assert paths(conn, F("Maps", "Abilities", colors=[4, 3])) == ["Maps/M1.png"]
        facets = index.facets(conn, F("Maps", "Abilities"))
        counts = {c["id"]: c["count"] for c in facets["colors"]}
        assert (counts[4], counts[3], counts[20], counts[0]) == (2, 1, 1, 0)
        assert (facets["colors_analyzed"], facets["colors_total"]) == (4, 4)
        # Re-saving replaces an image's colours.
        index.save_colors(conn, [("Maps/M1.png", {20: 1.0})])
        assert paths(conn, F("Maps", colors=[4])) == ["Maps/M1_splash.png"]


def test_changed_files_lose_their_colors(content, tmp_path):
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    with closing(index.connect(db)) as conn:
        index.save_colors(conn, [("Maps/M1.png", {4: 1.0}), ("Maps/M1_splash.png", {4: 1.0})])
    os.utime(content / "Maps" / "M1.png", ns=(1, 1))
    index.build(open_library(content), db)
    with closing(index.connect(db)) as conn:
        assert paths(conn, F("Maps", colors=[4])) == ["Maps/M1_splash.png"]
        assert {r["path"]: r["analyzed"] for r in index.all_images(conn)}["Maps/M1.png"] == 0


def test_facets(content, tmp_path):
    catalog = json.loads((content / "PublicContentCatalog.json").read_text())
    catalog["maps"] += [{"id": "M3", "name": loc("Ascent Night")}, {"id": "M4", "name": loc("Night Market")}]
    (content / "PublicContentCatalog.json").write_text(json.dumps(catalog))
    for name in ("M2", "M3", "M4"):
        (content / "Maps" / f"{name}.png").write_bytes(PNG)
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    with closing(index.connect(db)) as conn:
        everything = F("Maps", "Abilities")
        tags = {t["tag"]: t["refs"] for t in index.facets(conn, everything, min_refs=1)["tags"]}
        # Items, not files: Ascent's two variants count once.
        assert tags == {"ascent": 2, "night": 2, "bind": 1, "market": 1, "wingman": 1}
        assert {t["tag"] for t in index.facets(conn, everything, min_refs=2)["tags"]} == {"ascent", "night"}
        assert index.facets(conn, everything, min_refs=2)["tags_total"] == 2
        narrowed = {t["tag"]: t["refs"] for t in index.facets(conn, F("Maps", tags=["night"]), min_refs=1)["tags"]}
        assert narrowed == {"night": 2, "ascent": 1, "market": 1}
        result = index.facets(conn, F("Maps", "Abilities", kinds=["maps"]))
        # A filter's own counts ignore its own selection.
        assert {k["kind"]: k["count"] for k in result["kinds"]} == {"maps": 5, "characters.abilities": 1}
        assert {v["variant"]: v["count"] for v in result["variants"]} == {"base": 4, "splash": 1}
        assert next(t for t in index.facets(conn, everything, min_refs=1)["tags"] if t["tag"] == "ascent")["name"] == "Ascent"


def test_image_detail(content, tmp_path):
    catalog = json.loads((content / "PublicContentCatalog.json").read_text())
    catalog["weapons"] = [{"id": "W1", "label": "W1", "name": loc("Vandal"), "category": "Rifle"}]
    catalog["weaponSkins"] = [{"id": "S1", "label": "S1", "name": loc("Reaver Vandal"), "weaponId": "W1",
                               "chromas": [{"id": "K1", "label": "K1", "name": loc("Reaver Vandal Red")}]}]
    (content / "PublicContentCatalog.json").write_text(json.dumps(catalog))
    (content / "Weapons").mkdir()
    for name in ("Weapons/W1.png", "Weapons/W1_killstream.png", "Maps/K1.png"):
        (content / name).write_bytes(PNG)
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    with closing(index.connect(db)) as conn:
        index.save_colors(conn, [("Weapons/W1.png", {4: 0.7, 3: 0.3})])
        weapon = index.image_detail(conn, "Weapons/W1.png")
        assert weapon["entry"]["name"] == "Vandal" and weapon["entry"]["fields"] == {"category": "Rifle"}
        assert [v["variant"] for v in weapon["variants"]] == ["", "killstream"]
        assert [c["name"] for c in weapon["colors"]] == ["Red", "White"] and weapon["analyzed"]
        assert [r["name"] for r in weapon["referenced_by"]] == ["Reaver Vandal"]  # the skin
        assert weapon["entry"]["tags"] == [{"tag": "vandal", "name": "Vandal"}]
        assert weapon["entry"]["names"] == {"fr-FR": "Vandal (fr)"}

        chroma = index.image_detail(conn, "Maps/K1.png")
        assert chroma["parent"]["name"] == "Reaver Vandal" and chroma["parent"]["image"] is None
        assert chroma["colors"] == [] and not chroma["analyzed"]
        skin = index._entry_summary(conn, "S1")
        assert skin["kind"] == "weaponSkins"
        assert [r["field"] for r in index.image_detail(conn, "Weapons/W1.png")["references"]] == []

        ability = index.image_detail(conn, "Abilities/C1_Ability1.png")
        assert ability["parent"]["name"] == "Gekko"
        unmatched = index.image_detail(conn, "Maps/mystery.png")
        assert unmatched["entry"] is None and unmatched["image"]["path"] == "Maps/mystery.png"
        assert index.image_detail(conn, "nope.png") is None


def test_references_resolve_to_entries(content, tmp_path):
    catalog = json.loads((content / "PublicContentCatalog.json").read_text())
    catalog["weapons"] = [{"id": "W1", "label": "W1", "name": loc("Vandal")}]
    catalog["weaponSkins"] = [{"id": "S1", "label": "S1", "name": loc("Prime"), "weaponId": "W1"}]
    (content / "PublicContentCatalog.json").write_text(json.dumps(catalog))
    (content / "Weapons").mkdir()
    (content / "Weapons" / "S1.png").write_bytes(PNG)
    (content / "Weapons" / "W1.png").write_bytes(PNG)
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    with closing(index.connect(db)) as conn:
        skin = index.image_detail(conn, "Weapons/S1.png")
        assert [(r["field"], r["name"], r["image"]["path"]) for r in skin["references"]] == \
            [("weaponId", "Vandal", "Weapons/W1.png")]
        # The skin is found by its weapon's name, even though "Vandal" isn't in its own.
        assert paths(conn, F("Weapons", q="vandal")) == ["Weapons/S1.png", "Weapons/W1.png"]


def test_catalog_overview(content, tmp_path):
    db = db_of(tmp_path)
    index.build(open_library(content), db)
    with closing(index.connect(db)) as conn:
        kinds = {k["kind"]: k for k in index.catalog_overview(conn)}
    assert set(kinds) == {"maps", "characters", "characters.abilities"}
    assert (kinds["maps"]["entries"], kinds["maps"]["with_images"], kinds["maps"]["other_languages"]) == (2, 1, 2)
    assert kinds["characters"]["description"] == 1
