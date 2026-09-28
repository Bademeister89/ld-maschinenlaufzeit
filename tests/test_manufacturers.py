"""Werkzeughersteller: Liste in der Konfiguration, Auswahl im Werkzeug-Dialog."""

import pytest
from fastapi.testclient import TestClient

from app.config import MachineConfig, Settings
from app.db import Database
from app.main import create_app

from .conftest import feed, snap


@pytest.fixture
def client(tmp_path):
    settings = Settings(machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"),), db_path=tmp_path / "h.db", simulate=True)
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as c:
        feed(app.state.ctx.collectors["m1"], (1000, snap("STARTED", tool="T5")), (1060, snap("STARTED", tool="T5")))
        yield c


def names(client):
    return [m["name"] for m in client.get("/api/config/manufacturers").json()["manufacturers"]]


def test_add_and_list(client):
    for name in ("Walter", " Garant ", "Hoffmann"):
        assert client.post("/api/config/manufacturers", json={"name": name}).status_code == 201
    assert names(client) == ["Garant", "Hoffmann", "Walter"]  # alphabetisch, ohne Leerzeichen
    assert client.get("/api/tools").json()["manufacturers"] == ["Garant", "Hoffmann", "Walter"]


@pytest.mark.parametrize(("name", "code"), [("", 400), ("   ", 400), ("x" * 81, 400), ("garant", 409)])
def test_add_invalid(client, name, code):
    client.post("/api/config/manufacturers", json={"name": "Garant"})
    assert client.post("/api/config/manufacturers", json={"name": name}).status_code == code


def test_rename_updates_tools(client):
    mid = client.post("/api/config/manufacturers", json={"name": "Garant"}).json()["id"]
    client.post("/api/config/manufacturers", json={"name": "Walter"})
    client.put("/api/tools/m1/5", json={"manufacturer": "Garant"})
    [m] = [m for m in client.get("/api/config/manufacturers").json()["manufacturers"] if m["name"] == "Garant"]
    assert m["tools"] == 1
    assert client.put(f"/api/config/manufacturers/{mid}", json={"name": "Garant Hoffmann"}).status_code == 200
    assert client.get("/api/tools/m1/5").json()["manufacturer"] == "Garant Hoffmann"
    assert client.put(f"/api/config/manufacturers/{mid}", json={"name": "WALTER"}).status_code == 409
    assert client.put("/api/config/manufacturers/999", json={"name": "X"}).status_code == 404


def test_delete_keeps_tool_entry(client):
    mid = client.post("/api/config/manufacturers", json={"name": "Garant"}).json()["id"]
    client.put("/api/tools/m1/5", json={"manufacturer": "Garant"})
    assert client.delete(f"/api/config/manufacturers/{mid}").status_code == 204
    assert names(client) == []
    assert client.get("/api/tools/m1/5").json()["manufacturer"] == "Garant"
    assert client.delete(f"/api/config/manufacturers/{mid}").status_code == 404


def test_seeded_once_from_existing_tools(tmp_path):
    path = tmp_path / "alt.db"
    db = Database(path)
    db._execute("DELETE FROM meta WHERE key = 'manufacturers_seeded'")  # Stand vor der Herstellerliste
    db.ensure_machine("m1", "DMG 1", "10.0.0.1", 19000)
    db.insert_tool("m1", 1, 0, manufacturer="Garant")
    db.insert_tool("m1", 2, 0, manufacturer="Walter")
    db.close()
    db = Database(path)
    assert [m["name"] for m in db.manufacturers()] == ["Garant", "Walter"]
    db.delete_manufacturer(db.manufacturers()[1]["id"])
    db.close()
    db = Database(path)  # gelöschte Hersteller kommen beim nächsten Start nicht zurück
    assert [m["name"] for m in db.manufacturers()] == ["Garant"]
    db.close()


def test_pages(client):
    assert 'id="hersteller"' in client.get("/konfiguration.html").text
    html = client.get("/werkzeuge.html").text
    assert '<select id="f-manufacturer">' in html and 'href="konfiguration.html#hersteller"' in html
