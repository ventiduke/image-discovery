"""Command line entry point: ``imagefind [content-folder]`` or ``python -m imagefind``."""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

import uvicorn

from .library import LibraryError
from .presence import Presence
from .server import create_app

DEFAULT_PORT = 8766  # local-html-edit uses 8765, so both can run at once


def default_content() -> Path | None:
    """The repo's own content/ folder (or one in the current directory)."""
    for candidate in (Path(__file__).resolve().parent.parent / "content", Path.cwd() / "content"):
        if candidate.is_dir():
            return candidate
    return None


def _bind(port: int) -> socket.socket:
    """Bind to 127.0.0.1:port, or any free port if that one is taken."""
    for candidate in (port, 0):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        if sys.platform == "win32":
            # Never share the port with another running server.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            # Allow an immediate restart on the same port (lingering TIME_WAIT).
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", candidate))
            return sock
        except OSError:
            sock.close()
    raise SystemExit("Could not bind a local port.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="imagefind",
        description="Browse and search a folder of catalogued images.",
    )
    parser.add_argument(
        "path", nargs="?", help="content folder to open (default: the repo's content folder)"
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    args = parser.parse_args(argv)

    sock = _bind(args.port)
    port = sock.getsockname()[1]
    presence = Presence()
    stop_reason: list[str] = []

    def request_exit(reason: str) -> None:
        if not stop_reason:
            stop_reason.append(reason)
        server.should_exit = True

    app = create_app(
        allowed_hosts={f"127.0.0.1:{port}", f"localhost:{port}"},
        on_exit=lambda: request_exit("Stopped from the browser."),
        presence=presence,
    )

    content = args.path or default_content()
    if content:
        try:
            library = app.state.session.open(content)
        except LibraryError as exc:
            sys.exit(str(exc))
        print(f"Content folder: {library.root} ({library.images:,} images)")

    server = uvicorn.Server(
        uvicorn.Config(app, log_level="warning", timeout_graceful_shutdown=3)
    )
    start_url = f"http://127.0.0.1:{port}/"

    def announce():
        while not server.started:
            if server.should_exit:
                return
            time.sleep(0.05)
        print(f"Image Discovery is running at {start_url}")
        print("It stops by itself shortly after you close its browser tabs,")
        print("or use the Exit button, or press Ctrl+C here.")
        if not args.no_browser:
            webbrowser.open(start_url)

    def watchdog():
        last_tick = time.monotonic()
        while not server.should_exit:
            time.sleep(2)
            now = time.monotonic()
            if now - last_tick > 30:
                # The computer was asleep; tabs haven't had a chance to check in.
                presence.resume()
            last_tick = now
            if presence.idle():
                request_exit("All Image Discovery tabs were closed, so it stopped.")

    threading.Thread(target=announce, daemon=True).start()
    threading.Thread(target=watchdog, daemon=True).start()
    server.run(sockets=[sock])
    print(stop_reason[0] if stop_reason else "Image Discovery stopped.")


if __name__ == "__main__":
    main()
