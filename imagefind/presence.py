"""Tracks open browser tabs so the server can exit when the last one closes."""

from __future__ import annotations

import threading
import time


class Presence:
    """Every page the tool serves says hello when it loads, sends a heartbeat
    while open, and says goodbye when it unloads. A goodbye is usually
    followed within a second by the next page's hello (a reload), so the
    server only exits after ``grace`` seconds with no tabs and no requests.
    Tabs that vanish without a goodbye (browser crash) are forgotten after
    ``stale`` seconds; background tabs only get timers about once a minute,
    so this must be well above that.
    """

    def __init__(self, grace: float = 15.0, stale: float = 180.0, clock=time.monotonic):
        self.grace = grace
        self.stale = stale
        self.clock = clock
        self.clients: dict[str, float] = {}
        self.last_request = clock()
        self.ever_seen = False
        self._lock = threading.Lock()

    def request(self) -> None:
        self.last_request = self.clock()

    def update(self, client: str, state: str) -> None:
        with self._lock:
            if state == "bye":
                self.clients.pop(client, None)
            else:
                self.clients[client] = self.clock()
                self.ever_seen = True

    def resume(self) -> None:
        """After the computer slept, give open tabs time to check in again."""
        with self._lock:
            now = self.clock()
            self.last_request = now
            self.clients = dict.fromkeys(self.clients, now)

    def idle(self) -> bool:
        """True once every tab has been closed for a while."""
        with self._lock:
            now = self.clock()
            self.clients = {c: t for c, t in self.clients.items() if now - t < self.stale}
            return self.ever_seen and not self.clients and now - self.last_request > self.grace
