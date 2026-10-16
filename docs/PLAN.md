# Local Image Discovery Tool — Build Plan

Oct 16, 2026

## Goal and scope

Build a local tool that points at a content folder (one catalog JSON plus sub-folders of images), and makes those images easy to find: browse a folder as a scrolling grid, and search across every image by its catalog metadata.

**In scope**

- Runs locally with one command and opens in the browser (same setup as `local-html-edit`: `uv run imagefind`)
- Works on Windows and macOS
- Pick a content folder; the tool reads the catalog and indexes the image folders
- Grid view of thumbnails with infinite scroll, filtered by folder(s)
- Search by name, ID, description and other metadata, in any language the catalog carries
- Detail panel for one image: full-size preview, its other variants, and its metadata
- Quick actions: copy file path, copy ID, open the containing folder

**Out of scope for v1**

- Editing the catalog or the images (the tool is read-only: it never writes inside the content folder)
- Tagging, favourites or collections
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
2. **Scan the folders.** For each image file: folder, filename, ID, variant, size, modified time and pixel dimensions (read from the header only, which is fast).
3. **Join on ID** (case-insensitive) and write everything to a SQLite database in the cache folder: an `images` table, an `entries` table and an FTS5 table over names, descriptions, IDs and filenames.
4. **Reuse the index next time.** Store the catalog's `version`, its mtime, and each folder's file count and newest mtime; rebuild only what changed. `--rebuild` forces a full rebuild.

The cache lives in the user's cache directory (`%LOCALAPPDATA%\imagefind` / `~/Library/Caches/imagefind`), keyed by a hash of the content folder's path.

**Why SQLite rather than in-memory search:** the full dump is expected to be much larger than the sample. SQLite keeps startup fast after the first run, FTS5 gives prefix and multi-word search for free, and filters (folder, section, variant, has-metadata) become simple `WHERE` clauses.

### Thumbnails

- Generated on demand on first request, at 320 px on the long side, saved as WebP in the cache folder and keyed by path + mtime.
- A background worker pre-generates thumbnails for the folder being viewed so scrolling stays smooth.
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

## User interface

One page, three areas:

- **Left sidebar: folders.** A checkbox list of image folders with counts (select one, several or all). Below it, filters: catalog section, variant (base / killstream / listview / splash / …), and "only images with metadata" / "only images without metadata".
- **Top bar: search.** Search as you type, debounced. Matches names in every language, descriptions, IDs and filenames, with prefix matching (`vand` finds "Vandal"). A language selector picks which name to show on the cards (default en-US). Sort by name, folder or file size.
- **Main area: the grid.** Thumbnail cards with the name and a small variant badge, in a responsive grid with adjustable card size. Infinite scroll. A result count ("312 images"). Options to group the variants of one ID into a single card or show each file separately.
- **Detail panel** (click a card; arrow keys move to the next card): large preview on a checkerboard background so transparent images read clearly, every variant of the ID as switchable tabs, dimensions and file size, the catalog fields in readable form (name, description, section, parent such as "Altitude Odin → Odin"), the raw JSON collapsed, and buttons to copy the path, copy the ID and show in folder.

The URL hash holds the current folder selection, query and open image, so a view can be bookmarked and the back button works.

## Milestones

1. **Skeleton.** Package, CLI, server, folder picker, presence shutdown (ported from `local-html-edit`). Opening a folder shows its subfolders and counts.
2. **Catalog and scan.** Catalog flattening, filename parsing, the ID join and the SQLite index, with tests on the sample content (all 74 sample images resolve to entries).
3. **Grid browsing.** Folder selection, thumbnail generation and caching, paged infinite-scroll grid.
4. **Search and filters.** FTS search, facets, language selector, sorting, URL state.
5. **Detail panel.** Variants, metadata, parent/child links, copy and reveal actions, keyboard navigation.
6. **Scale and hardening.** Test against the full dump (or a generated large fixture): first-run indexing time, incremental rebuild, thumbnail pre-generation, a missing-images report, and handling of non-PNG files and bad filenames.

## Open questions

1. **How big is the full dump?** Rough number of images and folders, and total size. This decides whether background thumbnail pre-generation is needed from day one.
2. **Which folders and variants are coming?** Knowing the full list (for example `Characters`, `Skins`, `PlayerCards`, `Sprays`) lets us check that every naming pattern parses, and lets us give variants friendly labels.
3. **Which languages matter for search?** Searching all 18 is cheap, but the display default and the language selector should match how the tool is used.
4. **Should the full content live in the repo?** At this size it probably shouldn't: the tool takes the content folder's path, so the dump can sit anywhere on disk. The sample in `content/` stays as a test fixture, and `.gitignore` can exclude anything beyond it.
5. **Is search on the metadata enough, or do people also need to find "all skins for weapon X" or "everything from act Y"?** Relationship filters (skin → weapon) are easy to add from the catalog; act/season links don't appear in the sample data.
