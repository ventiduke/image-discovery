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

from . import index
from .catalog import CatalogError
from .library import LibraryError, open_library
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
    parser.add_argument("--rebuild", action="store_true", help="rebuild the index from scratch")
    parser.add_argument(
        "--report", action="store_true",
        help="index the content folder, print how its images match the catalog, and exit",
    )
    args = parser.parse_args(argv)
    content = args.path or default_content()

    if args.report:
        if not content:
            sys.exit("No content folder: pass its path.")
        print_report(content, rebuild=args.rebuild)
        return

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

    if content:
        try:
            library = app.state.session.open(content, rebuild=args.rebuild)
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


def print_report(content: str | Path, rebuild: bool = False) -> None:
    try:
        library = open_library(content)
    except LibraryError as exc:
        sys.exit(str(exc))
    db_path = index.cache_dir(library.root) / "index.sqlite"

    def progress(done: int, total: int) -> None:
        print(f"\rIndexing {done:,} of {total:,} images", end="", flush=True)

    try:
        result = index.build(library, db_path, rebuild=rebuild,
                             progress=progress if sys.stdout.isatty() else None)
    except CatalogError as exc:
        sys.exit(f"\n{exc}")
    summary = (f"Indexed {result.images:,} images in {result.seconds}s ({result.headers_read:,} read, "
               f"catalog {'re-read' if result.catalog_reloaded else 'unchanged'})")
    print(f"\r{summary:<60}" if sys.stdout.isatty() else summary)
    for line in format_report(library, index.report(db_path, unmatched_limit=25)):
        print(line)


def format_report(library, report: dict) -> list[str]:
    lines = [
        f"Content folder: {library.root}",
        f"Catalog: {library.catalog.name if library.catalog else 'none'}"
        f" (version {report['catalog_version'] or 'unknown'})",
        f"Images matched to the catalog: {report['matched']:,} of {report['images']:,}",
        "",
        f"{'Folder':<16}{'Images':>8}{'Matched':>9}  Variants / catalog kinds",
    ]
    for f in report["folders"]:
        variants = ", ".join(f"{v} {n:,}" for v, n in f["variants"].items())
        kinds = ", ".join(f"{k} {n:,}" for k, n in f["kinds"].items()) or "-"
        lines.append(f"{f['name']:<16}{f['images']:>8,}{f['matched']:>9,}  {variants}")
        lines.append(f"{'':<35}{kinds}")
    lines += ["", f"{'Catalog kind':<26}{'Entries':>8}{'With images':>13}"]
    for c in report["coverage"]:
        lines.append(f"{c['kind']:<26}{c['entries']:>8,}{c['with_images']:>13,}")
    for warning in library.warnings:
        lines += ["", f"Warning: {warning}"]
    if report["unmatched_total"]:
        lines += ["", f"Files that match no catalog entry ({report['unmatched_total']:,}):"]
        lines += [f"  {path}" for path in report["unmatched"]]
        if report["unmatched_total"] > len(report["unmatched"]):
            lines.append(f"  ... and {report['unmatched_total'] - len(report['unmatched']):,} more")
    return lines


if __name__ == "__main__":
    main()
