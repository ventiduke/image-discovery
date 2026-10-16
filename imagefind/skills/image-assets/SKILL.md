---
name: image-assets
description: Find and export images from the team's catalogued image library (game art such as maps, agents, abilities, weapons, weapon skins, player cards, sprays, buddies) for use in slides, HTML reports and other documents, for example a section header, a slide background, an icon or a cut-out. Use whenever a document you're making needs a picture of something from that library, or the user asks for a fitting image, header, background or icon.
---

# Finding images in the image library

The library is a folder of about 11,000 images, each matched to an entry in a catalog (name, kind, description and other fields). The `imagefind` tool searches it and exports images ready to use. Run it with:

```
uv run --project "{{REPO}}" imagefind <command> ...
```

(Below, `imagefind` is short for that.) Every command brings its index up to date first. After a new content drop the first command takes a minute or two (progress goes to stderr); after that, commands take a second or so.

## The usual workflow

1. **Find candidates.** Search by words and filter by what the image is for:

   ```
   imagefind find ascent --use header
   imagefind find --folder Maps --use background --color blue
   imagefind find gekko --use icon --json
   ```

2. **Look before choosing.** Names say what an item is, not what the picture looks like. Make a contact sheet of the candidates and view it:

   ```
   imagefind sheet ascent --use header --out /tmp/candidates.png
   ```

   It saves one image with up to 24 numbered thumbnails (on a grey checkerboard, so transparency shows) and prints the same numbered list with each file's path. Open the sheet and pick by number.

3. **Export what you chose**, sized for where it goes. Never use files straight from the library folder in a deliverable; export a copy:

   ```
   imagefind export "Maps/<file>.png" --out ./assets/header.png --fit 1600x400
   ```

   `export` prints JSON with the output path and size. If it says `"upscaled": true`, the image was enlarged and may look soft: prefer a larger source or a smaller `--fit`.

## Filters (find and sheet)

| Option | What it does |
| --- | --- |
| words | Every word must match, as a prefix, in the name, description, ID or a related item's name ("gekko" also finds his abilities, "vandal" every Vandal skin). |
| `--use background` | Opaque, landscape (about 4:3 to 21:9), at least 1600 px wide, not too busy. For full-slide or hero backgrounds. |
| `--use header` | A wide banner, or a large opaque landscape image with a calm side for the title, to crop into a strip. For section headers. |
| `--use icon` | Transparent background, roughly square, subject filling the frame. For bullets, cards, small spot art. |
| `--use cutout` | Transparent background, large. A subject to place on a slide. |
| `--shape` | `banner` (2.5:1 or wider), `landscape`, `square`, `portrait`. |
| `--color` | At least 10% of this colour; repeat to need several. Names: black, dark grey, light grey, white, and red, orange, yellow, lime, green, teal, cyan, sky, blue, purple, magenta, pink, each also "dark …" (dark orange is "brown", dark yellow "olive"). |
| `--folder` | Abilities, Armors, CharacterRoles, Characters, Charms, Flexes, GameModes, Maps, PlayerCards, Sprays, Weapons, WeaponSkins. |
| `--kind`, `--variant` | Catalog kind (e.g. `maps`, `weaponSkins.chromas`) and file variant (e.g. `base`, `splash`, `listview`, `wide`, `large`, `killstream`). |
| `--min-width`, `--min-height` | Size in pixels. |
| `--limit` | How many to list (default 20; 24 for sheets). |

With `--color`, the best colour matches come first; with `--use`, the largest images come first.

## Reading the results

Each result (use `--json` for all fields) gives the full `path`, the catalog `name`, `kind` and `variant`, `width` and `height`, its main `colors`, and:

- `good_for`: which of the uses above it fits.
- `transparent`: share of transparent pixels. Above about 0.15 it's a cut-out or icon.
- `best_on`: for transparent images, the page background it shows up on. **Many icons are white silhouettes (`best_on: dark`) that vanish on a white slide.** Put those on a dark band, or pick another image.
- `calm_side` and `text_color`: for opaque images, the calmest third (left, center or right) and whether white or black text reads there. Put a title on that side.
- `busyness`: fine detail, 0 to 1. Lower is calmer and better behind text.

## Export options

| Option | What it does |
| --- | --- |
| `IMAGE` | A path as `find` prints it (absolute or relative to the library), or a catalog ID with `--variant`. |
| `--out` | `.png` (keeps transparency), `.webp`, or `.jpg` (transparency filled with `--background`, white by default). Must be outside the library folder. |
| `--fit WxH` | Exact output size. `--mode cover` (default) fills it, cropping the overflow; `--mode contain` fits inside it, padding with transparency. |
| `--focus` | What to keep when cropping: `auto` (default, centres on the visible subject), `center`, `left`, `right`, `top`, `bottom`. Use the `calm_side`'s opposite to keep the subject and leave the calm side for text. |

Typical sizes: slide background `1920x1080`, section header `1600x400` (HTML) or `1920x480`, icon `256x256` with `--mode contain`.

## Good habits

- Start broad (`find <topic>`), then narrow with `--use` and `--color` to match the document's palette.
- Always look at a sheet before committing to an image.
- Tell the user which image you used (name and path), so they can swap it.
- If nothing fits, say so and show the closest candidates rather than forcing a poor match.
