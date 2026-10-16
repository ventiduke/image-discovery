import time
from pathlib import Path

from PIL import Image

from imagefind import thumbs


def picture(path: Path, size, mode="RGBA") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new(mode, size, (200, 50, 50, 128) if mode == "RGBA" else 128).save(path)
    return path


def test_needs_thumbnail():
    assert thumbs.needs_thumbnail(2048, 2048)
    assert thumbs.needs_thumbnail(None, None)  # size unknown: try anyway
    assert not thumbs.needs_thumbnail(320, 100)
    assert not thumbs.needs_thumbnail(128, 128)


def test_thumb_path_changes_with_the_file():
    base = Path("cache")
    a = thumbs.thumb_path(base, "Maps/a.png", 10, 1)
    assert a == thumbs.thumb_path(base, "Maps/a.png", 10, 1)
    assert a != thumbs.thumb_path(base, "Maps/a.png", 10, 2)
    assert a != thumbs.thumb_path(base, "Maps/b.png", 10, 1)
    assert a.suffix == ".webp" and a.parent.parent == base


def test_make_keeps_aspect_and_transparency(tmp_path):
    src = picture(tmp_path / "wide.png", (1920, 1080))
    out = thumbs.make(src, tmp_path / "t" / "wide.webp")
    with Image.open(out) as im:
        assert im.format == "WEBP" and im.size == (320, 180) and im.mode == "RGBA"
    assert list((tmp_path / "t").iterdir()) == [out]  # no temp files left behind


def test_make_handles_other_modes(tmp_path):
    for mode in ("L", "P", "RGB"):
        src = picture(tmp_path / f"{mode}.png", (640, 640), mode)
        with Image.open(thumbs.make(src, tmp_path / f"{mode}.webp")) as im:
            assert im.size == (320, 320)


def test_warmer_makes_missing_thumbnails_and_reports_failures(tmp_path):
    jobs = [(picture(tmp_path / f"{i}.png", (800, 400)), tmp_path / "t" / f"{i}.webp") for i in range(6)]
    (tmp_path / "broken.png").write_bytes(b"not an image")
    jobs.append((tmp_path / "broken.png", tmp_path / "t" / "broken.webp"))
    errors = []
    finished = []
    warmer = thumbs.Warmer(jobs, total=len(jobs), workers=2, on_error=lambda job, e: errors.append(job[0].name),
                           on_finish=lambda: finished.append(1))
    warmer.join(30)
    assert not warmer.running
    assert (warmer.done, warmer.failed) == (7, 1)
    assert errors == ["broken.png"]
    assert all(target.exists() for _, target in jobs[:-1])
    assert finished == [1]  # once, from the last thread


def test_warmer_stops_between_images(tmp_path):
    slow = ((tmp_path / f"{i}.png", tmp_path / f"{i}.webp") for i in range(10_000))

    def jobs():
        for job in slow:
            time.sleep(0.01)
            yield job

    warmer = thumbs.Warmer(jobs(), total=10_000, workers=2)
    warmer.stop()
    warmer.join(5)
    assert not warmer.running and warmer.done < 10_000


def test_warmer_runs_any_job(tmp_path):
    seen = []
    warmer = thumbs.Warmer(range(50), total=50, process=seen.append, workers=3)
    warmer.join(10)
    assert sorted(seen) == list(range(50)) and warmer.done == 50
