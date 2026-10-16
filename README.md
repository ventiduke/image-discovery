# Image Discovery

A local tool for browsing and searching the images in `content/`: pick folders, scroll through them as a grid, and search by the metadata in the catalog JSON. It only reads the content folder; it never changes it.

See [docs/PLAN.md](docs/PLAN.md) for the full design.

## Status

| Milestone | State |
| --- | --- |
| 1. Skeleton (open a content folder, list its folders) | Done |
| 2. Catalog and scan (match images to catalog entries) | Done |
| 3. Grid browsing | Done |
| 4. Search, filters, colors, details panel | Done |
| 5. Relationships (collections, "show all" links) | Partly done (details panel shows parents, contents and links) |
| 6. Full dump and refresh | Not started |
| Agent access (find, sheet, export, "good for") | Done; rules tuned on the full drop |

## Running it

You need [uv](https://docs.astral.sh/uv/). It installs a suitable Python for you if you don't have one.

- **Windows (PowerShell):** `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **macOS:** `curl -LsSf https://astral.sh/uv/install.sh | sh`

Then, from this folder:

```
uv run imagefind
```

A browser tab opens on the repo's `content` folder. Use **Change folder…** to open a different content folder.

## Using it

**Browse.** Tick folders on the left and scroll through their images. Each tile shows the item's English name from the catalog, its variant (`splash`, `killstream`, …) and, when more than one folder is ticked, its folder. Sort by name, folder or file size, and use the **Size** slider to make the tiles bigger or smaller.

**Search.** Type in the search box: it matches names, descriptions and IDs, and the names of related items, so "Gekko" also finds his abilities and "Vandal" every Vandal skin. Every word must match, and partial words count ("reav vand"). Tick **All languages** to also match names in the other 17 languages.

**Filter.** In the sidebar:

- **Colors**: each image's main colors, worked out in the background after indexing. Click a swatch (the number is how many images have that color) to show images with at least 10% of it, best matches first. Pick several to need all of them.
- **Good for**: what each image suits, worked out from its size, shape, transparency and amount of detail. **Slide background** (opaque, landscape, at least 1600 px wide, not too busy), **Section header** (a wide banner such as a player card's wide variant, or a big landscape image with a calm side for the title), **Tall panel** (opaque and tall, such as a player card's large variant), **Icon / spot art** (transparent, squarish) and **Cut-out** (transparent and large). Hover one for its rule.
- **Shape**: wide banner, landscape, square-ish or portrait.
- **Kind** and **Variant**: the kind of catalog item (weapon skins › chromas, player cards, …) and the file variant.

The counts follow the other filters, and the filters in use show above the grid, where you can remove them one at a time or all at once. The address bar keeps everything, so you can bookmark a view.

**Details.** Click a tile to open its details: a large preview, its other variants, its colors (click one to filter by it), what it's good for (with the measurements behind it: how transparent, how detailed, which backgrounds it shows up on, and which side has room for a title in white or black text), and everything the catalog says about it. Fields that point at another item, like a skin's weapon, are links; the panel also shows what the item belongs to, what it contains and what links to it (a weapon's skins). Use ← and → to step through the grid and Esc to close. **Copy path** and **Copy ID** copy to the clipboard; Ctrl+click a tile to open the full image in a new tab instead.

**Match report.** The **Match report** tab shows how the files match the catalog (per folder, which catalog kinds and variants it holds, and any files that match nothing) and **what the catalog holds**: for each kind of item, how often it has a description, names in other languages, and each other field, with example values.

The first time a folder is opened, the tool indexes it: it reads the catalog and every image's size, and matches each file to its catalog entry. For the full dump this takes a few seconds; after that only new or changed files are re-read. It then makes small thumbnails of the large images and works out every image's colors in the background, which takes a minute or two the first time (browsing works meanwhile; the color filter covers more images as it goes) and only covers new or changed images after that. The index and thumbnails are kept in the git-ignored `.cache` folder (for folders outside the repo, in your user cache folder), never in the content folder.

**Stopping it.** Click **Exit**, or just close the browser tab: the tool notices when none of its tabs are open any more and stops by itself about 15 seconds later. Ctrl+C in the terminal also works.

Options:

```
uv run imagefind path\to\content   # open a different content folder
uv run imagefind --port 9000       # default port is 8766 (a free one is used if it's taken)
uv run imagefind --no-browser      # don't open a browser tab
uv run imagefind --rebuild         # rebuild the index from scratch
uv run imagefind --report          # print how the images match the catalog, then exit
```

**Checking a new content drop.** `uv run imagefind --report` prints, for each folder, how many files matched a catalog entry, which variants (`splash`, `killstream`, …) it found, and the files that match nothing. The page shows the same report.

## For AI agents

An agent (Claude Code, for example) can find and export images for slides and reports from the command line, without the page:

```
uv run imagefind find ascent --use header            # list candidates (add --json for structured output)
uv run imagefind sheet ascent --use header --out c.png   # the same as one numbered contact sheet to look at
uv run imagefind export Maps/<file>.png --out header.png --fit 1600x400   # copy out, cropped to size
uv run imagefind stats                               # how the images measure, per folder
```

`find` and `sheet` take the same filters as the page: search words, `--use background|header|panel|icon|cutout`, `--color` (by name, e.g. `red`, `"dark blue"`), `--shape`, `--folder`, `--kind`, `--variant`, `--min-width`, `--min-height`. Each result gives the full path, catalog name, size, main colors, what it's good for, and hints such as "best on dark backgrounds" (many icons are white silhouettes) or which side has room for a title. `export` copies an image out (never into the content folder), optionally resized to an exact size: `--mode cover` crops to fill, `--mode contain` pads with transparency.

The first command after a new content drop prepares every image (thumbnails, colors, measurements), which takes a minute or two; after that, commands take about a second.

**Teach Claude Code to use it** by installing the `image-assets` skill once:

```
uv run imagefind install-skill
```

It goes to `~/.claude/skills/image-assets/`, with this folder's location filled in, so Claude Code can use the library from any project: ask it for "a section header image for the Ascent slide" and it searches, looks at a contact sheet, and exports the one it picks. Run it again after moving this folder or updating the tool.

**Tuning.** The "good for" rules were tuned on the full drop (most images are small: half are 128 px or less, and only about 80 reach 1600 px). `uv run imagefind stats` prints how each folder measures and how many images each rule picks; the rules live in `imagefind/measure.py`.

## The content folder

```
content/
  PublicContentCatalog.json   metadata for every item
  Abilities/                  image folders, one per kind of item
  Maps/
  Weapons/
  ...
```

Images are named after the catalog ID they show, optionally with a variant: `<id>.png`, `<id>_splash.png`. Items without an ID, such as abilities, are named after their label instead (`<character id>_Ability1.png`). Matching ignores case, and tries the whole filename before splitting off a variant, so `<character id>_Ability1` is the ability rather than an `Ability1` picture of the character. The page warns about anything unexpected (no catalog file, images sitting at the top level instead of in a folder, files that match no catalog entry).

## Development

```
uv run pytest
```
