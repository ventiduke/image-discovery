import struct
import zlib
from pathlib import Path

import pytest

from imagefind.scan import _dimensions, dimensions, match, walk

CONTENT = Path(__file__).parent.parent / "content"

LOOKUP = {
    "0AFB2636-4093-C63B-4EF1-1E97966E2A3E": "W",
    "E370FA57-4757-3604-3648-499E1F642D3F": "CH",
    "E370FA57-4757-3604-3648-499E1F642D3F_ABILITY1": "AB",
}


@pytest.mark.parametrize("stem, expected", [
    ("0AFB2636-4093-C63B-4EF1-1E97966E2A3E", ("W", "")),
    ("0AFB2636-4093-C63B-4EF1-1E97966E2A3E_killstream", ("W", "killstream")),
    ("0afb2636-4093-c63b-4ef1-1e97966e2a3e_Splash", ("W", "splash")),  # any case
    ("0AFB2636-4093-C63B-4EF1-1E97966E2A3E_list_view", ("W", "list_view")),
    # The ability's label wins over reading "Ability1" as a variant of the character.
    ("E370FA57-4757-3604-3648-499E1F642D3F_Ability1", ("AB", "")),
    ("E370FA57-4757-3604-3648-499E1F642D3F_Ability1_icon", ("AB", "icon")),
    ("E370FA57-4757-3604-3648-499E1F642D3F_Ability2", ("CH", "ability2")),
    ("unknown_thing", (None, "")),
    ("", (None, "")),
])
def test_match(stem, expected):
    assert match(stem, LOOKUP.get) == expected


def png(w, h):
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr
            + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr)))


def test_dimensions_of_each_format():
    assert _dimensions(png(456, 100)) == (456, 100)
    assert _dimensions(b"GIF89a" + struct.pack("<HH", 30, 20)) == (30, 20)
    vp8x = b"RIFF\0\0\0\0WEBPVP8X" + b"\0" * 8 + (639).to_bytes(3, "little") + (479).to_bytes(3, "little")
    assert _dimensions(vp8x) == (640, 480)
    bits = (99 & 0x3FFF) | ((49 & 0x3FFF) << 14)
    vp8l = b"RIFF\0\0\0\0WEBPVP8L\0\0\0\0\x2f" + bits.to_bytes(4, "little")
    assert _dimensions(vp8l) == (100, 50)
    vp8 = b"RIFF\0\0\0\0WEBPVP8 " + b"\0" * 10 + struct.pack("<HH", 320, 240)
    assert _dimensions(vp8) == (320, 240)
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\0" + b"\0" * 9
    sof0 = b"\xff\xc0" + struct.pack(">HBHH", 17, 8, 1080, 1920) + b"\0" * 10
    assert _dimensions(b"\xff\xd8" + app0 + sof0) == (1920, 1080)
    assert _dimensions(b"not an image at all") is None
    assert _dimensions(b"\xff\xd8\xff") is None


def test_dimensions_of_sample_files():
    assert dimensions(CONTENT / "Maps" / "1C18AB1F-420D-0D8B-71D0-77AD3C439115_listview.png") == (456, 100)
    assert dimensions(CONTENT / "nope.png") is None


def test_walk(tmp_path):
    for rel in ["A/x.png", "A/sub/y.JPG", "A/notes.txt", "A/.hidden.png", "A/.git/z.png", "B/w.webp"]:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_bytes(b"12345")
    files = list(walk(tmp_path, ["A", "B"]))
    assert [(f.path, f.folder, f.stem, f.size) for f in files] == [
        ("A/x.png", "A", "x", 5), ("A/sub/y.JPG", "A", "y", 5), ("B/w.webp", "B", "w", 5),
    ]
