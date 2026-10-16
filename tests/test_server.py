from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from imagefind import picker
from imagefind.presence import Presence
from imagefind.server import create_app

CONTENT = Path(__file__).parent.parent / "content"
TOKEN = "test-token"
HOST = "testserver"


@pytest.fixture
def picked():
    return {"path": None}


@pytest.fixture
def client(picked):
    app = create_app(allowed_hosts={HOST}, api_token=TOKEN, pick=lambda _: picked["path"])
    return TestClient(app, headers={"X-IF-Token": TOKEN})


# -- security ---------------------------------------------------------------


def test_rejects_foreign_host(client):
    assert client.get("/", headers={"host": "evil.example"}).status_code == 403


def test_api_requires_token(client):
    assert client.get("/api/library", headers={"X-IF-Token": "nope"}).status_code == 403
    assert client.post("/api/open", json={"path": str(CONTENT)}, headers={"X-IF-Token": ""}).status_code == 403


def test_page_carries_token_and_keepalive(client):
    res = client.get("/")
    assert res.status_code == 200
    assert TOKEN in res.text and "/static/keepalive.js" in res.text
    assert res.headers["content-security-policy"] == "frame-ancestors 'none'"


def test_serves_only_known_assets(client):
    for name in ("app.js", "app.css", "keepalive.js"):
        assert client.get(f"/static/{name}").status_code == 200
    assert client.get("/static/index.html").status_code == 404
    assert client.get("/static/..%2Fserver.py").status_code == 404


# -- opening a content folder -------------------------------------------------


def test_no_library_until_opened(client):
    assert client.get("/api/library").json() == {"library": None}


def test_open_lists_folders(client):
    res = client.post("/api/open", json={"path": str(CONTENT)})
    assert res.status_code == 200
    lib = res.json()["library"]
    assert lib["catalog"] == "PublicContentCatalog.json"
    assert lib["version"] == "13.06.00.5435758"
    names = {f["name"]: f["images"] for f in lib["folders"]}
    assert names["Weapons"] == 70 and names["Maps"] == 4
    assert client.get("/api/library").json()["library"] == lib


def test_open_rejects_bad_path(client):
    res = client.post("/api/open", json={"path": str(CONTENT / "nope")})
    assert res.status_code == 400
    assert "not found" in res.json()["error"]


def test_pick_cancel_and_success(client, picked):
    assert client.post("/api/pick").json() == {"cancelled": True}
    picked["path"] = str(CONTENT)
    assert client.post("/api/pick").json()["library"]["path"] == str(CONTENT.resolve())


def test_pick_without_dialog():
    def no_dialog(_):
        raise picker.DialogUnavailable("no tk")

    app = create_app(allowed_hosts={HOST}, api_token=TOKEN, pick=no_dialog)
    res = TestClient(app, headers={"X-IF-Token": TOKEN}).post("/api/pick")
    assert res.status_code == 501
    assert res.json()["noDialog"] is True


def test_pick_starts_in_the_open_folder(picked):
    seen = []
    app = create_app(allowed_hosts={HOST}, api_token=TOKEN, pick=lambda d: seen.append(d))
    c = TestClient(app, headers={"X-IF-Token": TOKEN})
    c.post("/api/pick")
    c.post("/api/open", json={"path": str(CONTENT)})
    c.post("/api/pick")
    assert seen == [None, str(CONTENT.resolve())]


def test_pick_cancel_endpoint_and_busy():
    closed = []

    def busy(_):
        raise picker.PickerBusy("A folder dialog is already open.")

    app = create_app(allowed_hosts={HOST}, api_token=TOKEN, pick=busy,
                     cancel_pick=lambda: closed.append(1) or True)
    c = TestClient(app, headers={"X-IF-Token": TOKEN})
    res = c.post("/api/pick")
    assert res.status_code == 409 and res.json()["busy"] is True
    assert c.post("/api/pick-cancel").json() == {"closed": True}
    assert closed == [1]


# -- staying alive and exiting ------------------------------------------------


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_presence_exits_only_after_last_tab_closes_and_grace_passes():
    clock = FakeClock()
    p = Presence(grace=15, stale=180, clock=clock)
    clock.t += 600
    assert not p.idle()  # never exits before any tab has connected
    p.update("a", "hello")
    p.update("b", "hello")
    p.update("a", "bye")
    clock.t += 60
    assert not p.idle()  # b still open
    p.update("b", "bye")
    p.request()
    clock.t += 10
    assert not p.idle()  # within grace: maybe a reload
    p.update("c", "hello")
    p.update("c", "bye")
    p.request()
    clock.t += 16
    assert p.idle()


def test_presence_forgets_tabs_that_vanish_and_survives_sleep():
    clock = FakeClock()
    p = Presence(grace=15, stale=180, clock=clock)
    p.update("a", "hello")
    clock.t += 100
    p.update("a", "beat")
    clock.t += 179
    assert not p.idle()
    clock.t += 3600  # computer slept
    p.resume()
    assert not p.idle()
    clock.t += 181  # no heartbeat after waking: the tab is gone (browser crashed)
    assert p.idle()


def test_presence_endpoint(client):
    assert client.post("/api/presence", json={"id": "x", "state": "hello"}).status_code == 200
    assert client.app.state.presence.clients.keys() == {"x"}


def test_shutdown_calls_exit_hook():
    called = []
    app = create_app(allowed_hosts={HOST}, api_token=TOKEN, on_exit=lambda: called.append(1))
    c = TestClient(app, headers={"X-IF-Token": TOKEN})
    assert c.post("/api/shutdown").status_code == 200
    assert called == [1]
    # Without the token, other sites can't shut it down.
    assert c.post("/api/shutdown", headers={"X-IF-Token": "no"}).status_code == 403
    assert called == [1]
