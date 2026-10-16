# Image Discovery: Overview

Oct 2026. A complete description of what the tool is, how to use it, how it works, and where it's headed. For the original build plan and its decisions, see [PLAN.md](PLAN.md); for day-to-day usage, the [README](../README.md).

## Contents

1. [What it is](#1-what-it-is)
2. [Status at a glance](#2-status-at-a-glance)
3. [Quick start](#3-quick-start)
4. [The content](#4-the-content)
5. [Using the app](#5-using-the-app)
6. [Using it from an AI agent](#6-using-it-from-an-ai-agent)
7. [How it works](#7-how-it-works)
8. [What the full drop looks like](#8-what-the-full-drop-looks-like)
9. [Running it day to day](#9-running-it-day-to-day)
10. [Code map](#10-code-map)
11. [Testing](#11-testing)
12. [Decisions and history](#12-decisions-and-history)
13. [Limitations and next steps](#13-limitations-and-next-steps)

---

## 1. What it is

A local tool for finding images in a large, catalogued image library: about 11,000 game-art images (maps, agents, abilities, weapons, weapon skins, player cards, sprays, buddies and more) plus a 15 MB catalog that names and describes each one.

The images are named by catalog ID (`0AFB2636-….png`), so on their own they're impossible to browse. The tool joins each file to its catalog entry and makes the library searchable two ways:

- **For people:** a browser page with a thumbnail grid, search, filters (folder, color, what the image is good for, shape, kind, variant) and a details panel showing everything the catalog knows about an image.
- **For AI agents:** command-line tools that find images matching criteria, show candidates as a numbered contact sheet, and export the chosen one at the right size, so an agent building a slide or HTML report can pick a fitting header, background, icon or cut-out.

It runs entirely on your machine (`uv run imagefind`), only reads the content folder, and keeps everything it generates in a git-ignored cache.

## 2. Status at a glance

| Area | State |
| --- | --- |
| Index the content folder and match every image to the catalog | Done; all 11,262 files in the full drop match |
| Thumbnail grid with infinite scroll | Done |
| Search (names, descriptions, IDs, related items' names; optional all languages) | Done |
| Color filter (28 colors) | Done |
| "Good for" labels (background, header, tall panel, icon, cut-out) and shape filter | Done; tuned on the full drop |
| Kind and variant filters | Done |
| Details panel (preview, variants, colors, measurements, all catalog fields, parent, contents, links) | Done |
| Match report and "what the catalog holds" | Done |
| Agent commands: `find`, `sheet`, `export`, `stats`, `install-skill` | Done |
| Claude Code skill (`image-assets`) | Written; not installed yet (by choice) |
| Collections inferred from names, "show all" links from the details panel | Not started |
| Monthly refresh helper and "new in this drop" | Not started |
| Tags from item names | Built, then removed: searching a word does the same job |

115 automated tests pass. The page has been checked in light and dark mode and at phone width.

## 3. Quick start

You need [uv](https://docs.astral.sh/uv/), which installs Python for you.

```powershell
# Windows, once:
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# From the repo folder:
uv run imagefind                 # open the app in the browser
uv run imagefind --report        # check how the content matches the catalog
uv run imagefind stats           # how the images measure, per folder
uv run imagefind find ascent     # search from the command line
```

The first run after a new content drop indexes everything (seconds), then makes thumbnails and measures every image in the background (a minute or two). After that, opening the app or running a command takes about a second.

## 4. The content

### Layout

```
content/
  PublicContentCatalog.json   the catalog: names, descriptions and fields for every item
  Abilities/  Armors/  CharacterRoles/  Characters/  Charms/  Flexes/
  GameModes/  Maps/  PlayerCards/  Sprays/  Weapons/  WeaponSkins/
```

The full drop lives in the repo's `content/` folder and is replaced about once a month.

### The catalog

`PublicContentCatalog.json` has a `version` and 14 sections (`characters`, `weapons`, `weaponSkins`, `maps`, `playerCards`, …). Some items contain others: a weapon skin holds its chromas and levels, a character holds its abilities. Flattened, that's **12,483 entries**. Every entry has an English name (plus up to 17 other languages); some have a description and other fields, such as a weapon's `category`, a skin's `weaponId`, or a player title's `titleText`. The **Match report → What the catalog holds** table shows, for each kind, how often each field is filled in.

### File names

Each image is named after the catalog entry it shows, optionally with a variant:

| Pattern | Example | Means |
| --- | --- | --- |
| `<id>.png` | `1C18AB1F-….png` | the base image |
| `<id>_<variant>.png` | `1C18AB1F-…_listview.png` | a variant: `splash`, `listview`, `killstream`, `wide`, `large`, `small`, `displayicon`, `fullportrait`, … |
| `<label>.png` | `E370FA57-…_Ability1.png` | abilities have no ID, only a label of the form `<character id>_Ability1` |

Matching ignores case and tries the whole name first, so `<character id>_Ability1` is the ability rather than an "Ability1" variant of the character.

### Folders in the full drop

| Folder | Files | What's in it |
| --- | --- | --- |
| Abilities | 118 | ability icons, white silhouettes |
| Armors | 3 | armor icons |
| CharacterRoles | 4 | role icons |
| Characters | 89 | agents: icons and full portraits (cut-outs) |
| Charms | 898 | buddies, in padded frames |
| Flexes | 23 | flex items |
| GameModes | 16 | game mode icons, mostly white |
| Maps | 72 | map art: minimap, list-view banner, 1920×1080 splash |
| PlayerCards | 4,058 | each card in small (square), wide (452×128) and large (268×640) |
| Sprays | 921 | sprays |
| Weapons | 70 | weapon images and kill-feed silhouettes |
| WeaponSkins | 4,990 | skins, chromas and levels |

Catalog kinds with no images (player titles, acts, matchmaking queues, charm and spray levels) are expected: they're text only, or their images are filed under the parent item.

## 5. Using the app

Run `uv run imagefind`; a browser tab opens on `content/`. **Change folder…** opens another content folder. The tool stops by itself about 15 seconds after you close its last tab, or use **Exit**.

### Images view

- **Grid.** Thumbnail tiles with the item's English name, variant and size (or folder, when several folders are ticked). Scroll to load more. **Sort** by name, folder or file size; **Size** makes tiles bigger or smaller. Tiles sit on a mid-grey checkerboard so transparent areas show, and white silhouettes stay visible.
- **Search.** Matches names, descriptions, IDs and related items' names, as prefixes, with every word required: "gekko" finds Gekko and his abilities, "vandal" every Vandal skin, "reav vand" the Reaver Vandal. **All languages** also matches the other 17 languages.
- **Filters** in the sidebar, each with counts that follow the other filters:
  - **Folders.**
  - **Colors:** 28 swatches; picking one shows images with at least 10% of that color, best matches first; picking several requires all of them.
  - **Good for:** slide background, section header, tall panel, icon / spot art, cut-out (rules in [section 7](#measurements-and-good-for-rules); hover a label for its rule).
  - **Shape:** wide banner, landscape, square-ish, portrait.
  - **Kind** (catalog kind, e.g. *Weapon skins › chromas*) and **Variant**.
- **Active filters** show above the grid, each removable, with **Clear all**. The address bar holds the whole view (folders, search, filters, sort), so views can be bookmarked or shared.

### Details panel

Click a tile (Ctrl+click opens the full image in a new tab instead). The panel shows:

- a large preview, **Open full image**, **Copy path** and **Copy ID**;
- the item's other **variants**;
- its **colors**, as a bar and list (click a color to filter by it);
- **Good for**, with the measurements behind it: how transparent, how much of the frame the subject fills, brightness, amount of detail, which backgrounds it shows up on, and (for opaque images) which side has room for a title and in which text color;
- **Details**: description, every catalog field, with fields that point at another item (a skin's `weaponId`) shown as links;
- **Part of** (a chroma's skin, an ability's character), **Contains** (a skin's chromas and levels) and **Linked from** (a weapon's skins);
- names in other languages and the raw catalog JSON.

← and → step through the grid; Esc closes.

### Match report view

- How each folder's files match the catalog: matched and unmatched counts, variants found, and catalog kinds.
- Catalog coverage: for each kind, how many entries have at least one image.
- Files that match no catalog entry, if any.
- **What the catalog holds:** per kind, how often there's a description, other-language names, and each other field, with example values on hover.

## 6. Using it from an AI agent

The aim: an agent making a slide deck or HTML report can find a fitting image (a section header, a background, an icon) without a person browsing for it.

### Commands

Run from the repo folder (or anywhere with `uv run --project <repo> imagefind …`):

| Command | What it does |
| --- | --- |
| `imagefind find [words] [filters]` | Lists matching images: full path, catalog name and kind, size, main colors, what it's good for, and hints. `--json` for structured output. |
| `imagefind sheet [words] [filters] --out FILE.png` | The same results as one image with up to 24 numbered thumbnails, so a multimodal agent can *look* before choosing. Prints the numbered list too. |
| `imagefind export IMAGE --out FILE [--fit WxH]` | Copies an image out, optionally to an exact size: `--mode cover` crops to fill (centred on the subject, or `--focus left/right/…`), `--mode contain` pads with transparency. `.png`, `.webp` or `.jpg`. Warns when it had to enlarge the image. Refuses to write inside the content folder. |
| `imagefind stats` | Measurements per folder (10th percentile, median, 90th) and how many images each "good for" rule picks. For tuning. |
| `imagefind install-skill` | Installs the `image-assets` skill for Claude Code (see below). |

Filters for `find` and `sheet`: search words, `--use background|header|panel|icon|cutout`, `--color NAME` (e.g. `red`, `"dark blue"`), `--shape banner|landscape|square|portrait`, `--folder`, `--kind`, `--variant`, `--min-width`, `--min-height`, `--all-languages`, `--limit`.

Each result includes hints an agent needs to use the image well:

- `best_on: dark`: a transparent image that's mostly white (most icons), which vanishes on a white slide.
- `calm_side` and `text_color`: where a title fits on an opaque image, and whether white or black text reads there.
- `good_for`, `transparent`, `brightness`, `busyness`, `colors`, `width`, `height`.

### Typical agent workflow

1. `find <topic> --use header` (or `background`, `icon`, …), optionally `--color` to match the document's palette.
2. `sheet` the candidates and look at them; names say what an item is, not what the picture looks like.
3. `export` the chosen one at the size it'll be shown, e.g. `--fit 900x255` for an HTML header or `--fit 1920x1080` for a slide.
4. Tell the user which image it used.

### The `image-assets` skill

[`imagefind/skills/image-assets/SKILL.md`](../imagefind/skills/image-assets/SKILL.md) teaches Claude Code the workflow above, the filters, how to read results, and where good candidates live in this library. `uv run imagefind install-skill` copies it to `~/.claude/skills/image-assets/` with the repo's location filled in. It isn't installed yet; to try it without installing, ask Claude Code to read that file and follow it.

### Where good candidates are

| Need | Best source | Notes |
| --- | --- | --- |
| Full-slide background | Map splashes, 1920×1080 (about 30) | Little else in the library is that large. |
| Section header | Player cards' wide variant, 452×128 (about 1,000) | Sharp up to about 450 px wide; soft if stretched across a slide. Map splashes cropped to a strip also work. |
| Side image on a slide | Player cards' large variant, 268×640 | "Tall panel". |
| Big cut-out | Character full portraits, weapon skins | |
| Icons | Abilities, weapons, game modes, roles (white silhouettes: need a dark background); buddies and sprays (in color) | |

## 7. How it works

### Architecture

```
            ┌─────────────── browser page (static/index.html, app.js, app.css)
            │                        │  JSON API, thumbnails, files
            │                        ▼
uv run imagefind ──► server.py (FastAPI on 127.0.0.1) ──┐
                                                        │
uv run imagefind find|sheet|export|stats ──► cli.py ────┤
                                                        ▼
                  library.py   open the content folder, list folders
                  catalog.py   flatten the catalog JSON into entries
                  scan.py      walk folders, read image sizes, match names to entries
                  index.py     SQLite index: entries, images, search, filters, details
                  analysis.py  background work: thumbs.py, colors.py, measure.py
                                                        │
                                                        ▼
                  .cache/<content>-<hash>/index.sqlite + thumbs/   (git-ignored)
```

The page and the command line share the same index and the same background work, so either can bring the cache up to date and the other benefits.

### Indexing

On open (or before any command):

1. **Catalog.** If the catalog file changed (size or modification time), it's re-read and flattened: one entry per item with an ID or label, including nested chromas, levels and abilities, keyed by ID or (for abilities) label. Each entry keeps its English name, other-language names, description, simple fields, parent, and raw JSON.
2. **Files.** Every image under the content folder's sub-folders is listed. Image sizes are read from file headers (PNG, JPEG, GIF, WebP), and only for new or changed files.
3. **Matching.** Each file name is matched to an entry, case-insensitively, trying the whole name first and then shorter prefixes split at underscores (the rest being the variant).
4. **Storing.** Everything is written to SQLite. Files that changed lose their stored colors and measurements, so they're redone.

The first index of the full drop takes a few seconds; later ones well under a second.

### Search

An SQLite FTS5 table holds, for each entry: the English name, description, other-language names, ID and label, and a **context** column with the names of related items (the parent chain, items its fields point at such as a skin's weapon, and plain field values such as "Rifle"). Every word of a query must match as a prefix. File paths are also searched, so raw IDs and unmatched files are findable.

### Background work: thumbnails, colors and measurements

After indexing, a pool of daemon threads works through any image missing a thumbnail, colors or measurements. Each large image is decoded once: a 320 px WebP thumbnail is made, and colors and measurements are read from it (small images are read as they are). Results are written in batches. The page keeps working meanwhile; the command line waits, showing progress on stderr. Quitting never waits for this work.

### Colors

Each visible pixel (transparent ones skipped) of a 48 px nearest-neighbour sample goes into one of 28 bins: black, dark grey, light grey, white, and 12 hues (red, orange, yellow, lime, green, teal, cyan, sky, blue, purple, magenta, pink) each bright or dark (dark orange is "brown", dark yellow "olive"). Shares under 3% are dropped. A color filter needs at least 10% of each chosen color and ranks by the total share.

### Measurements and "good for" rules

Measured per image from the thumbnail:

| Measurement | Meaning |
| --- | --- |
| `transparent` | share of mostly transparent pixels |
| `fill` | how much of the frame the visible subject's bounding box covers |
| `brightness`, `contrast` | mean and spread of the visible pixels' luminance, 0 to 1 |
| `busyness` | mean edge strength (fine detail), 0 to 1 |
| `calm_side`, `calm_busyness`, `calm_brightness` | the calmest third (left, center, right), its detail and brightness |

Derived hints: `best_on` (dark, light or any background, for transparent images) and `text_color` (white or black, for opaque images).

The rules ([`measure.py`](../imagefind/measure.py)) are SQL conditions applied at query time, so changing one never needs re-measuring:

| Use | Rule |
| --- | --- |
| Slide background | transparent < 2%, at least 1600 px wide, aspect 1.3 to 2.4, busyness ≤ 0.14 |
| Section header | transparent < 30%, and either a banner (2.5:1 or wider, at least 400 px) or a large landscape image (at least 1600 px, 1.3:1 or wider) with a calm third (≤ 0.10) |
| Tall panel | transparent < 2%, at least 1.8 times as high as wide, at least 500 px high |
| Icon / spot art | transparent ≥ 15%, aspect 0.75 to 1.34, subject fills ≥ 35%, at least 128 px |
| Cut-out | transparent ≥ 15%, at least 512 px long side and 256 px short side |

Shapes: banner (2.5:1 or wider), landscape (1.2 to 2.5), square-ish (0.8 to 1.2), portrait (0.8 or less).

### The local server

FastAPI on `127.0.0.1`, port 8766 (or a free one). Requests must come from the tool's own host name (blocking DNS-rebinding), and API calls carry a per-run token embedded in the page. Thumbnail and file URLs only serve paths the index knows about, so nothing else on disk can be read. Image URLs include the file's size and modification time, so the browser caches them for good and a changed file gets a new URL.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/library`, `POST /api/open`, `POST /api/pick` | the open content folder; open another (by path, or the native folder dialog) |
| `GET /api/status` | indexing progress, the match report, background progress |
| `GET /api/images` | one page of grid results for the given filters |
| `GET /api/facets` | counts for the filter controls |
| `GET /api/image` | everything for the details panel |
| `GET /api/catalog` | "what the catalog holds" |
| `GET /thumb/…`, `GET /file/…` | thumbnail, original image |
| `POST /api/presence`, `POST /api/shutdown` | tab tracking for auto-exit; the Exit button |

### Cache

For the repo's own `content/`, the cache is `.cache/content-<hash>/` in the repo (git-ignored); for any other folder, the user's cache directory (`%LOCALAPPDATA%\imagefind` on Windows). It holds `index.sqlite` and `thumbs/`. Deleting it is always safe: it's rebuilt on the next run.

## 8. What the full drop looks like

From `imagefind stats` on the full drop (11,262 images):

- **Most images are small.** Half are 128 px or less on the long side; only about 80 are 1600 px or more.
- **Transparency:** abilities, armors, roles, game modes, weapons and charms are mostly transparent (median 57% to 79%); player cards are fully opaque; maps and sprays are mostly opaque; weapon skins and characters are mixed.
- **Brightness:** abilities, armors, roles, game modes and weapons have median brightness 0.96 to 1.00. They're white silhouettes, which is why `best_on: dark` matters.
- **Charms** fill only about half their frame (padding), hence the icon rule's lower fill threshold.

"Good for" counts before the last tuning: background 29, header 29, icon 959, cut-out 801. The tuning then added about 1,000 player-card banners as headers, the player cards' large variant as tall panels, and more charms as icons. Re-run `imagefind stats` for current numbers.

## 9. Running it day to day

### A new content drop (monthly)

1. Replace the contents of `content/` with the new drop (delete the old files first, so removed images don't linger).
2. `uv run imagefind --report`: check that everything matches. Unmatched files are listed; send a few names if the naming has changed.
3. `uv run imagefind stats`: the first run prepares every image (a minute or two); check the "good for" counts look reasonable.
4. Commit and push with git or GitHub Desktop (the GitHub upload page can't handle 11,000 files). Git stores unchanged images once, so only new or changed images grow the repo.

### Useful options

```
uv run imagefind path\to\content   # open another content folder
uv run imagefind --port 9000       # another port (default 8766)
uv run imagefind --no-browser      # don't open a tab
uv run imagefind --rebuild         # rebuild the index from scratch
```

### Troubleshooting

| Symptom | Fix |
| --- | --- |
| The folder dialog doesn't appear | It may be behind other windows (check the taskbar), or paste the path instead. |
| Colors or "good for" counts are low and growing | Background work is still running; the page shows its progress. |
| Something looks stale or wrong after a big change | `uv run imagefind --rebuild`, or delete the `.cache` folder. |
| A command seems slow the first time | It's preparing images after a new drop; later runs take about a second. |

## 10. Code map

| File | Role |
| --- | --- |
| `imagefind/__main__.py` | Entry point: starts the app, or hands `find`/`sheet`/`export`/`stats`/`install-skill` to `cli.py`; `--report` |
| `imagefind/server.py` | Local web server: page, JSON API, thumbnails and files, background indexing |
| `imagefind/cli.py` | Agent commands, contact sheets, export with fit and crop, stats, skill install |
| `imagefind/library.py` | Open a content folder: catalog file, folders, image counts, warnings |
| `imagefind/catalog.py` | Flatten the catalog into entries keyed by ID or label |
| `imagefind/scan.py` | Walk image folders, read sizes from headers, match file names to entries |
| `imagefind/index.py` | SQLite schema and incremental build; search, filters, counts, details, report, catalog overview |
| `imagefind/analysis.py` | Background thumbnails, colors and measurements, shared by page and CLI |
| `imagefind/thumbs.py` | Thumbnail making and the background worker pool |
| `imagefind/colors.py` | The 28 color bins and per-image color shares |
| `imagefind/measure.py` | Image measurements, "good for" rules and shapes |
| `imagefind/picker.py` | Native folder dialog (from local-html-edit) |
| `imagefind/presence.py` | Stop when the last tab closes (from local-html-edit) |
| `imagefind/static/` | The page: `index.html`, `app.js`, `app.css`, `keepalive.js` |
| `imagefind/skills/image-assets/SKILL.md` | The Claude Code skill |
| `tests/` | Automated tests, one file per module |
| `docs/PLAN.md` | Build plan, milestones and decisions |

## 11. Testing

```
uv run pytest
```

115 tests cover catalog flattening, file matching (including ability labels), header parsing for each image format, incremental indexing, search, every filter and its counts, colors, measurements and rules, the details panel data, the server's API and its security checks (host, token, path guard), background work, and every command-line command including export cropping and refusals. Tests use small generated libraries plus the real sample images in `content/`.

Beyond the tests, each change was checked in a real browser (light, dark, phone width) and against a synthetic 10,453-image library built from the real catalog, then on the real full drop on Windows.

## 12. Decisions and history

| Decision | Why |
| --- | --- |
| Local Python tool run with `uv`, browser UI, like local-html-edit | One command, no install steps, works on Windows and macOS; reuses proven pieces (folder dialog, auto-exit, security). |
| SQLite (with FTS5) index in a cache folder | Fast start after the first run, real search, simple filters; nothing written to the content folder. |
| Match by ID *or label*, whole name first | Abilities have no ID; this keeps `<character>_Ability1` from being read as a variant. |
| Full drop lives in the repo, replaced monthly | Requested; git stores unchanged images once. |
| English first; other languages opt-in | Requested. |
| Tags from item names: built, then removed | Search for a word does the same, with less clutter. |
| Color filter with 28 bins, "all colors" for several | Requested; simple, predictable, and fast to compute. |
| "Good for" rules as SQL at query time | Tuning a rule needs no re-measuring. Rules were tuned on the real drop's `stats`. |
| Contact sheets for agents | Names say what an item is, not what it looks like; multimodal agents can look. |
| Mid-grey checkerboard | Many icons are white silhouettes that vanished on a light background. |
| Usage rights not tracked | Not needed, per the team. |

Milestones, in order: skeleton (open folder, list folders) → catalog and matching → thumbnail grid → search, colors, filters and details panel (expanded on request) → tags removed → agent commands and "good for" labels → rules tuned on the full drop.

## 13. Limitations and next steps

**Limitations**

- Search is over catalog text only: the tool doesn't know what's *in* a picture beyond its name, colors and measurements. An image described only by an ID-like name is findable by color and shape, not by content.
- The "good for" labels are rules of thumb. They find candidates; looking (the contact sheet, or the grid) still decides.
- Most images are small, so full-slide use is limited to the map splashes; `export` warns when it enlarges.
- The color bins are coarse (12 hues × 2 shades + 4 neutrals), and the 10% threshold is a single global setting.

**Possible next steps**

- **Monthly refresh helper:** a command that replaces `content/` from a new drop, checks it, and summarises what's new, changed and removed; plus a "new in this drop" filter.
- **Collections:** group items by shared name (the "Reaver" skins, card, spray and buddy) as a filter and in the details panel.
- **"Show all in the grid"** from the details panel (all of a weapon's skins, all of a skin's chromas).
- **AI descriptions (optional):** have a vision model write a short description of each image once, so search can find "dragon" or "neon city" even when the name doesn't say so. It's a one-off cost for about 11,000 images, and needs an API key.
- **Install the skill** when the team is ready, and try real agent requests to tune the rules further.
