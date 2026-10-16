import random

from PIL import Image, ImageDraw

from imagefind import measure


def test_opaque_calm_image():
    im = Image.new("RGB", (320, 180), (20, 30, 60))
    m = measure.measure(im)
    assert m.transparent == 0 and m.fill == 1.0
    assert m.brightness < 0.2 and m.busyness < 0.01
    assert m.text_color == "white" and m.best_on is None


def test_light_image_wants_dark_text():
    m = measure.measure(Image.new("RGB", (200, 100), (240, 240, 235)))
    assert m.text_color == "black"


def test_white_silhouette_on_transparency():
    im = Image.new("RGBA", (300, 300), (0, 0, 0, 0))
    ImageDraw.Draw(im).ellipse((60, 60, 240, 240), fill=(255, 255, 255, 255))
    m = measure.measure(im)
    assert 0.70 < m.transparent < 0.73  # 1 - pi * 90^2 / 300^2
    assert 0.35 < m.fill < 0.37  # 180 x 180 box in 300 x 300
    assert m.brightness > 0.95
    assert m.best_on == "dark" and m.text_color is None


def test_busy_side_and_calm_side():
    random.seed(1)
    im = Image.new("RGB", (300, 120), (90, 90, 90))
    d = ImageDraw.Draw(im)
    for _ in range(3000):  # noise on the left two thirds
        x, y = random.randrange(0, 200), random.randrange(0, 120)
        d.point((x, y), fill=(255, 255, 255) if random.random() < .5 else (0, 0, 0))
    m = measure.measure(im)
    assert m.calm_side == "right"
    assert m.calm_busyness < m.busyness


def test_fully_transparent():
    m = measure.measure(Image.new("RGBA", (50, 50), (0, 0, 0, 0)))
    assert m.transparent == 1 and m.fill == 0 and m.best_on is None


def test_rules_are_named_and_described():
    assert set(measure.USES) == {"background", "header", "panel", "icon", "cutout"}
    assert all(label and desc and cond for label, desc, cond in measure.USES.values())
    assert set(measure.SHAPES) == {"banner", "landscape", "square", "portrait"}
