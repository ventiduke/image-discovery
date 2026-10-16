"""Local web server: the app page and its JSON API."""

from __future__ import annotations

import mimetypes
import secrets
import threading
import time
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from . import index, picker
from .library import Library, LibraryError, open_library
from .presence import Presence

STATIC_DIR = Path(__file__).parent / "static"
ASSETS = ("app.js", "app.css", "keepalive.js")

# Windows takes MIME types from the registry, which is sometimes wrong (.js as
# text/plain breaks scripts). Pin the ones the page depends on.
for _type, _ext in [("text/javascript", ".js"), ("text/css", ".css")]:
    mimetypes.add_type(_type, _ext)

_no_store = {"Cache-Control": "no-store"}


class OpenRequest(BaseModel):
    path: str


class PresenceRequest(BaseModel):
    id: str
    state: str


class Session:
    """The content folder open in this run of the server, and its index.

    Opening a folder returns at once; the index is built on a background
    thread and :attr:`status` reports its progress. Opening another folder
    while one is indexing lets that build finish, but its result is dropped.
    """

    def __init__(self, cache_for: Callable[[Path], Path] = index.cache_dir) -> None:
        self.cache_for = cache_for
        self.library: Library | None = None
        self.status: dict = {"state": "none"}
        self.db_path: Path | None = None
        self._generation = 0
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()  # one build at a time

    def open(self, raw_path: str | Path, rebuild: bool = False) -> Library:
        library = open_library(raw_path)
        with self._lock:
            self._generation += 1
            generation = self._generation
            self.library = library
            self.db_path = None
            self.status = {"state": "indexing", "done": 0, "total": library.images}
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
        db_path = self.cache_for(library.root) / "index.sqlite"

        def progress(done: int, total: int) -> None:
            self._publish(generation, {"state": "indexing", "done": done, "total": total})

        try:
            with self._build_lock:
                result = index.build(library, db_path, rebuild=rebuild, progress=progress)
                report = index.report(db_path)
        except Exception as exc:
            self._publish(generation, {"state": "error", "error": f"Indexing failed: {exc}"})
            return
        with self._lock:
            if generation == self._generation:
                self.db_path = db_path
                self.status = {"state": "ready", "seconds": result.seconds, "report": report}

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
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    session = Session(cache_for)
    if pick is None:
        dialog = picker.Picker()
        pick, cancel_pick = dialog.pick, dialog.cancel
    cancel_pick = cancel_pick or (lambda: False)
    api_token = api_token or secrets.token_urlsafe(24)
    presence = presence or Presence()
    app.state.session = session
    app.state.api_token = api_token
    app.state.presence = presence

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
    def index():
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
        return session.status

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
