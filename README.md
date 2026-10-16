# Image Discovery

A local tool for browsing and searching the images in `content/`: pick folders, scroll through them as a grid, and search by the metadata in the catalog JSON. It only reads the content folder; it never changes it.

See [docs/PLAN.md](docs/PLAN.md) for the full design.

## Status

| Milestone | State |
| --- | --- |
| 1. Skeleton (open a content folder, list its folders) | Done |
| 2. Catalog and scan (match images to catalog entries) | Done |
| 3. Grid browsing | Not started |
| 4. Search and filters | Not started |
| 5. Relationships and detail panel | Not started |
| 6. Full dump and refresh | Not started |

## Running it

You need [uv](https://docs.astral.sh/uv/). It installs a suitable Python for you if you don't have one.

- **Windows (PowerShell):** `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- **macOS:** `curl -LsSf https://astral.sh/uv/install.sh | sh`

Then, from this folder:

```
uv run imagefind
```

A browser tab opens on the repo's `content` folder. Use **Change folder…** to open a different content folder.

The first time a folder is opened, the tool indexes it: it reads the catalog and every image's size, and matches each file to its catalog entry. For the full dump this takes a few seconds; after that only new or changed files are re-read. The index is kept in the git-ignored `.cache` folder (for folders outside the repo, in your user cache folder), never in the content folder.

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
