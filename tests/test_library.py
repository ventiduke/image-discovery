from pathlib import Path

import pytest

from imagefind.library import LibraryError, open_library

CONTENT = Path(__file__).parent.parent / "content"


def make(tmp_path, files):
    for rel, data in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    return tmp_path


def test_sample_content():
    lib = open_library(CONTENT)
    assert lib.catalog.name == "PublicContentCatalog.json"
    assert lib.version == "13.06.00.5435758"
    counts = {f.name: f.images for f in lib.folders}
    assert counts["Weapons"] == 70 and counts["Maps"] == 4
    assert lib.images == sum(counts.values())
    assert lib.warnings == []


def test_counts_nested_folders_and_skips_hidden_and_other_files(tmp_path):
    root = make(tmp_path, {
        "cat.json": b'{"version": "1.0", "maps": []}',
        "Maps/a.png": b"",
        "Maps/b_splash.PNG": b"",
        "Maps/readme.md": b"",
        "Maps/.DS_Store": b"",
        "Maps/old/c.webp": b"",
        ".git/x.png": b"",
    })
    lib = open_library(root)
    assert [(f.name, f.images, f.other_files) for f in lib.folders] == [("Maps", 3, 1)]
    assert lib.version == "1.0"


def test_folders_sorted_case_insensitively(tmp_path):
    root = make(tmp_path, {"c.json": b"{}", "weapons/a.png": b"", "Maps/a.png": b"", "Abilities/a.png": b""})
    assert [f.name for f in open_library(root).folders] == ["Abilities", "Maps", "weapons"]


def test_warns_about_missing_catalog_loose_images_and_no_folders(tmp_path):
    root = make(tmp_path, {"a.png": b"", "b.png": b""})
    warnings = open_library(root).warnings
    assert any("No catalog" in w for w in warnings)
    assert any("No image folders" in w for w in warnings)
    assert any("2 images sit directly" in w for w in warnings)


def test_prefers_the_public_catalog_and_reports_the_others(tmp_path):
    root = make(tmp_path, {
        "big.json": b"{" + b" " * 1000 + b"}",
        "PublicContentCatalog.json": b'{"version": "2"}',
        "Maps/a.png": b"",
    })
    lib = open_library(root)
    assert lib.catalog.name == "PublicContentCatalog.json"
    assert any("ignoring big.json" in w for w in lib.warnings)


def test_catalog_without_version(tmp_path):
    root = make(tmp_path, {"c.json": b'{"maps": []}', "Maps/a.png": b""})
    assert open_library(root).version is None


def test_accepts_quoted_windows_style_path():
    assert open_library(f'"{CONTENT}"').root == CONTENT.resolve()


@pytest.mark.parametrize("bad", ["", "   ", "does/not/exist"])
def test_rejects_bad_paths(bad):
    with pytest.raises(LibraryError):
        open_library(bad)


def test_rejects_a_file(tmp_path):
    (tmp_path / "x.json").write_text("{}")
    with pytest.raises(LibraryError, match="Not a folder"):
        open_library(tmp_path / "x.json")
