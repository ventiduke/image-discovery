"""Local web server: the app page and its JSON API."""

from __future__ import annotations

import mimetypes
import secrets
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from PIL import Image
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import colors, index, picker, thumbs
from .library import Library, LibraryError, open_library
from .presence import Presence

STATIC_DIR = Path(__file__).parent / "static"
ASSETS = ("app.js", "app.css", "keepalive.js")

# Windows takes MIME types from the registry, which is sometimes wrong (.js as
# text/plain breaks scripts). Pin the ones the page depends on.
for _type, _ext in [("text/javascript", ".js"), ("text/css", ".css")]:
    mimetypes.add_type(_type, _ext)

_no_store = {"Cache-Control": "no-store"}
# Image URLs carry the file's size and modification time (?v=...), so a
# changed file gets a new URL and the browser can keep the old one for good.
_cache_forever = {"Cache-Control": "private, max-age=31536000, immutable"}


class OpenRequest(BaseModel):
    path: str


class PresenceRequest(BaseModel):
    id: str
    state: str


@dataclass(frozen=True)
class Ready:
    """An indexed content folder: what the grid and thumbnails read from."""

    root: Path
    db_path: Path
    thumbs_dir: Path


class Session:
    """The content folder open in this run of the server, and its index.

    Opening a folder returns at once; the index is built on a background
    thread and :attr:`status` reports its progress. Opening another folder
    while one is indexing lets that build finish, but its result is dropped.
    Once indexed, missing thumbnails are made in the background.
    """

    def __init__(self, cache_for: Callable[[Path], Path] = index.cache_dir,
                 warm_thumbnails: bool = True) -> None:
        self.cache_for = cache_for
        self.warm_thumbnails = warm_thumbnails
        self.library: Library | None = None
        self.status: dict = {"state": "none"}
        self.ready: Ready | None = None
        self.warmer: thumbs.Warmer | None = None
        self._generation = 0
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()  # one build at a time

    def current_status(self) -> dict:
        status = dict(self.status)
        warmer = self.warmer
        if status["state"] == "ready" and warmer is not None:
            status["thumbs"] = {"done": warmer.done, "total": warmer.total,
                                "failed": warmer.failed, "running": warmer.running}
        return status

    def open(self, raw_path: str | Path, rebuild: bool = False) -> Library:
        library = open_library(raw_path)
        with self._lock:
            self._generation += 1
            generation = self._generation
            self.library = library
            self.ready = None
            self.status = {"state": "indexing", "done": 0, "total": library.images}
            if self.warmer:
                self.warmer.stop()
                self.warmer = None
        threading.Thread(
            target=self._index, args=(library, generation, rebuild), daemon=True
        ).start()
        return library

    def wait(self, timeout: float = 30) -> dict:
        """Block until the current build finishes (for tests and the CLI)."""
        deadline = time.monotonic() + timeout
        while self.status["state"] == "indexing" and time.monotonic() < deadline:
            time.sleep(0.02)
        return self.status

    def _index(self, library: Library, generation: int, rebuild: bool) -> None:
        cache = self.cache_for(library.root)
        db_path = cache / "index.sqlite"

        def progress(done: int, total: int) -> None:
            self._publish(generation, {"state": "indexing", "done": done, "total": total})

        try:
            with self._build_lock:
                result = index.build(library, db_path, rebuild=rebuild, progress=progress)
                report = index.report(db_path)
        except Exception as exc:
            self._publish(generation, {"state": "error", "error": f"Indexing failed: {exc}"})
            return
        ready = Ready(library.root, db_path, cache / "thumbs")
        warmer = self._start_warmer(ready) if self.warm_thumbnails else None
        with self._lock:
            if generation == self._generation:
                self.ready = ready
                self.warmer = warmer
                self.status = {"state": "ready", "seconds": result.seconds, "report": report}
            elif warmer:
                warmer.stop()

    def _start_warmer(self, ready: Ready) -> thumbs.Warmer:
        """Make missing thumbnails and work out colours, in the background.

        Colours are read from the thumbnail (or the image itself when it's
        small), so each large image is decoded only once. Results are written
        to the index in batches.
        """
        with closing(index.connect(ready.db_path)) as conn:
            rows = index.all_images(conn)
        jobs = []
        for row in rows:
            target = None
            if thumbs.needs_thumbnail(row["width"], row["height"]):
                target = thumbs.thumb_path(ready.thumbs_dir, row["path"], row["size"], row["mtime_ns"])
            missing = target is not None and not target.exists()
            if missing or not row["analyzed"]:
                jobs.append((row["path"], ready.root / row["path"], target, bool(row["analyzed"])))

        pending: list[tuple[str, dict]] = []
        lock = threading.Lock()
        conn = index.connect(ready.db_path)

        def flush(final: bool = False) -> None:
            with lock:
                batch = pending[:]
                pending.clear()
                if batch:
                    index.save_colors(conn, batch)
                if final:
                    conn.close()

        def process(job) -> None:
            path, source, target, analyzed = job
            im = None
            if target is not None and not target.exists():
                im = thumbs.shrink(source)
                thumbs.save(im, target)
            if analyzed:
                return
            if im is None:
                with Image.open(target if target is not None else source) as opened:
                    shares = colors.analyze(opened)
            else:
                shares = colors.analyze(im)
            with lock:
                pending.append((path, shares))
                full = len(pending) >= 200
            if full:
                flush()

        return thumbs.Warmer(jobs, total=len(jobs), process=process, on_finish=lambda: flush(final=True))

    def _publish(self, generation: int, status: dict) -> None:
        with self._lock:
            if generation == self._generation:
                self.status = status


def create_app(
    *,
    allowed_hosts: set[str],
    api_token: str | None = None,
    pick: Callable[[str | None], str | None] | None = None,
    cancel_pick: Callable[[], bool] | None = None,
    on_exit: Callable[[], None] = lambda: None,
    presence: Presence | None = None,
    cache_for: Callable[[Path], Path] = index.cache_dir,
    warm_thumbnails: bool = True,
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    session = Session(cache_for, warm_thumbnails)
    if pick is None:
        dialog = picker.Picker()
        pick, cancel_pick = dialog.pick, dialog.cancel
    cancel_pick = cancel_pick or (lambda: False)
    api_token = api_token or secrets.token_urlsafe(24)
    presence = presence or Presence()
    app.state.session = session
    app.state.api_token = api_token
    app.state.presence = presence

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        # The page reads {"error": ...} from every failed API call.
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        # Only our own origin: blocks DNS-rebinding attacks from other sites.
        if request.headers.get("host", "").lower() not in allowed_hosts:
            return Response("Bad host", status_code=403)
        presence.request()
        if request.url.path.startswith("/api/"):
            # Other sites can't read our pages to learn the token, and can't
            # send a custom header cross-origin without a CORS preflight.
            if not secrets.compare_digest(request.headers.get("x-if-token", ""), api_token):
                return JSONResponse({"error": "Missing or wrong API token."}, status_code=403)
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        return response

    # -- pages ------------------------------------------------------------

    @app.get("/", response_class=HTMLResponse)
    def page():
        page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(page.replace("{{API_TOKEN}}", api_token), headers=_no_store)

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.get("/static/{name}")
    def asset(name: str):
        if name not in ASSETS:
            raise HTTPException(404)
        return FileResponse(STATIC_DIR / name, headers=_no_store)

    # -- API --------------------------------------------------------------

    @app.get("/api/library")
    def api_library():
        library = session.library
        return {"library": library.summary() if library else None}

    @app.get("/api/status")
    def api_status():
        return session.current_status()

    def filters(
        folder: list[str] = Query(default=[]),
        q: str = "",
        langs: bool = False,
        color: list[int] = Query(default=[]),
        kind: list[str] = Query(default=[]),
        variant: list[str] = Query(default=[]),
    ) -> index.Filters:
        return index.Filters(folder, q, langs, color, kind, variant)

    def _ready_or_409() -> Ready:
        ready = session.ready
        if ready is None:
            raise HTTPException(409, "The folder is still being indexed.")
        return ready

    @app.get("/api/images")
    def api_images(
        f: index.Filters = Depends(filters),
        sort: str = "name",
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=200, ge=1, le=1000),
    ):
        ready = _ready_or_409()
        with closing(index.connect(ready.db_path)) as conn:
            total, rows = index.list_images(conn, f, sort, offset, limit)
        for row in rows:
            _add_urls(row)
            row["name"] = row["name"] or row["stem"]
        return {"total": total, "offset": offset, "images": rows}

    @app.get("/api/facets")
    def api_facets(f: index.Filters = Depends(filters)):
        ready = _ready_or_409()
        with closing(index.connect(ready.db_path)) as conn:
            return index.facets(conn, f)

    @app.get("/api/image")
    def api_image(path: str):
        ready = _ready_or_409()
        with closing(index.connect(ready.db_path)) as conn:
            detail = index.image_detail(conn, path)
        if detail is None:
            raise HTTPException(404, "No such image.")
        _add_urls(detail["image"])
        for item in [detail.get("parent"), *detail.get("references", []), *detail.get("children", []),
                     *detail.get("referenced_by", [])]:
            if item and item.get("image"):
                _add_urls(item["image"])
        for variant in detail.get("variants", []):
            _add_urls(variant)
        return detail

    @app.get("/api/catalog")
    def api_catalog():
        ready = _ready_or_409()
        with closing(index.connect(ready.db_path)) as conn:
            return {"kinds": index.catalog_overview(conn)}

    def _add_urls(row: dict) -> None:
        version = f"{row.pop('size')}-{row.pop('mtime_ns')}"
        url = quote(row["path"])
        row["thumb"] = f"/thumb/{url}?v={version}"
        row["file"] = f"/file/{url}?v={version}"

    # -- images ---------------------------------------------------------------

    def _image_or_404(path: str) -> tuple[Ready, object]:
        ready = session.ready
        if ready is None:
            raise HTTPException(404)
        # Only files the index knows about: nothing else on disk can be read.
        with closing(index.connect(ready.db_path)) as conn:
            row = index.image(conn, path)
        if row is None or not (ready.root / path).is_file():
            raise HTTPException(404)
        return ready, row

    @app.get("/thumb/{path:path}")
    def thumbnail(path: str):
        ready, row = _image_or_404(path)
        source = ready.root / path
        if not thumbs.needs_thumbnail(row["width"], row["height"]):
            return FileResponse(source, headers=_cache_forever)
        target = thumbs.thumb_path(ready.thumbs_dir, path, row["size"], row["mtime_ns"])
        if not target.exists():
            try:
                thumbs.make(source, target)
            except Exception:
                raise HTTPException(415, "Can't make a thumbnail of this image.") from None
        return FileResponse(target, media_type="image/webp", headers=_cache_forever)

    @app.get("/file/{path:path}")
    def original(path: str):
        ready, _row = _image_or_404(path)
        return FileResponse(ready.root / path, headers=_cache_forever)

    @app.post("/api/open")
    def api_open(body: OpenRequest):
        return _open(body.path)

    @app.post("/api/pick")
    def api_pick():
        current = session.library
        initial = str(current.root) if current else None
        try:
            chosen = pick(initial)
        except picker.PickerBusy as exc:
            return JSONResponse({"error": str(exc), "busy": True}, status_code=409)
        except picker.DialogUnavailable:
            return JSONResponse(
                {"error": "No folder dialog is available here. Paste the folder's path instead.",
                 "noDialog": True},
                status_code=501,
            )
        except Exception as exc:
            return JSONResponse({"error": f"Folder dialog failed: {exc}"}, status_code=500)
        if not chosen:
            return {"cancelled": True}
        return _open(chosen)

    @app.post("/api/pick-cancel")
    def api_pick_cancel():
        return {"closed": cancel_pick()}

    @app.post("/api/presence")
    def api_presence(body: PresenceRequest):
        presence.update(body.id, body.state)
        return {"ok": True}

    @app.post("/api/shutdown")
    def api_shutdown():
        on_exit()
        return {"ok": True}

    def _open(raw_path: str):
        try:
            library = session.open(raw_path)
        except LibraryError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        return {"library": library.summary()}

    return app
