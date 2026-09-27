import socket
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import MachineConfig, Settings
from app.db import Database
from app.main import create_app
from app.registry import slugify

from .conftest import snap

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture
def app_client(tmp_path):
    settings = Settings(
        machines=(MachineConfig("dmg1", "DMG 1", "10.0.0.1", sort_order=0), MachineConfig("dmg2", "DMG 2", "10.0.0.2", sort_order=10)),
        db_path=tmp_path / "data" / "cfg.db",
        simulate=True,
    )
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as client:
        yield app, client


def ids(client):
    return [m["id"] for m in client.get("/api/machines").json()["machines"]]


def test_seed_from_config_only_once(tmp_path):
    settings = Settings(machines=(MachineConfig("a", "A", "10.0.0.1"),), db_path=tmp_path / "s.db")
    with TestClient(create_app(settings, run_collectors=False)) as client:
        client.delete("/api/config/machines/a")
    # Beim nächsten Start darf die entfernte Maschine nicht wieder aus der config.yaml auftauchen
    with TestClient(create_app(settings, run_collectors=False)) as client:
        assert ids(client) == []


def test_add_update_remove(app_client):
    app, client = app_client
    r = client.post("/api/config/machines", json={"name": "DMU 50 Halle 2", "host": "192.168.5.20", "note": "Halle 2"})
    assert r.status_code == 201
    m = r.json()
    assert (m["id"], m["port"], m["note"]) == ("dmu-50-halle-2", 19000, "Halle 2")
    assert ids(client) == ["dmg1", "dmg2", "dmu-50-halle-2"]
    assert "dmu-50-halle-2" in app.state.ctx.collectors

    # Name ändern: gleicher Collector (keine neue Verbindung)
    collector = app.state.ctx.collectors["dmu-50-halle-2"]
    r = client.put("/api/config/machines/dmu-50-halle-2", json={"name": "DMU 50", "host": "192.168.5.20", "note": ""})
    assert r.json()["name"] == "DMU 50"
    assert app.state.ctx.collectors["dmu-50-halle-2"] is collector
    assert client.get("/api/machines").json()["machines"][2]["name"] == "DMU 50"

    # Prüfadresse ändern: gleicher Collector, Wert kommt sofort an
    r = client.put("/api/config/machines/dmu-50-halle-2", json={"name": "DMU 50", "host": "192.168.5.20", "check_host": "192.168.5.1"})
    assert r.json()["check_host"] == "192.168.5.1"
    assert app.state.ctx.collectors["dmu-50-halle-2"] is collector
    assert collector.machine.check_host == "192.168.5.1"

    # Adresse ändern: neuer Collector (Verbindung wird neu aufgebaut)
    client.put("/api/config/machines/dmu-50-halle-2", json={"name": "DMU 50", "host": "192.168.5.21"})
    assert app.state.ctx.collectors["dmu-50-halle-2"] is not collector

    assert client.delete("/api/config/machines/dmu-50-halle-2").status_code == 204
    assert ids(client) == ["dmg1", "dmg2"]
    assert client.put("/api/config/machines/dmu-50-halle-2", json={"name": "x", "host": "1.2.3.4"}).status_code == 404


def test_removed_machine_keeps_history_and_id_is_not_reused(app_client):
    app, client = app_client
    app.state.ctx.collectors["dmg1"].process(snap("STARTED"), 1000.0)
    client.delete("/api/config/machines/dmg1")
    db = app.state.ctx.db
    assert db.machine("dmg1")["removed"] == 1
    assert len(db.intervals(0, 2000, "dmg1")) == 1
    # Neue Maschine mit gleichem Namen bekommt eine neue ID, alte Daten bleiben getrennt
    r = client.post("/api/config/machines", json={"name": "dmg1", "host": "10.0.0.9"})
    assert r.json()["id"] == "dmg1-2"
    stats = client.get("/api/stats", params={"from": 0, "to": 2000}).json()
    assert "dmg1" not in stats["machines"]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"name": "", "host": "1.2.3.4"}, "Namen"),
        ({"name": "X", "host": ""}, "IP-Adresse"),
        ({"name": "X", "host": "1.2.3.4; rm"}, "keine gültige"),
        ({"name": "X", "host": "1.2.3.4", "port": 70000}, "Port"),
        ({"name": "X", "host": "1.2.3.4", "port": "abc"}, "Zahl"),
        ({"name": "X", "host": "10.0.0.1"}, "bereits für „DMG 1“"),
    ],
)
def test_validation_messages(app_client, payload, message):
    _, client = app_client
    r = client.post("/api/config/machines", json=payload)
    assert r.status_code == 400
    assert message in r.json()["detail"]


def test_reorder(app_client):
    _, client = app_client
    assert client.post("/api/config/order", json={"ids": ["dmg2", "dmg1"]}).status_code == 204
    assert ids(client) == ["dmg2", "dmg1"]
    assert [m["id"] for m in client.get("/api/meta").json()["machines"]] == ["dmg2", "dmg1"]
    assert client.post("/api/config/order", json={"ids": ["dmg2"]}).status_code == 400


def test_image_upload_replace_and_delete(app_client):
    app, client = app_client
    images = app.state.ctx.settings.images_dir
    r = client.put("/api/config/machines/dmg1/image", content=PNG, headers={"Content-Type": "image/png"})
    assert r.status_code == 200
    url = r.json()["image_url"]
    assert url.startswith("/api/machines/dmg1/image?v=dmg1-") and url.endswith(".png")
    got = client.get(url)
    assert got.content == PNG
    assert got.headers["content-type"] == "image/png"

    r = client.put("/api/config/machines/dmg1/image", content=JPG, headers={"Content-Type": "image/jpeg"})
    assert r.json()["image_url"].endswith(".jpg")
    assert [p.suffix for p in images.iterdir()] == [".jpg"]  # altes Bild gelöscht

    assert client.delete("/api/config/machines/dmg1/image").json()["image_url"] is None
    assert list(images.iterdir()) == []
    assert client.get("/api/machines/dmg1/image").status_code == 404


def test_image_rejects_other_files(app_client):
    _, client = app_client
    r = client.put("/api/config/machines/dmg1/image", content=b"<svg onload=alert(1)>", headers={"Content-Type": "image/svg+xml"})
    assert r.status_code == 400
    r = client.put("/api/config/machines/dmg1/image", content=PNG + b"\x00" * (5 * 1024 * 1024))
    assert r.status_code == 413


def test_connection_test_reports_unreachable(app_client):
    _, client = app_client
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    r = client.post("/api/config/test", json={"host": "127.0.0.1", "port": port})
    data = r.json()
    assert data["ok"] is False
    assert data["steps"][0]["title"] == "Netzwerk"
    assert data["steps"][0]["hints"]
    assert client.post("/api/config/test", json={"host": "a b"}).status_code == 400


def test_overview(app_client):
    _, client = app_client
    data = client.get("/api/config").json()
    assert data["settings"]["simulate"] is True
    assert data["settings"]["data_dir"].endswith("data")
    assert [m["id"] for m in data["machines"]] == ["dmg1", "dmg2"]


def test_migration_from_schema_v1(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES ('schema_version', '1');"
        "CREATE TABLE machines (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL);"
        "INSERT INTO machines VALUES ('dmg1', 'DMG 1', '10.0.0.1', 19000);"
    )
    con.commit()
    con.close()
    db = Database(path)
    row = db.machine("dmg1")
    assert (row["note"], row["sort_order"], row["image"], row["removed"], row["check_host"]) == ("", 0, None, 0, "")
    assert db.get_meta("schema_version") == "4"
    db.close()


@pytest.mark.parametrize(
    ("name", "expected"),
    [("DMG 1", "dmg-1"), ("Fräse Süd / Halle 2", "fraese-sued-halle-2"), ("!!!", "maschine")],
)
def test_slugify(name, expected):
    assert slugify(name) == expected
