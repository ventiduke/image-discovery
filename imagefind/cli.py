"""Commands for finding and exporting images without the browser, mainly for AI agents.

    imagefind find [words…] [filters]       list matching images (--json for structured output)
    imagefind sheet [words…] [filters]      the same, as one numbered contact-sheet image
    imagefind export IMAGE --out FILE       copy an image out, optionally cropped/resized to fit
    imagefind stats                         how the images measure (to tune the "good for" rules)
    imagefind install-skill                 install the image-assets skill for Claude Code

Every command brings the index up to date first. The first run after a new
content drop also makes thumbnails and measures every image, which takes a
minute or two; progress goes to stderr, results to stdout.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
import sys
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

from . import analysis, colors, index, measure, thumbs
from .catalog import CatalogError
from .library import Library, LibraryError, open_library

COMMANDS = ("find", "sheet", "export", "stats", "install-skill")
SKILL_NAME = "image-assets"
SKILL_FILE = Path(__file__).parent / "skills" / SKILL_NAME / "SKILL.md"


def fail(message: str) -> None:
    raise SystemExit(f"imagefind: {message}")


def note(message: str, end: str = "\n") -> None:
    print(message, end=end, file=sys.stderr, flush=True)


# -- getting ready --------------------------------------------------------------


@dataclass
class Prepared:
    library: Library
    db_path: Path
    thumbs_dir: Path


def prepare(content: str | Path | None) -> Prepared:
    """Open the content folder and bring its index, thumbnails and measurements up to date."""
    from .__main__ import default_content

    content = content or default_content()
    if not content:
        fail("no content folder found; pass --content PATH")
    try:
        library = open_library(content)
    except LibraryError as exc:
        fail(str(exc))
    cache = index.cache_dir(library.root)
    db_path = cache / "index.sqlite"
    try:
        index.build(library, db_path)
    except CatalogError as exc:
        fail(str(exc))
    warmer = analysis.start(library.root, db_path, cache / "thumbs")
    if warmer.total:
        note(f"Preparing {warmer.total:,} images (thumbnails, colours, measurements); "
             "this happens once per content drop.")
        while warmer.running:
            warmer.join(1)
            if sys.stderr.isatty():
                note(f"\r  {warmer.done:,} of {warmer.total:,}", end="")
        note(f"\r  done ({warmer.failed:,} could not be read)" if warmer.failed else "\r  done" + " " * 20)
    return Prepared(library, db_path, cache / "thumbs")


# -- filters shared by find and sheet ------------------------------------------------


def add_filter_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("words", nargs="*", help="search words: names, descriptions, IDs, related names")
    p.add_argument("--folder", action="append", default=[], help="only this folder (repeatable)")
    p.add_argument("--color", action="append", default=[],
                   help="has at least 10%% of this colour, e.g. red, 'dark blue', white (repeatable: all must match)")
    p.add_argument("--use", action="append", default=[], choices=list(measure.USES),
                   help="good for: " + "; ".join(f"{k}: {v[1]}" for k, v in measure.USES.items()))
    p.add_argument("--shape", action="append", default=[], choices=list(measure.SHAPES),
                   help="banner (2.5:1+), landscape, square, portrait (repeatable: any)")
    p.add_argument("--kind", action="append", default=[], help="catalog kind, e.g. maps, weaponSkins.chromas")
    p.add_argument("--variant", action="append", default=[], help="file variant, e.g. base, splash, wide")
    p.add_argument("--min-width", type=int, default=0)
    p.add_argument("--min-height", type=int, default=0)
    p.add_argument("--all-languages", action="store_true", help="also match names in other languages")
    p.add_argument("--sort", choices=list(index.SORTS), default="name",
                   help="order when no colour or use is given (those sort by best match / largest)")
    p.add_argument("--limit", type=int, default=20)
    p.add_argument("--content", help="content folder (default: the repo's content folder)")


def color_ids(names: list[str]) -> list[int]:
    by_name = {b.name.casefold(): b.id for b in colors.BINS}
    by_name.update({b.name.casefold().replace("grey", "gray"): b.id for b in colors.BINS})
    ids = []
    for name in names:
        key = name.strip().casefold()
        if key not in by_name:
            fail(f"unknown colour {name!r}. Colours: {', '.join(b.name for b in colors.BINS)}")
        ids.append(by_name[key])
    return ids


def filters_from(args, library: Library) -> index.Filters:
    folders = [f.name for f in library.folders]
    if args.folder:
        lower = {f.casefold(): f for f in folders}
        chosen = []
        for name in args.folder:
            if name.casefold() not in lower:
                fail(f"no folder {name!r}. Folders: {', '.join(folders)}")
            chosen.append(lower[name.casefold()])
        folders = chosen
    return index.Filters(
        folders=folders, q=" ".join(args.words), all_languages=args.all_languages,
        colors=color_ids(args.color), kinds=args.kind, variants=args.variant, uses=args.use,
        shapes=args.shape, min_width=args.min_width, min_height=args.min_height,
    )


def results(prep: Prepared, f: index.Filters, sort: str, limit: int) -> tuple[int, list[dict]]:
    """Matching images, described for an agent: what they are, how they look,
    what they're good for, and the full path to use."""
    with closing(index.connect(prep.db_path)) as conn:
        total, rows = index.list_images(conn, f, sort, 0, limit)
        out = []
        for rank, row in enumerate(rows, 1):
            out.append(describe(conn, prep, row["path"], rank))
    return total, out


def describe(conn: sqlite3.Connection, prep: Prepared, path: str, rank: int | None = None) -> dict:
    row = conn.execute(
        "SELECT i.path, i.folder, i.variant, i.width, i.height, i.size, e.name, e.kind, e.id, e.label, "
        "e.description FROM images i LEFT JOIN entries e ON e.key = i.entry_key WHERE i.path = ?",
        (path,)).fetchone()
    m = index.measures_of(conn, path)
    cols = [{"name": colors.BY_ID[b].name, "share": w} for b, w in conn.execute(
        "SELECT bin, weight FROM image_colors WHERE path = ? ORDER BY weight DESC LIMIT 4", (path,))]
    w, h = row["width"] or 0, row["height"] or 0
    item = {
        "rank": rank,
        "path": str(prep.library.root / row["path"]),
        "file": row["path"],
        "name": row["name"] or Path(row["path"]).stem,
        "id": row["id"] or row["label"],
        "kind": row["kind"],
        "variant": row["variant"] or "base",
        "folder": row["folder"],
        "description": row["description"] or None,
        "width": w, "height": h,
        "aspect": round(w / h, 2) if h else None,
        "bytes": row["size"],
        "colors": cols,
        "good_for": m["uses"] if m else [],
        "transparent": m["transparent"] if m else None,
        "brightness": m["brightness"] if m else None,
        "busyness": m["busyness"] if m else None,
        "best_on": m["best_on"] if m else None,
        "calm_side": m["calm_side"] if m else None,
        "text_color": m["text_color"] if m else None,
    }
    if rank is None:
        del item["rank"]
    return item


def human(item: dict) -> str:
    bits = [f"{item['width']}×{item['height']}"]
    if item["variant"] != "base":
        bits.append(item["variant"])
    if item["good_for"]:
        bits.append("good for " + ", ".join(item["good_for"]))
    if item["transparent"] is not None and item["transparent"] >= 0.15:
        bits.append(f"transparent bg{', best on ' + item['best_on'] + ' backgrounds' if item['best_on'] in ('dark', 'light') else ''}")
    if item["colors"]:
        bits.append(", ".join(f"{c['name'].lower()} {round(c['share'] * 100)}%" for c in item["colors"][:3]))
    kind = f" ({item['kind']})" if item["kind"] else ""
    return f"{item['rank']:>3}. {item['name']}{kind}: {'; '.join(bits)}\n     {item['path']}"


# -- commands -------------------------------------------------------------------------


def cmd_find(args) -> None:
    prep = prepare(args.content)
    total, items = results(prep, filters_from(args, prep.library), args.sort, args.limit)
    if args.json:
        print(json.dumps({"total": total, "shown": len(items), "images": items}, indent=2, ensure_ascii=False))
        return
    print(f"{total:,} match{'es' if total != 1 else ''}" + (f"; showing {len(items)}" if total > len(items) else ""))
    for item in items:
        print(human(item))


def cmd_sheet(args) -> None:
    prep = prepare(args.content)
    total, items = results(prep, filters_from(args, prep.library), args.sort, args.limit)
    if not items:
        fail("nothing matches, so there's no sheet to make")
    out = Path(args.out).expanduser().resolve()
    make_sheet(prep, items, out, cols=args.columns)
    if args.json:
        print(json.dumps({"sheet": str(out), "total": total, "images": items}, indent=2, ensure_ascii=False))
        return
    print(f"Contact sheet: {out}  ({len(items)} of {total:,} matches, numbered in this order)")
    for item in items:
        print(human(item))


CELL, CAPTION, PAD = 220, 40, 8


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def make_sheet(prep: Prepared, items: list[dict], out: Path, cols: int = 6) -> Path:
    cols = max(1, min(cols, len(items)))
    rows = -(-len(items) // cols)
    sheet = Image.new("RGB", (cols * (CELL + PAD) + PAD, rows * (CELL + CAPTION + PAD) + PAD), (40, 42, 48))
    draw = ImageDraw.Draw(sheet)
    small, badge_font = _font(12), _font(16)
    checker = _checker(CELL)
    with closing(index.connect(prep.db_path)) as conn:
        for n, item in enumerate(items):
            x = PAD + (n % cols) * (CELL + PAD)
            y = PAD + (n // cols) * (CELL + CAPTION + PAD)
            sheet.paste(checker, (x, y))
            row = index.image(conn, item["file"])
            source = prep.library.root / item["file"]
            if thumbs.needs_thumbnail(row["width"], row["height"]):
                source = thumbs.thumb_path(prep.thumbs_dir, item["file"], row["size"], row["mtime_ns"])
            try:
                with Image.open(source) as im:
                    im = im.convert("RGBA")
                    im.thumbnail((CELL - 8, CELL - 8))
                    sheet.paste(im, (x + (CELL - im.width) // 2, y + (CELL - im.height) // 2), im)
            except OSError:
                draw.text((x + 10, y + CELL // 2), "No preview", fill=(150, 150, 150), font=small)
            label = str(item["rank"])
            box = draw.textbbox((0, 0), label, font=badge_font)
            draw.rectangle((x, y, x + box[2] + 12, y + box[3] + 8), fill=(20, 20, 20))
            draw.text((x + 6, y + 3), label, fill=(255, 255, 255), font=badge_font)
            name = item["name"] if len(item["name"]) <= 30 else item["name"][:29] + "…"
            draw.text((x + 2, y + CELL + 4), name, fill=(235, 235, 235), font=small)
            # Plain "x": the built-in font has no "×".
            meta = f"{item['width']}x{item['height']}" + (f" · {item['variant']}" if item["variant"] != "base" else "")
            draw.text((x + 2, y + CELL + 20), meta, fill=(160, 165, 175), font=small)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    return out


def _checker(size: int, step: int = 12) -> Image.Image:
    # Mid grey, so both white silhouettes and dark art stand out.
    im = Image.new("RGB", (size, size), (118, 120, 126))
    d = ImageDraw.Draw(im)
    for yy in range(0, size, step):
        for xx in range(0, size, step):
            if (xx // step + yy // step) % 2:
                d.rectangle((xx, yy, xx + step - 1, yy + step - 1), fill=(136, 138, 144))
    return im


def resolve_image(prep: Prepared, spec: str, variant: str | None) -> str:
    """An image given as a path (relative to the content folder or absolute) or a catalog ID/label."""
    root = prep.library.root
    with closing(index.connect(prep.db_path)) as conn:
        candidate = Path(spec.strip().strip('"'))
        if candidate.is_absolute():
            try:
                rel = candidate.resolve().relative_to(root).as_posix()
            except ValueError:
                fail(f"{spec} is not inside the content folder {root}")
        else:
            rel = candidate.as_posix()
        if index.image(conn, rel):
            return rel
        key = spec.strip().upper()
        rows = conn.execute(
            "SELECT i.path, i.variant FROM images i JOIN entries e ON e.key = i.entry_key "
            "WHERE e.key = ? OR upper(e.label) = ? OR upper(e.id) = ? "
            "ORDER BY i.variant != '', i.variant, i.path", (key, key, key)).fetchall()
    if not rows:
        fail(f"no image found for {spec!r} (give a path inside the content folder, or a catalog ID)")
    if variant:
        wanted = "" if variant == index.BASE_VARIANT else variant.lower()
        for r in rows:
            if r["variant"] == wanted:
                return r["path"]
        fail(f"{spec} has no {variant!r} variant; it has: {', '.join(r['variant'] or 'base' for r in rows)}")
    return rows[0]["path"]


def parse_size(text: str) -> tuple[int, int]:
    try:
        w, h = (int(x) for x in text.lower().replace("×", "x").split("x"))
        assert w > 0 and h > 0
        return w, h
    except (ValueError, AssertionError):
        fail(f"--fit takes WIDTHxHEIGHT, like 1600x400 (got {text!r})")


def cmd_export(args) -> None:
    prep = prepare(args.content)
    rel = resolve_image(prep, args.image, args.variant)
    source = prep.library.root / rel
    out = Path(args.out).expanduser().resolve()
    if out == prep.library.root or prep.library.root in out.parents:
        fail("won't write inside the content folder; choose another --out")
    fmt = {".png": "PNG", ".webp": "WEBP", ".jpg": "JPEG", ".jpeg": "JPEG"}.get(out.suffix.lower())
    if not fmt:
        fail("--out must end in .png, .webp, .jpg or .jpeg")
    with Image.open(source) as im:
        im = ImageOps.exif_transpose(im).convert("RGBA")
    upscaled = False
    if args.fit:
        tw, th = parse_size(args.fit)
        im, upscaled = fit(im, tw, th, args.mode, args.focus)
    if fmt == "JPEG":
        flat = Image.new("RGB", im.size, args.background)
        flat.paste(im, mask=im.getchannel("A"))
        im = flat
    out.parent.mkdir(parents=True, exist_ok=True)
    im.save(out, fmt, **({"quality": 90} if fmt in ("JPEG", "WEBP") else {}))
    with closing(index.connect(prep.db_path)) as conn:
        info = describe(conn, prep, rel)
    result = {"out": str(out), "width": im.width, "height": im.height, "upscaled": upscaled, "source": info}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if upscaled:
        note("Note: the image was enlarged to fit, so it may look soft. Consider a larger image or a smaller --fit.")


def fit(im: Image.Image, tw: int, th: int, mode: str, focus: str) -> tuple[Image.Image, bool]:
    """Resize to exactly tw × th: 'cover' crops to fill, 'contain' pads with transparency."""
    w, h = im.size
    if mode == "contain":
        scale = min(tw / w, th / h)
        nw, nh = max(1, round(w * scale)), max(1, round(h * scale))
        canvas = Image.new("RGBA", (tw, th), (0, 0, 0, 0))
        canvas.paste(im.resize((nw, nh), Image.Resampling.LANCZOS), ((tw - nw) // 2, (th - nh) // 2))
        return canvas, scale > 1
    scale = max(tw / w, th / h)
    nw, nh = max(tw, round(w * scale)), max(th, round(h * scale))
    big = im.resize((nw, nh), Image.Resampling.LANCZOS)
    # Where to centre the crop: the visible subject by default.
    if focus == "auto":
        box = im.getchannel("A").point(lambda a: 255 if a >= 128 else 0).getbbox() or (0, 0, w, h)
        cx, cy = (box[0] + box[2]) / 2 * scale, (box[1] + box[3]) / 2 * scale
    else:
        cx = {"left": 0, "right": nw}.get(focus, nw / 2)
        cy = {"top": 0, "bottom": nh}.get(focus, nh / 2)
    left = int(min(max(cx - tw / 2, 0), nw - tw))
    top = int(min(max(cy - th / 2, 0), nh - th))
    return big.crop((left, top, left + tw, top + th)), scale > 1


def cmd_stats(args) -> None:
    prep = prepare(args.content)
    with closing(index.connect(prep.db_path)) as conn:
        rows = conn.execute(
            "SELECT i.folder, i.width, i.height, m.* FROM images i JOIN image_measures m ON m.path = i.path"
        ).fetchall()
        use_counts = {}
        for name, (_label, _desc, cond) in measure.USES.items():
            use_counts[name] = dict(conn.execute(
                f"SELECT i.folder, COUNT(*) FROM images i JOIN image_measures m ON m.path = i.path "
                f"WHERE {cond} GROUP BY i.folder").fetchall())
    by_folder: dict[str, list] = {}
    for r in rows:
        by_folder.setdefault(r["folder"], []).append(r)
    fields = ("transparent", "fill", "brightness", "busyness", "calm_busyness")
    print("Measurements per folder: 10th percentile / median / 90th percentile")
    print(f"{'Folder':<16}{'Images':>7}  " + "".join(f"{f:<20}" for f in fields))
    for folder in sorted(by_folder, key=str.casefold):
        rs = by_folder[folder]
        cells = []
        for f in fields:
            vals = sorted(r[f] for r in rs)
            q = lambda p: vals[min(len(vals) - 1, int(p * len(vals)))]  # noqa: E731
            cells.append(f"{q(.1):.2f}/{q(.5):.2f}/{q(.9):.2f}".ljust(20))
        print(f"{folder:<16}{len(rs):>7,}  " + "".join(cells))
    print()
    print("Good for, per folder:")
    print(f"{'Folder':<16}" + "".join(f"{u:>12}" for u in measure.USES))
    for folder in sorted(by_folder, key=str.casefold):
        print(f"{folder:<16}" + "".join(f"{use_counts[u].get(folder, 0):>12,}" for u in measure.USES))
    totals = {u: sum(c.values()) for u, c in use_counts.items()}
    print(f"{'Total':<16}" + "".join(f"{totals[u]:>12,}" for u in measure.USES))
    sizes = [max(r["width"] or 0, r["height"] or 0) for r in rows]
    if sizes:
        print(f"\nLongest side: median {statistics.median(sizes):,.0f} px; "
              f"{sum(s >= 1600 for s in sizes):,} images are at least 1600 px.")


def skill_path() -> Path:
    return Path.home() / ".claude" / "skills" / SKILL_NAME / "SKILL.md"


def install_skill() -> Path:
    """Copy the skill to ~/.claude/skills, with this repo's location filled in."""
    target = skill_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    text = SKILL_FILE.read_text(encoding="utf-8").replace("{{REPO}}", str(index.REPO_ROOT))
    target.write_text(text, encoding="utf-8")
    return target


def cmd_install_skill(_args) -> None:
    print(f"Installed the {SKILL_NAME} skill at {install_skill()}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="imagefind", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("find", help="list matching images")
    add_filter_args(p)
    p.add_argument("--json", action="store_true", help="structured output")
    p.set_defaults(func=cmd_find)

    p = sub.add_parser("sheet", help="save a numbered contact sheet of matching images")
    add_filter_args(p)
    p.set_defaults(limit=24)
    p.add_argument("--out", default="imagefind-sheet.png", help="where to save the sheet (PNG)")
    p.add_argument("--columns", type=int, default=6)
    p.add_argument("--json", action="store_true", help="structured output")
    p.set_defaults(func=cmd_sheet)

    p = sub.add_parser("export", help="copy an image out, optionally resized to fit")
    p.add_argument("image", help="path inside the content folder (as find prints it), or a catalog ID")
    p.add_argument("--out", required=True, help="output file: .png, .webp or .jpg")
    p.add_argument("--variant", help="with a catalog ID: which variant (base, splash, wide, …)")
    p.add_argument("--fit", help="WIDTHxHEIGHT, e.g. 1920x1080 for a slide or 1600x400 for a header")
    p.add_argument("--mode", choices=("cover", "contain"), default="cover",
                   help="cover: fill the size, cropping the overflow (default); contain: fit inside, padding")
    p.add_argument("--focus", choices=("auto", "center", "left", "right", "top", "bottom"), default="auto",
                   help="which part to keep when cropping; auto centres on the visible subject")
    p.add_argument("--background", default="white", help="colour behind transparency in a .jpg")
    p.add_argument("--content", help="content folder (default: the repo's content folder)")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("stats", help="how the images measure, per folder")
    p.add_argument("--content", help="content folder (default: the repo's content folder)")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("install-skill", help="install the image-assets skill for Claude Code")
    p.set_defaults(func=cmd_install_skill)
    return parser


def main(argv: list[str]) -> None:
    # Piped output on Windows can use a legacy code page; never crash on a
    # character it lacks.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    args = build_parser().parse_args(argv)
    if getattr(args, "limit", 1) < 1:
        fail("--limit must be at least 1")
    try:
        args.func(args)
    except ValueError as exc:
        fail(str(exc))

