import json
import shutil
from pathlib import Path

import pytest
from PIL import Image, ImageDraw

from imagefind import cli, index

CONTENT = Path(__file__).parent.parent / "content"


def loc(text):
    return {"defaultText": text, "localizedByCulture": {"en-US": text}}


@pytest.fixture
def content(tmp_path, monkeypatch):
    """A small library with real pictures: a wide opaque scene, a white icon, a red icon."""
    root = tmp_path / "content"
    (root / "Maps").mkdir(parents=True)
    (root / "Icons").mkdir()
    catalog = {"version": "9", "maps": [
        {"id": "M1", "label": "M1", "name": loc("Sunset Harbor")},
        {"id": "I1", "label": "I1", "name": loc("White Star")},
        {"id": "I2", "label": "I2", "name": loc("Red Star")},
    ]}
    (root / "PublicContentCatalog.json").write_text(json.dumps(catalog))
    scene = Image.new("RGB", (1920, 1080), (25, 40, 90))
    ImageDraw.Draw(scene).rectangle((1300, 300, 1800, 900), fill=(240, 120, 30))
    scene.save(root / "Maps" / "M1.png")
    scene.crop((0, 0, 1920, 480)).save(root / "Maps" / "M1_banner.png")
    for key, colour in (("I1", (255, 255, 255, 255)), ("I2", (220, 30, 30, 255))):
        icon = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
        ImageDraw.Draw(icon).ellipse((40, 40, 472, 472), fill=colour)
        icon.save(root / "Icons" / f"{key}.png")
    # Keep the index out of the user's cache.
    monkeypatch.setattr(index, "cache_dir", lambda r: tmp_path / "cache")
    return root


def run(capsys, *argv):
    cli.main(list(argv))
    return capsys.readouterr()


def test_find_json(content, capsys):
    out = run(capsys, "find", "--content", str(content), "--json")
    data = json.loads(out.out)
    assert data["total"] == 4
    assert "Preparing 4 images" in out.err
    by_file = {i["file"]: i for i in data["images"]}
    scene = by_file["Maps/M1.png"]
    assert scene["path"] == str(content / "Maps" / "M1.png")
    assert scene["name"] == "Sunset Harbor" and scene["width"] == 1920 and scene["aspect"] == 1.78
    assert "background" in scene["good_for"] and "header" in scene["good_for"]
    assert scene["text_color"] == "white" and scene["calm_side"] in ("left", "center")
    white = by_file["Icons/I1.png"]
    assert set(white["good_for"]) == {"icon", "cutout"} and white["best_on"] == "dark"
    assert by_file["Icons/I2.png"]["colors"][0]["name"] == "Red"
    # Second run: nothing left to prepare.
    assert "Preparing" not in run(capsys, "find", "--content", str(content)).err


def test_find_filters(content, capsys):
    def files(*args):
        data = json.loads(run(capsys, "find", "--content", str(content), "--json", *args).out)
        return [i["file"] for i in data["images"]]

    assert files("star") == ["Icons/I2.png", "Icons/I1.png"]
    assert files("--use", "icon", "--color", "red") == ["Icons/I2.png"]
    # Best colour match first: the banner is cropped from the blue sky, without the orange.
    assert files("--color", "Dark Blue", "--use", "header") == ["Maps/M1_banner.png", "Maps/M1.png"]
    assert files("--shape", "banner") == ["Maps/M1_banner.png"]
    assert files("--folder", "icons", "--min-width", "500") == ["Icons/I2.png", "Icons/I1.png"]
    assert files("--variant", "banner") == ["Maps/M1_banner.png"]
    assert len(files("--limit", "1")) == 1


def test_find_text_output(content, capsys):
    out = run(capsys, "find", "star", "--content", str(content)).out
    assert out.startswith("2 matches")
    assert "1. Red Star (maps): 512×512; good for icon, cutout; transparent bg" in out
    assert "best on dark backgrounds" in out
    assert str(content / "Icons" / "I1.png") in out


def test_bad_arguments(content, capsys):
    with pytest.raises(SystemExit, match="unknown colour 'mauve'"):
        run(capsys, "find", "--content", str(content), "--color", "mauve")
    with pytest.raises(SystemExit, match="no folder 'Nope'"):
        run(capsys, "find", "--content", str(content), "--folder", "Nope")
    with pytest.raises(SystemExit):
        run(capsys, "find", "--content", str(content), "--use", "poster")
    with pytest.raises(SystemExit, match="Folder not found"):
        run(capsys, "find", "--content", str(content / "missing"))


def test_sheet(content, capsys, tmp_path):
    out = run(capsys, "sheet", "--content", str(content), "--out", str(tmp_path / "s.png"), "--columns", "2")
    assert "Contact sheet:" in out.out and "  1. " in out.out
    with Image.open(tmp_path / "s.png") as im:
        assert im.size == (2 * (cli.CELL + cli.PAD) + cli.PAD, 2 * (cli.CELL + cli.CAPTION + cli.PAD) + cli.PAD)
    data = json.loads(run(capsys, "sheet", "star", "--content", str(content), "--out",
                          str(tmp_path / "t.png"), "--json").out)
    assert data["sheet"] == str(tmp_path / "t.png") and len(data["images"]) == 2
    with pytest.raises(SystemExit, match="nothing matches"):
        run(capsys, "sheet", "zebra", "--content", str(content), "--out", str(tmp_path / "u.png"))


def export(capsys, content, *args):
    return json.loads(run(capsys, "export", "--content", str(content), *args).out)


def test_export_cover_contain_and_formats(content, capsys, tmp_path):
    header = export(capsys, content, "Maps/M1.png", "--out", str(tmp_path / "h.png"), "--fit", "1600x400")
    assert (header["width"], header["height"], header["upscaled"]) == (1600, 400, False)
    assert header["source"]["name"] == "Sunset Harbor"
    with Image.open(tmp_path / "h.png") as im:
        assert im.size == (1600, 400)

    icon = export(capsys, content, "I1", "--out", str(tmp_path / "i.png"), "--fit", "256x128", "--mode", "contain")
    with Image.open(tmp_path / "i.png") as im:
        assert im.size == (256, 128) and im.mode == "RGBA"
        assert im.getpixel((0, 0))[3] == 0  # padded with transparency
    assert icon["upscaled"] is False

    big = export(capsys, content, str(content / "Icons" / "I2.png"), "--out", str(tmp_path / "b.jpg"), "--fit", "1024x1024")
    assert big["upscaled"] is True
    with Image.open(tmp_path / "b.jpg") as im:
        assert im.mode == "RGB" and im.getpixel((0, 0)) == (255, 255, 255)  # transparency on white

    copy = export(capsys, content, "M1", "--variant", "banner", "--out", str(tmp_path / "c.webp"))
    assert (copy["width"], copy["height"]) == (1920, 480)


def test_export_crop_focus(content, capsys, tmp_path):
    # The orange block sits right of centre: focusing right keeps more of it.
    export(capsys, content, "Maps/M1.png", "--out", str(tmp_path / "r.png"), "--fit", "500x500", "--focus", "right")
    export(capsys, content, "Maps/M1.png", "--out", str(tmp_path / "l.png"), "--fit", "500x500", "--focus", "left")
    with Image.open(tmp_path / "r.png") as r, Image.open(tmp_path / "l.png") as left:
        orange = lambda im: sum(1 for p in im.convert("RGB").get_flattened_data() if p[0] > 200)  # noqa: E731
        assert orange(r) > 0 and orange(left) == 0


def test_export_refusals(content, capsys, tmp_path):
    with pytest.raises(SystemExit, match="inside the content folder"):
        export(capsys, content, "M1", "--out", str(content / "Maps" / "x.png"))
    with pytest.raises(SystemExit, match="must end in"):
        export(capsys, content, "M1", "--out", str(tmp_path / "x.gif"))
    with pytest.raises(SystemExit, match="no image found"):
        export(capsys, content, "ZZZ", "--out", str(tmp_path / "x.png"))
    with pytest.raises(SystemExit, match="has no 'splash' variant; it has: base, banner"):
        export(capsys, content, "M1", "--variant", "splash", "--out", str(tmp_path / "x.png"))
    with pytest.raises(SystemExit, match="WIDTHxHEIGHT"):
        export(capsys, content, "M1", "--out", str(tmp_path / "x.png"), "--fit", "wide")
    with pytest.raises(SystemExit, match="not inside the content folder"):
        export(capsys, content, str(tmp_path / "x.png"), "--out", str(tmp_path / "y.png"))


def test_stats(content, capsys):
    out = run(capsys, "stats", "--content", str(content)).out
    assert "Measurements per folder" in out and "Icons" in out and "Maps" in out
    assert "background" in out and "Total" in out


def test_install_skill(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    out = run(capsys, "install-skill").out
    installed = tmp_path / ".claude" / "skills" / "image-assets" / "SKILL.md"
    assert str(installed) in out
    text = installed.read_text()
    assert "{{REPO}}" not in text and str(index.REPO_ROOT) in text
    assert text.startswith("---\nname: image-assets\n")


def test_entry_point_dispatches(monkeypatch, capsys, tmp_path):
    from imagefind.__main__ import main

    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    main(["install-skill"])
    assert "Installed the image-assets skill" in capsys.readouterr().out


def test_sample_content_still_works(tmp_path, monkeypatch, capsys):
    root = tmp_path / "content"
    shutil.copytree(CONTENT, root)
    monkeypatch.setattr(index, "cache_dir", lambda r: tmp_path / "cache")
    data = json.loads(run(capsys, "find", "kasbah", "--content", str(root), "--json").out)
    assert {i["name"] for i in data["images"]} == {"Kasbah"}
