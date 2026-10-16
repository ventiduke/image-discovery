# Local Image Discovery Tool — Build Plan

Oct 16, 2026

**Progress:** milestones 1 (skeleton), 2 (catalog and scan), 3 (grid browsing) and 4 (search, filters, tags, colors and the details panel, which brings in much of 5) are built. Against a synthetic dump of 10,453 files with the real catalog, the first index takes about 3 seconds and later ones about 0.2 seconds; thumbnails and colour analysis for all 10,453 take about 40 seconds on 4 cores. The full dump has been checked: every file matches a catalog entry.

## Goal and scope

Build a local tool that points at a content folder (one catalog JSON plus sub-folders of images), and makes those images easy to find: browse a folder as a scrolling grid, and search across every image by its catalog metadata.

**In scope**

- Runs locally with one command and opens in the browser (same setup as `local-html-edit`: `uv run imagefind`)
- Works on Windows and macOS
- Opens the repo's own `content/` folder by default (another folder can be passed or picked); the tool reads the catalog and indexes the image folders
- Grid view of thumbnails with infinite scroll, filtered by folder(s)
- Search by name, ID, description and other metadata. English first; other languages as an option
- Relationship filters: browse from a weapon to its skins, from a skin to its chromas and levels, and from a collection (e.g. "Reaver") to everything in it
- Detail panel for one image: full-size preview, its other variants, its metadata and its related items
- Handles the monthly content refresh: re-indexes automatically and can show what's new in this drop
- Quick actions: copy file path, copy ID, open the containing folder

**Out of scope for v1**

- Editing the catalog or the images (the tool is read-only: it never writes inside the content folder)
- Tagging, favourites or user-made collections
- Hosting, multi-user access or auth
- Image-content search (search by what an image looks like rather than its metadata)

**Design principle:** the content folder is never modified. Everything the tool generates (thumbnails, the search index) lives in a cache folder outside it.

## What the content looks like

From the sample in `content/`:

- **`PublicContentCatalog.json`** (15 MB). A top-level `version` and 14 sections: `characters` (30), `weapons` (36), `weaponSkins` (1,415, with 2,931 chromas and 2,692 levels nested inside), `armors`, `maps` (27), `playerCards` (1,016), `playerTitles` (445), `charms` (898), `sprays` (921), `flexes`, `gameModes`, `acts`, `characterRoles`, `matchmakingQueues`. About 4,900 top-level entries, plus about 5,600 nested chromas and levels that carry IDs of their own.
- Every entry has an `id` (a GUID) and a localized `name` (`defaultText` plus 18 `localizedByCulture` strings). Many also have a `description`. Some have extra fields worth searching or filtering on: `category` (weapons), `weaponId` (skins → weapon), `titleText` (player titles), `type` (acts).
- **Image folders** (`content/Weapons`, `content/Maps`, more to come). Each file is named `<catalog id>.png` or `<catalog id>_<variant>.png`. Variants seen so far: none (the base image), `_killstream`, `_listview`, `_splash`.
- **Every sample image matches a catalog ID** (all 70 in `Weapons` match `weapons`, all 4 in `Maps` match `maps`).
- **Sizes vary a lot**: 96×50 icons of 1 KB up to 1920×1080 splash art of 3.3 MB. A grid can't load full-size files, so thumbnails are required.

Two things the tool must not assume:

- **Folder names don't have to equal section names.** `Weapons` happens to match `weapons`, but the tool should match images to entries by ID across the whole catalog, not by folder name. Then a folder like `Skins` holding chroma images still resolves.
- **Not every image will have a catalog entry, and not every entry will have an image.** Unmatched images still appear in the grid (searchable by filename); entries with no image can be counted in a "missing images" report.

**The full dump** is 11,262 files and over 1 GB, and it will live in this repo's `content/` folder, replaced about once a month. Its folders, compared with the catalog:

| Folder | Files | Catalog section | Entries | What the count suggests |
| --- | --- | --- | --- | --- |
| Abilities | 118 | `characters[].abilities` | 121 | One image per ability. **Abilities have no `id`**, only a `label` of the form `<character id>_Ability1` (see below) |
| Armors | 3 | `armors` | 3 | One each |
| CharacterRoles | 4 | `characterRoles` | 4 | One each |
| Characters | 89 | `characters` | 30 | About 3 variants per character |
| Charms | 898 | `charms` (898, each with 1 level) | 898 | One each, by charm or level ID |
| Flexes | 23 | `flexes` | 23 | One each |
| GameModes | 16 | `gameModes` | 18 | Two modes without images |
| Maps | 72 | `maps` | 27 | About 3 variants (base, `_listview`, `_splash`) for most maps |
| PlayerCards | 4,058 | `playerCards` | 1,016 | About 4 variants per card |
| Sprays | 921 | `sprays` (921, each with 1 level) | 921 | One each, by spray or level ID |
| Weapons | 70 | `weapons` | 36 | Base + `_killstream` for 35 weapons |
| WeaponSkins | 4,990 | `weaponSkins` + chromas + levels | 1,415 + 2,931 + 2,692 | Images for some skins, chromas and levels, not all |

Sections with no folder: `playerTitles` (text only), `acts`, `matchmakingQueues`. They're still indexed, so they show up in search as text results with no image, and acts can be used as filters if a link to them turns up.

**Filenames are matched by `label` as well as `id`.** Abilities show why: the image for Gekko's "Wingman" is probably `E370FA57-…_Ability1.png`, which a plain `<id>_<variant>` split would misread as a character image with an `Ability1` variant. So the scanner first looks up the whole filename (without the extension) as an ID or label, and only if that fails splits off the last `_suffix` as a variant. Files that match nothing are listed in an "unmatched files" report, so naming surprises in the other folders show up on the first run rather than silently.

## Content in the repo

- **Location.** The full dump goes in `content/` (catalog at the top, image folders below), replacing the sample. `uv run imagefind` with no argument opens it.
- **Monthly refresh.** Delete the old `content/` contents and copy in the new drop, so images removed upstream don't linger. Commit with the catalog version in the message (e.g. "Content drop 13.07"). Git stores files by content, so images that are unchanged between drops add nothing to the repo's size; only new or changed images do.
- **Uploading.** 11,000 files is too many for GitHub's "Add files via upload" page, which is how the sample went in (it takes about 100 files at a time). Use `git` or GitHub Desktop to commit and push the drop. GitHub rejects single files over 100 MB; the largest sample image is 3.3 MB, so this shouldn't come up, but the refresh script should check.
- **Keep generated files out of the repo.** Thumbnails and the search index go in a git-ignored `.cache/` folder in the repo (or the user's cache directory). They are never committed.
- **A small refresh helper** (`uv run imagefind refresh <path-to-drop>`): replaces `content/`, checks for oversize files and filenames that don't parse, and prints a summary (new, changed and removed images; the new catalog version) to paste into the commit message.

## Architecture

Mirror `local-html-edit`: a small Python package run with `uv`, a FastAPI server bound to `127.0.0.1`, and a vanilla HTML/CSS/JS front end with no build step. Reuse its proven pieces: port binding, the native folder picker (adapted from file to folder), the tab-presence auto-shutdown and the Exit button.

```
imagefind/
  __main__.py      CLI: imagefind [content-folder] --port --no-browser --rebuild
  server.py        FastAPI app and routes
  catalog.py       load the JSON, flatten entries, build the ID → entry map
  scan.py          walk image folders, parse <id>_<variant> filenames
  index.py         SQLite + FTS5 search index; rebuild when the content changes
  thumbs.py        thumbnail generation and cache (Pillow)
  picker.py        native folder dialog (from local-html-edit)
  static/
    index.html     single page: folder sidebar, search bar, grid, detail panel
    app.js
    app.css
tests/
```

Dependencies: `fastapi`, `uvicorn`, `pillow`. SQLite with FTS5 ships with Python.

### Indexing (on open)

1. **Load the catalog.** Walk every section and record each object that has an `id`: the section, its parent if nested (for example a chroma's skin and that skin's weapon), the English name, all localized names, the description, and the scalar fields (`category`, `type`, `titleText`, …). Keep the raw JSON for each entry for the detail panel.
2. **Scan the folders.** For each image file: folder, filename, matched ID or label (rule above), variant, size, modified time and pixel dimensions (read from the header only, which is fast).
3. **Join on ID** (case-insensitive) and write everything to a SQLite database in the cache folder: an `images` table, an `entries` table and an FTS5 table over names, descriptions, IDs and filenames.
4. **Reuse the index next time.** Store the catalog's `version`, its mtime, and each folder's file count and newest mtime; rebuild only what changed. `--rebuild` forces a full rebuild.

The cache lives in a git-ignored `.cache/` folder next to `content/` (for another content folder, in the user's cache directory, keyed by a hash of its path).

**"New in this drop."** When the catalog `version` changes, the index keeps the previous drop's list of IDs and file hashes, so the grid can filter to images that are new or changed since the last drop.

**Why SQLite rather than in-memory search:** with 11,000 files and about 10,500 catalog entries, holding everything in the browser is possible but slow to load. SQLite keeps startup fast after the first run, FTS5 gives prefix and multi-word search for free, and filters (folder, section, variant, has-metadata) become simple `WHERE` clauses.

### Thumbnails

- Generated at 320 px on the long side, saved as WebP in the cache folder and keyed by path + mtime. At that size 11,000 thumbnails come to roughly 100–200 MB.
- After indexing, a background worker generates every missing thumbnail (several processes in parallel, the folder being viewed first). The first run after a new drop takes a few minutes; the grid works meanwhile and makes any thumbnail it needs on request. Later runs only redo changed images.
- Small images (already under the thumbnail size) are served as they are.
- The grid uses `loading="lazy"` and requests one page of results at a time.

### API

| Route | Purpose |
| --- | --- |
| `POST /api/open` | Open a content folder (path, or pick via the native dialog); starts indexing |
| `GET /api/status` | Indexing progress, counts, catalog version |
| `GET /api/folders` | Folders with image counts |
| `GET /api/facets` | Sections, variants, categories with counts, for the filter controls |
| `GET /api/images?q=&folder=&section=&variant=&lang=&sort=&offset=&limit=` | One page of grid results |
| `GET /api/images/{id}` | Detail: all variants of this ID, its catalog entry, parent and child entries |
| `GET /thumb/{path}` / `GET /file/{path}` | Thumbnail / original image, limited to files inside the content folder |
| `POST /api/reveal` | Show a file in Explorer or Finder |

### Relationships

Built from the catalog during indexing and stored as a `relations` table (from ID, to ID, kind), so each is a single filter:

| Relationship | Source | Notes |
| --- | --- | --- |
| Skin → weapon | `weaponSkins[].weaponId` | All 1,415 skins have one |
| Chroma / level → skin | nesting (`chromas`, `levels`) | 2,931 chromas, 2,692 levels |
| Skin → default chroma | `defaultChromaId` | Used to pick the skin's cover image |
| Charm / spray level → item | nesting (`levels`) | |
| Weapon → category | `weapons[].category` | Rifle, SMG, Sniper, Sidearm, Shotgun, Heavy, Melee |
| Ability → character | nesting (`abilities`), label prefix | 121 abilities across 29 characters; slots ability1/2, grenade, ultimate, passive |
| Collection (inferred) | names | See below |
| Same item, other variants | shared ID | base / killstream / listview / splash |

**Collections are inferred, not given.** The catalog has no bundle or collection field, but names follow a pattern: "Reaver Vandal", "Reaver Card", "Reaver Spray", "Reaver Buddy". Stripping the weapon name from a skin's name gives the collection name for 1,219 of the 1,415 skins (411 collections), and 323 of those also match a card, spray, buddy or title by name. To avoid false matches ("Prime Time Buddy" is not part of "Prime"), only the known suffixes count (`Card`, `Spray`, `Buddy`, `Title`, and the weapon names). The UI labels these as "Collection (from names)" so nobody mistakes them for official data. Characters don't link to their roles in the catalog, so there is no role filter unless the full dump adds that link.

In the UI, relationships show up in three places:

- **Filters** in the sidebar: weapon (with category), collection, and section.
- **Detail panel**: a "Related" strip, e.g. for a skin: its weapon, its chromas and levels, and the rest of its collection. Each item is clickable.
- **"Show all" links**: from a weapon, "all its skins"; from a collection, "everything in Reaver". These set the grid's filters, so they're bookmarkable like any other view.

## User interface

One page, three areas:

- **Left sidebar: folders.** A checkbox list of image folders with counts (select one, several or all). Below it, filters: catalog section, variant (base / killstream / listview / splash / …), and "only images with metadata" / "only images without metadata".
- **Top bar: search.** Search as you type, debounced. Matches English names, descriptions, IDs and filenames, with prefix matching (`vand` finds "Vandal"). Cards show English names. A "search all languages" toggle (off by default) also matches the 17 other languages, for when someone pastes a localized name. Sort by name, folder or file size.
- **Main area: the grid.** Thumbnail cards with the name and a small variant badge, in a responsive grid with adjustable card size. Infinite scroll. A result count ("312 images"). Options to group the variants of one ID into a single card or show each file separately.
- **Detail panel** (click a card; arrow keys move to the next card): large preview on a checkerboard background so transparent images read clearly, every variant of the ID as switchable tabs, dimensions and file size, the catalog fields in readable form (name, description, section, parent such as "Altitude Odin → Odin"), the raw JSON collapsed, and buttons to copy the path, copy the ID and show in folder.

The URL hash holds the current folder selection, query and open image, so a view can be bookmarked and the back button works.

## Milestones

1. **Skeleton.** Package, CLI, server, folder picker, presence shutdown (ported from `local-html-edit`), `.gitignore` for `.cache/`. Opens `content/` by default and shows its subfolders and counts.
2. **Catalog and scan.** Catalog flattening, filename parsing, the ID join and the SQLite index, with tests on the sample content (all 74 sample images resolve to entries).
3. **Grid browsing.** Folder selection, background thumbnail generation and caching, paged infinite-scroll grid.
4. **Search, filters and colours** (expanded on request). Done:
   - Search as you type over English names, descriptions, IDs and *related* names (an ability's character, a skin's weapon), with an all-languages toggle.
   - Colours: each image's share of 28 colour bins (4 neutrals, 12 hues × bright/dark), worked out in the background alongside thumbnails; a swatch grid with counts filters to images with at least 10% of every picked colour, best matches first.
   - Kind and variant filters; all filter counts follow the other filters.
   - Details panel: preview, variants, colours, every catalog field (IDs that point at other items shown as links), parent, contents, items that link here, other-language names and the raw JSON; ← → to step through the grid.
   - "What the catalog holds" in the report tab: for each kind, how often each field is filled in, with examples.
5. **Relationships.** Remaining: inferred collections as a filter, "show all of these in the grid" from the details panel (e.g. all of a weapon's skins), reveal in Explorer/Finder.
6. **Full dump and refresh.** Run against the real 11,000-file drop: first-run indexing and thumbnail time, incremental re-index, the refresh helper, "new in this drop", the missing-images report, and handling of non-PNG files and filenames that don't parse.

## Open questions

None that block milestone 1. To confirm before milestone 2, a few sample filenames from `Abilities`, `Characters`, `PlayerCards` and `WeaponSkins` would show the variant suffixes and whether the ability files use the `<character id>_Ability1` labels. If not, the unmatched-files report will show it on the first run against the full dump.

## Decisions

- **Added after milestone 3** (requested): search, more visibility of the metadata, and a colour map/filter. Tags from item names were built too, then removed: searching a word ("Blush") does the same job.
- **Full dump:** 11,262 files in 12 folders, over 1 GB (folder list above). Thumbnails are pre-generated in the background from the start.
- **Language:** English is the primary language for display and search; other languages are an opt-in search toggle.
- **Content location:** the full dump lives in the repo's `content/` and is replaced about monthly. Generated files stay out of git.
- **Relationships:** in v1, including collections inferred from names.
- **Stray root images:** the 13 images accidentally uploaded to the repo root were removed. They were map images (base, list-view and splash for 5 maps) and remain in git history; the full dump is expected to include them under `content/Maps`.
