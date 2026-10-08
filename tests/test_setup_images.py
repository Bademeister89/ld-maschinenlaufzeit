"""Ein Bild je Aufspannung (Spannsituation): Hochladen im Auftrag, Anzeige auf der Live-Karte."""

import re

import pytest
from fastapi.testclient import TestClient

from app.config import MachineConfig, Settings
from app.main import create_app

from .conftest import feed, snap

KEY = "21055"
SETUP1 = "TNC:\\AUFTRAG\\26-21055-01-01.H"
SETUP2 = "TNC:\\AUFTRAG\\26-21055-02-01.H"
V1_SETUP2 = "TNC:\\AUFTRAG\\26-21055V1-02-01.H"
RIM = "TNC:\\FELGEN\\10101018-01 tasche.H"
FULL = b"\xff\xd8\xff\xe0" + b"S" * 2000
THUMB = b"\xff\xd8\xff\xe0" + b"s" * 200
FULL2 = b"\xff\xd8\xff\xe0" + b"T" * 1500


@pytest.fixture
def app_client(tmp_path):
    settings = Settings(machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"),), db_path=tmp_path / "s.db", simulate=True)
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as client:
        collector = app.state.ctx.collectors["m1"]
        t = 1000
        for program in (SETUP1, SETUP2, V1_SETUP2, RIM):  # je ein fertiger Lauf: die Aufspannungen gibt es
            feed(collector, (t, snap("IDLE", program)), (t + 10, snap("STARTED", program)), (t + 100, snap("FINISHED", program)))
            t += 200
        yield app, client


@pytest.fixture
def client(app_client):
    return app_client[1]


@pytest.fixture
def folder(app_client):
    return app_client[0].state.ctx.settings.order_images_dir


def upload(client, setup, version="", key=KEY, full=FULL):
    query = f"?version={version}" if version else ""
    return client.put(
        f"/api/orders/{key}/setups/{setup}/image{query}",
        content=full + THUMB,
        headers={"Content-Type": "application/octet-stream", "X-Image-Length": str(len(full))},
    )


def names(folder):
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def setups(client, key=KEY):
    return {(s["version"], s["setup"]): s for s in client.get(f"/api/orders/{key}").json()["setups"]}


def test_upload_shows_in_order_detail(client, folder):
    r = upload(client, 2)
    assert r.status_code == 200
    urls = r.json()
    [full_name] = [n for n in names(folder) if not n.endswith("-thumb.jpg")]
    assert re.fullmatch(r"21055-sp02-[0-9a-f]{8}\.jpg", full_name)
    assert urls == {
        "image_url": f"/api/orders/{KEY}/setups/2/image?size=full&v={full_name}",
        "thumb_url": f"/api/orders/{KEY}/setups/2/image?size=thumb&v={full_name}",
    }
    assert client.get(urls["image_url"]).content == FULL
    assert client.get(urls["thumb_url"]).content == THUMB
    s = setups(client)
    assert s[("", 2)]["thumb_url"] == urls["thumb_url"]
    assert s[("", 1)]["image_url"] is None and s[("V1", 2)]["image_url"] is None
    # Auftragsbild und Aufspannungsbild sind unabhängig
    assert client.get(f"/api/orders/{KEY}").json()["order"]["image_url"] is None
    # in den Versionsblöcken stehen dieselben Angaben
    blocks = client.get(f"/api/orders/{KEY}").json()["versions"]
    assert [s["thumb_url"] is not None for b in blocks for s in b["setups"]] == [False, True, False]


def test_version_has_its_own_image(client, folder):
    r = upload(client, 2, "v1")  # kleines v wie im Programmnamen
    assert r.status_code == 200
    assert "?version=V1&size=thumb" in r.json()["thumb_url"]
    assert [n for n in names(folder) if n.startswith("21055V1-sp02-")]
    s = setups(client)
    assert s[("V1", 2)]["thumb_url"] == r.json()["thumb_url"]
    assert s[("", 2)]["thumb_url"] is None


def test_replace_and_remove(client, folder):
    upload(client, 2)
    first = names(folder)
    upload(client, 2, full=FULL2)
    second = names(folder)
    assert len(second) == 2 and not set(first) & set(second)  # altes Bild gelöscht
    r = client.delete(f"/api/orders/{KEY}/setups/2/image")
    assert r.status_code == 200 and r.json() == {"image_url": None, "thumb_url": None}
    assert names(folder) == []
    assert client.delete(f"/api/orders/{KEY}/setups/2/image").status_code == 200  # ohne Bild: nichts zu tun


def test_unknown_setup_or_order(client, folder):
    assert upload(client, 5).status_code == 404  # Aufspannung 5 gibt es im Auftrag nicht
    assert upload(client, 1, "V1").status_code == 404
    assert upload(client, 2, key="99999").status_code == 404
    assert upload(client, 2, "X1").status_code == 422
    assert client.get(f"/api/orders/{KEY}/setups/2/image").status_code == 404
    assert names(folder) == []


def test_live_card_shows_image_of_running_setup(app_client):
    app, client = app_client
    collector = app.state.ctx.collectors["m1"]
    uploaded = upload(client, 2).json()
    feed(collector, (5000, snap("IDLE", SETUP2)), (5010, snap("STARTED", SETUP2)))
    order = client.get("/api/machines").json()["machines"][0]["order"]
    assert (order["key"], order["setup"], order["setup_thumb_url"]) == (KEY, 2, uploaded["thumb_url"])
    assert order["setup_image_url"] == uploaded["image_url"]  # für Bildschirme mit hoher Pixeldichte
    # Aufspannung 1 hat kein Bild; Version V1 hat eine eigene Aufspannung 2
    for program in (SETUP1, V1_SETUP2):
        feed(collector, (5100, snap("FINISHED", SETUP2)), (5200, snap("IDLE", program)), (5210, snap("STARTED", program)))
        assert client.get("/api/machines").json()["machines"][0]["order"]["setup_thumb_url"] is None


def test_rim_spannung(app_client):
    app, client = app_client
    r = upload(client, 1, key="10101018")
    assert r.status_code == 200
    collector = app.state.ctx.collectors["m1"]
    feed(collector, (5000, snap("IDLE", RIM)), (5010, snap("STARTED", RIM)))
    assert client.get("/api/machines").json()["machines"][0]["order"]["setup_thumb_url"] == r.json()["thumb_url"]


def test_deleting_setup_or_order_removes_images(client, folder):
    upload(client, 1)
    upload(client, 2)
    upload(client, 2, "V1")
    assert len(names(folder)) == 6
    assert client.delete(f"/api/orders/{KEY}/setups/2").status_code == 200
    assert len(names(folder)) == 4 and not [n for n in names(folder) if n.startswith("21055-sp02-")]
    assert client.delete(f"/api/orders/{KEY}").status_code == 200
    assert names(folder) == []
