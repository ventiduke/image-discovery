from PIL import Image

from imagefind import colors, tags


def test_bins():
    assert len(colors.BINS) == 28 and [b.id for b in colors.BINS] == list(range(28))
    names = [b.name for b in colors.BINS]
    assert names[:4] == ["Black", "Dark grey", "Light grey", "White"]
    assert {"Red", "Dark red", "Brown", "Olive", "Blue", "Pink"} <= set(names)


def test_classify():
    name = lambda rgb: colors.BY_ID[colors.classify(*rgb)].name  # noqa: E731
    assert name((10, 10, 10)) == "Black"
    assert name((250, 250, 250)) == "White"
    assert name((100, 100, 100)) == "Dark grey"
    assert name((180, 180, 180)) == "Light grey"
    assert name((230, 30, 30)) == "Red"
    assert name((250, 20, 60)) == "Red"  # hues wrap around
    assert name((120, 20, 20)) == "Dark red"
    assert name((240, 140, 30)) == "Orange"
    assert name((120, 70, 20)) == "Brown"
    assert name((30, 60, 230)) == "Blue"


def test_analyze_skips_transparency_and_small_shares():
    im = Image.new("RGBA", (100, 100), (0, 0, 0, 0))
    im.paste((230, 30, 30, 255), (0, 0, 60, 100))  # 60% of the image, all of what's visible
    im.paste((30, 60, 230, 255), (60, 0, 61, 100))  # a sliver: under 3%
    shares = colors.analyze(im)
    assert list(shares) == [colors.classify(230, 30, 30)]
    assert shares[colors.classify(230, 30, 30)] > 0.95
    assert colors.analyze(Image.new("RGBA", (10, 10), (0, 0, 0, 0))) == {}
    assert sum(colors.analyze(Image.new("RGB", (10, 10), (250, 250, 250))).values()) == 1.0


def test_words():
    assert tags.words("Reaver Vandal Level 2") == ["Reaver", "Vandal", "Level"]
    assert tags.words("Viper's Pit") == ["Viper", "Pit"]
    assert tags.words("The Range of the Range") == ["Range"]
    assert tags.words("K/O") == []
    assert tags.words("Gauntlet: Glitched") == ["Gauntlet", "Glitched"]
    assert tags.words("// VCT25 x Sentinels") == ["VCT25", "Sentinels"]


def test_tag_names_pick_the_usual_spelling():
    per_name, display = tags.tag_names(["VCT Card", "VCT Spray", "Vct Buddy"])
    assert per_name["Vct Buddy"] == ["vct", "buddy"]
    assert display["vct"] == "VCT"
