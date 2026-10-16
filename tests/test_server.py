from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from imagefind import measure, picker
from imagefind.presence import Presence
from imagefind.server import create_app

CONTENT = Path(__file__).parent.parent / "content"
TOKEN = "test-token"
HOST = "testserver"


@pytest.fixture
def picked():
    return {"path": None}


@pytest.fixture
def client(picked, tmp_path):
    app = create_app(allowed_hosts={HOST}, api_token=TOKEN, pick=lambda _: picked["path"],
                     cache_for=lambda root: tmp_path / "cache", warm_thumbnails=False)
    return TestClient(app, headers={"X-IF-Token": TOKEN})


@pytest.fixture
def ready(client):
    client.post("/api/open", json={"path": str(CONTENT)})
    assert client.app.state.session.wait()["state"] == "ready"
    return client


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


def test_open_indexes_in_the_background(client):
    assert client.get("/api/status").json() == {"state": "none"}
    client.post("/api/open", json={"path": str(CONTENT)})
    status = client.app.state.session.wait()
    assert status["state"] == "ready"
    assert client.get("/api/status").json()["report"] == status["report"]
    report = status["report"]
    assert report["images"] == report["matched"] > 0
    assert {f["name"] for f in report["folders"]} == {"Maps", "Weapons"}


def test_indexing_error_is_reported(client, tmp_path):
    root = tmp_path / "content"
    (root / "Maps").mkdir(parents=True)
    (root / "Maps" / "a.png").write_bytes(b"")
    (root / "PublicContentCatalog.json").write_text("{broken")
    client.post("/api/open", json={"path": str(root)})
    status = client.app.state.session.wait()
    assert status["state"] == "error"
    assert "PublicContentCatalog.json" in status["error"]


def test_reopening_drops_the_older_build(client, tmp_path):
    other = tmp_path / "other"
    (other / "Maps").mkdir(parents=True)
    (other / "Maps" / "x.png").write_bytes(b"")
    session = client.app.state.session
    client.post("/api/open", json={"path": str(CONTENT)})
    client.post("/api/open", json={"path": str(other)})
    status = session.wait()
    assert status["state"] == "ready" and status["report"]["images"] == 1


# -- grid, thumbnails and files ---------------------------------------------------


def test_images_wait_for_the_index(client):
    assert client.get("/api/images", params={"folder": "Maps"}).status_code == 409


def test_images_lists_a_page(ready):
    res = ready.get("/api/images", params={"folder": ["Maps", "Weapons"], "limit": 5, "offset": 2})
    assert res.status_code == 200
    page = res.json()
    assert page["total"] == 74 and page["offset"] == 2 and len(page["images"]) == 5
    first = page["images"][0]
    assert first["name"] and first["kind"] in ("maps", "weapons")
    assert first["thumb"].startswith(f"/thumb/{first['path']}?v=")
    assert first["file"].startswith(f"/file/{first['path']}?v=")
    assert ready.get("/api/images", params={"limit": 5000}).status_code == 422


def test_images_take_filters(ready):
    res = ready.get("/api/images", params={"folder": ["Maps", "Weapons"], "q": "kasb"}).json()
    assert res["total"] == 2 and {i["name"] for i in res["images"]} == {"Kasbah"}
    res = ready.get("/api/images", params={"folder": "Weapons", "q": "vandal"}).json()
    assert res["total"] == 2
    res = ready.get("/api/images", params={"folder": "Weapons", "variant": "killstream"}).json()
    assert res["total"] == 35
    assert ready.get("/api/images", params={"folder": "Weapons", "color": "x"}).status_code == 422


def test_unknown_filter_values_are_refused(ready):
    res = ready.get("/api/images", params={"folder": "Maps", "use": "poster"})
    assert res.status_code == 400 and "poster" in res.json()["error"]
    assert ready.get("/api/facets", params={"folder": "Maps", "shape": "round"}).status_code == 400


def test_facets_detail_and_catalog(ready):
    facets = ready.get("/api/facets", params={"folder": ["Maps", "Weapons"]}).json()
    assert len(facets["colors"]) == 28 and facets["colors_analyzed"] == 0  # not worked out (warming is off)
    assert {k["kind"] for k in facets["kinds"]} == {"maps", "weapons"}
    path = ready.get("/api/images", params={"folder": "Weapons", "q": "vandal"}).json()["images"][0]["path"]
    detail = ready.get("/api/image", params={"path": path}).json()
    assert detail["entry"]["name"] == "Vandal"
    assert detail["image"]["thumb"].startswith("/thumb/") and detail["variants"][0]["file"].startswith("/file/")
    assert ready.get("/api/image", params={"path": "nope"}).json() == {"error": "No such image."}
    kinds = {k["kind"] for k in ready.get("/api/catalog").json()["kinds"]}
    assert {"weapons", "weaponSkins.chromas", "characters.abilities"} <= kinds


def test_thumbnails_and_originals(ready, tmp_path):
    big = "Maps/1C18AB1F-420D-0D8B-71D0-77AD3C439115_listview.png"  # 456 x 100
    res = ready.get(f"/thumb/{big}")
    assert res.status_code == 200 and res.headers["content-type"] == "image/webp"
    assert "immutable" in res.headers["cache-control"]
    assert len(list((tmp_path / "cache" / "thumbs").rglob("*.webp"))) == 1
    small = "Weapons/5261A41B-4C56-8EB5-9E46-7193F363477E_killstream.png"  # 96 x 50: served as is
    res = ready.get(f"/thumb/{small}")
    assert res.headers["content-type"] == "image/png"
    assert res.content == (CONTENT / small).read_bytes()
    assert ready.get(f"/file/{big}").content == (CONTENT / big).read_bytes()


@pytest.mark.parametrize("path", [
    "/thumb/../pyproject.toml", "/file/%2E%2E/pyproject.toml", "/file/PublicContentCatalog.json",
    "/file/Maps/nope.png", "/thumb/Maps/Readme.md",
])
def test_only_indexed_images_are_served(ready, path):
    assert ready.get(path).status_code == 404


def test_thumbnails_are_warmed_after_indexing(tmp_path):
    app = create_app(allowed_hosts={HOST}, api_token=TOKEN, cache_for=lambda root: tmp_path)
    c = TestClient(app, headers={"X-IF-Token": TOKEN})
    c.post("/api/open", json={"path": str(CONTENT)})
    session = app.state.session
    session.wait()
    session.warmer.join(60)
    thumbs = c.get("/api/status").json()["thumbs"]
    assert thumbs["done"] == thumbs["total"] == 74 and thumbs["failed"] == 0 and not thumbs["running"]
    made = len(list((tmp_path / "thumbs").rglob("*.webp")))
    assert 0 < made < 74  # small images need no thumbnail, but still get their colours
    folders = {"folder": ["Maps", "Weapons"]}
    facets = c.get("/api/facets", params=folders).json()
    assert facets["colors_analyzed"] == facets["colors_total"] == 74
    assert facets["measured"] == 74 and len(facets["uses"]) == len(measure.USES)
    assert sum(col["count"] for col in facets["colors"]) > 0
    # Opening the folder again finds nothing left to do.
    c.post("/api/open", json={"path": str(CONTENT)})
    session.wait()
    assert c.get("/api/status").json()["thumbs"]["total"] == 0


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


def test_pick_starts_in_the_open_folder(picked, tmp_path):
    seen = []
    app = create_app(allowed_hosts={HOST}, api_token=TOKEN, pick=lambda d: seen.append(d),
                     cache_for=lambda root: tmp_path)
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
