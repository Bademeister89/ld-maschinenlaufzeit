"""Werkzeugauswertung (app/tools.py): Einsatzzeit je Maschine und T-Nummer, Limit, Zurücksetzen."""

import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import tools
from app.adapters.sim_adapter import TOOLS
from app.config import MachineConfig, Settings
from app.db import SCHEMA_VERSION, Database
from app.main import create_app
from app.tools import parse_tool, status

from .conftest import feed, snap

# --- Grundlagen ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("T100 FRAESER_D10", (100, "FRAESER_D10")),
        ("T5", (5, "")),
        ("  T600 KUGEL R3 ", (600, "KUGEL R3")),
        ("T1000", (1000, "")),
        ("T0", None),
        ("", None),
        (None, None),
        ("Werkzeug 5", None),
    ],
)
def test_parse_tool(text, expected):
    assert parse_tool(text) == expected


def test_status_thresholds():
    assert status(50, None) == "none"
    assert status(89, 100) == "ok"
    assert status(90, 100) == "warn"
    assert status(100, 100) == "over"
    assert status(250, 100) == "over"
    # Eingetragene Vorwarnzeit statt 90 %
    assert status(79, 100, 80) == "ok"
    assert status(80, 100, 80) == "warn"
    assert status(100, 100, 80) == "over"
    assert status(80, None, 80) == "warn"  # Vorwarnung auch ohne Limit
    assert tools.warn_threshold(100, None) == 90 and tools.warn_threshold(100, 80) == 80


def test_simulation_uses_tool_range():
    assert all(tools.TOOL_MIN <= number <= tools.TOOL_MAX for number, _ in TOOLS)


# --- Erfassung ------------------------------------------------------------------------------


def used(db, number, machine="m1"):
    return db.tool(machine, number)["used_s"]


def test_usage_only_while_running(db, make_collector):
    c = make_collector()
    feed(
        c,
        (0, snap("IDLE", tool="T1 BOHRER")),  # angewählt, steht: angelegt, zählt nicht
        (10, snap("STARTED", tool="T1 BOHRER")),
        (70, snap("STARTED", tool="T1 BOHRER")),
        (100, snap("STARTED", tool="T5 FRAESER")),  # Werkzeugwechsel
        (160, snap("STOPPED", tool="T5 FRAESER")),  # NC-Stopp zählt nicht
        (400, snap("STOPPED", tool="T5 FRAESER")),
        (410, snap("STARTED", tool="T5 FRAESER")),
        (470, snap("STARTED", tool="T5 FRAESER")),
        (480, None),  # Verbindung weg: Abschnitt endet bei der letzten Abfrage (470)
        (600, snap("STARTED", tool="T5 FRAESER")),
        (660, snap("STARTED", tool="T5 FRAESER")),
    )
    assert used(db, 1) == pytest.approx(90)
    assert used(db, 5) == pytest.approx(60 + 60 + 60)
    assert db.tool("m1", 1)["name"] == "BOHRER"
    assert db.tool("m1", 5)["last_used_at"] == 660
    # Summe der Werkzeugzeiten = Laufzeit (Zustand "Läuft") bei bekanntem Werkzeug
    running = sum(i["end"] - i["start"] for i in db.intervals(0, 1000, "m1") if i["state"] == "RUNNING")
    assert used(db, 1) + used(db, 5) == pytest.approx(running)


def test_tool_without_number_is_ignored(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED", tool=None)), (60, snap("STARTED", tool="T0")), (120, snap("STARTED")))
    assert db.tools() == []


def test_name_follows_control(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED", tool="T7 ALT")), (10, snap("STARTED", tool="T8")), (20, snap("STARTED", tool="T7 NEU")))
    assert db.tool("m1", 7)["name"] == "NEU"


def test_tools_are_per_machine(db, make_collector):
    c1 = make_collector()
    c2 = make_collector(MachineConfig(id="m2", name="Maschine 2", host="127.0.0.2"))
    feed(c1, (0, snap("STARTED", tool="T100")), (100, snap("STARTED", tool="T100")))
    feed(c2, (0, snap("STARTED", tool="T100")), (30, snap("STARTED", tool="T100")))
    assert (used(db, 100, "m1"), used(db, 100, "m2")) == (pytest.approx(100), pytest.approx(30))


def test_reset(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED", tool="T12")), (300, snap("STARTED", tool="T12")))
    db.update_tool("m1", 12, note="Test", limit_s=3600)
    assert db.reset_tool("m1", 12, 350) == pytest.approx(300)
    assert used(db, 12) == 0
    # Der laufende Abschnitt zählt ab dem Zurücksetzen weiter
    feed(c, (400, snap("STARTED", tool="T12")))
    assert used(db, 12) == pytest.approx(50)
    detail = tools.tool_detail(db, "m1", 12)
    assert detail["resets"] == [{"reset_at": 350, "used_s": pytest.approx(300), "limit_s": 3600}]
    assert detail["avg_life_s"] == pytest.approx(300)
    assert (detail["note"], detail["limit_s"]) == ("Test", 3600)  # Limit und Notiz bleiben


def test_schema_upgrade_adds_tool_tables(tmp_path):
    path = tmp_path / "alt.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES ('schema_version', '5');"
    )
    con.close()
    db = Database(path)
    assert db.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert db.tools() == []
    db.close()


# --- API ------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"), MachineConfig("m2", "DMG 2", "10.0.0.2")),
        db_path=tmp_path / "t.db",
        simulate=True,
    )
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as c:
        col = app.state.ctx.collectors["m1"]
        feed(col, (1000, snap("STARTED", tool="T100 FRAESER_D10")), (1000 + 7200, snap("STARTED", tool="T100 FRAESER_D10")))
        yield c


def test_api_list(client):
    data = client.get("/api/tools").json()
    assert [m["id"] for m in data["machines"]] == ["m1", "m2"]
    assert data["range"] == [1, 1000]
    [tool] = data["tools"]
    assert (tool["machine"], tool["number"], tool["name"], tool["status"]) == ("DMG 1", 100, "FRAESER_D10", "ok")
    assert tool["limit_s"] == 100 * 3600 and data["default_limit_h"] == 100  # Standard für neue Werkzeuge
    assert tool["used_s"] == pytest.approx(7200)
    assert (tool["in_spindle"], tool["running"]) == (True, True)
    assert data["alerts"] == 0


def test_api_limit_and_alert(client):
    r = client.put("/api/tools/m1/100", json={"limit_h": "1,5", "note": "VHM D10"})
    assert r.status_code == 200
    assert (r.json()["limit_s"], r.json()["note"], r.json()["status"]) == (5400, "VHM D10", "over")
    data = client.get("/api/tools").json()
    assert data["alerts"] == 1 and data["tools"][0]["ratio"] == pytest.approx(7200 / 5400)
    assert client.get("/api/meta").json()["tool_alerts"] == 1
    live = client.get("/api/machines").json()["machines"][0]
    assert live["tool_info"]["status"] == "over" and live["tool_info"]["number"] == 100
    # Limit wieder entfernen
    assert client.put("/api/tools/m1/100", json={"limit_h": ""}).json()["limit_s"] is None
    assert client.get("/api/meta").json()["tool_alerts"] == 0


@pytest.mark.parametrize("limit", ["-1", "0", "abc", "1e9", "nan"])
def test_api_invalid_limit(client, limit):
    assert client.put("/api/tools/m1/100", json={"limit_h": limit}).status_code == 400


def test_api_reset(client):
    detail = client.post("/api/tools/m1/100/reset").json()
    assert detail["used_s"] == 0
    assert detail["resets"][0]["used_s"] == pytest.approx(7200)
    events = client.get("/api/events", params={"from": 0, "machine": "m1"}).json()
    assert "tool_reset" in {e["type"] for e in events}


def test_new_tools_get_default_limit(client):
    # Von Hand ohne Angabe: Standard 100 h; ausdrücklich leer: kein Limit
    r = client.post("/api/tools", json={"machine_id": "m2", "number": 7})
    assert r.json()["limit_s"] == 100 * 3600
    r = client.post("/api/tools", json={"machine_id": "m2", "number": 8, "limit_h": None})
    assert r.json()["limit_s"] is None


def test_auto_created_tool_gets_default_limit(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED", tool="T9")))
    assert db.tool("m1", 9)["limit_s"] == tools.DEFAULT_LIMIT_S == 360000
    db.update_tool("m1", 9, limit_s=7200)
    feed(c, (10, snap("STARTED", tool="T3")), (20, snap("STARTED", tool="T9")))
    assert db.tool("m1", 9)["limit_s"] == 7200  # geändertes Limit bleibt beim erneuten Auftauchen


def test_api_create_and_delete(client):
    r = client.post("/api/tools", json={"machine_id": "m2", "number": "T250", "limit_h": 100, "note": "neu"})
    assert r.status_code == 201
    assert (r.json()["number"], r.json()["limit_s"], r.json()["used_s"]) == (250, 360000, 0)
    assert client.post("/api/tools", json={"machine_id": "m2", "number": 250}).status_code == 409
    assert client.post("/api/tools", json={"machine_id": "m2", "number": 0}).status_code == 400
    assert client.post("/api/tools", json={"machine_id": "m2", "number": 1001}).status_code == 400
    assert client.post("/api/tools", json={"machine_id": "m2", "number": 1000}).status_code == 201
    assert client.post("/api/tools", json={"machine_id": "m2", "number": "x"}).status_code == 400
    assert client.post("/api/tools", json={"machine_id": "zz", "number": 5}).status_code == 404
    assert client.delete("/api/tools/m2/250").status_code == 204
    assert client.get("/api/tools/m2/250").status_code == 404
    assert client.post("/api/tools/m2/999/reset").status_code == 404


def test_api_delete_tool_in_spindle(client):
    # Steckt das Werkzeug noch in der Spindel, wird es bei der nächsten Abfrage neu angelegt (zählt ab dann)
    assert client.delete("/api/tools/m1/100").status_code == 204
    assert client.get("/api/tools").json()["tools"] == []
    col = client.app.state.ctx.collectors["m1"]
    feed(col, (9000, snap("STARTED", tool="T100 FRAESER_D10")), (9060, snap("STARTED", tool="T100 FRAESER_D10")))
    [tool] = client.get("/api/tools").json()["tools"]
    assert (tool["number"], tool["created_at"], tool["used_s"]) == (100, 9000, pytest.approx(60))


def test_api_export(client):
    client.put("/api/tools/m1/100", json={"limit_h": 4})
    text = client.get("/api/tools/export.csv").content.decode("utf-8").lstrip("﻿")
    header, row = text.strip().split("\r\n")
    assert header.split(";")[:11] == [
        "Maschine", "Werkzeug", "Name", "Hersteller", "Artikelnummer", "Durchmesser (mm)", "Radius (mm)",
        "Notiz", "Einsatzzeit (h)", "Maximallaufzeit (h)", "Vorwarnung ab (h)",
    ]
    # Limit 4 h unter der Standard-Vorwarnzeit (80 h): Vorwarnung wieder automatisch bei 90 % = 3,6 h
    assert row.split(";")[:13] == ["DMG 1", "T100", "FRAESER_D10", "", "", "", "", "", "2,00", "4,00", "3,60", "50", "ok"]


def test_page(client):
    html = client.get("/werkzeuge.html").text
    assert "Werkzeugauswertung" in html and "werkzeuge.js" in html
    assert client.get("/werkzeuge.js").status_code == 200


# --- Werkzeugdaten und Vorwarnzeit (1.6.2) -------------------------------------------------------


def test_new_tools_get_default_warning(client):
    r = client.post("/api/tools", json={"machine_id": "m2", "number": 11})
    assert (r.json()["limit_s"], r.json()["warn_s"], r.json()["warn_at_s"]) == (360000, 288000, 288000)
    assert client.get("/api/tools").json()["default_warn_h"] == 80


def test_auto_created_tool_gets_default_warning(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED", tool="T4")))
    assert db.tool("m1", 4)["warn_s"] == tools.DEFAULT_WARN_S == 80 * 3600


def test_api_tool_data(client):
    r = client.put(
        "/api/tools/m1/100",
        json={
            "manufacturer": " Garant ",
            "article_no": "202340",
            "diameter": "10",
            "radius": "0,5",
            "limit_h": "4",
            "warn_h": "1,5",
            "note": "Alu",
        },
    )
    assert r.status_code == 200, r.text
    tool = r.json()
    assert (tool["manufacturer"], tool["article_no"], tool["diameter"], tool["radius"]) == ("Garant", "202340", 10, 0.5)
    assert (tool["limit_s"], tool["warn_s"], tool["status"]) == (4 * 3600, 1.5 * 3600, "warn")  # 2 h benutzt
    # Nur einzelne Felder ändern: der Rest bleibt
    tool = client.put("/api/tools/m1/100", json={"note": "Stahl"}).json()
    assert (tool["manufacturer"], tool["warn_s"], tool["note"]) == ("Garant", 1.5 * 3600, "Stahl")
    # Leeren = keine Angabe; Radius 0 ist erlaubt (scharfe Ecke)
    tool = client.put("/api/tools/m1/100", json={"diameter": "", "radius": 0}).json()
    assert (tool["diameter"], tool["radius"]) == (None, 0)
    # Suche/Anzeige: Felder kommen auch in der Liste an
    [row] = client.get("/api/tools").json()["tools"]
    assert (row["manufacturer"], row["article_no"], row["warn_at_s"]) == ("Garant", "202340", 1.5 * 3600)


@pytest.mark.parametrize(
    "payload",
    [
        {"diameter": "abc"},
        {"diameter": "0"},
        {"diameter": "5000"},
        {"radius": "-1"},
        {"warn_h": "0"},
        {"limit_h": "100", "warn_h": "100"},  # Vorwarnung muss vor dem Limit liegen
        {"limit_h": "100", "warn_h": "120"},
        {"manufacturer": "x" * 81},
        {"article_no": "x" * 81},
    ],
)
def test_api_tool_data_invalid(client, payload):
    assert client.put("/api/tools/m1/100", json=payload).status_code == 400


def test_lower_limit_below_warning_resets_warning(client):
    # Nur das Limit unter die Vorwarnzeit gesenkt: Vorwarnung wieder automatisch (90 %)
    tool = client.put("/api/tools/m1/100", json={"limit_h": "50"}).json()
    assert (tool["limit_s"], tool["warn_s"], tool["warn_at_s"]) == (50 * 3600, None, 45 * 3600)


def test_schema_upgrade_from_v6_keeps_tools(tmp_path):
    path = tmp_path / "v6.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES ('schema_version', '6');"
        "CREATE TABLE machines (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL);"
        "INSERT INTO machines VALUES ('m1', 'DMG 1', '10.0.0.1', 19000);"
        "CREATE TABLE tools (machine_id TEXT NOT NULL, number INTEGER NOT NULL, name TEXT NOT NULL DEFAULT '',"
        " note TEXT NOT NULL DEFAULT '', limit_s REAL, reset_at REAL NOT NULL, created_at REAL NOT NULL,"
        " PRIMARY KEY (machine_id, number));"
        "INSERT INTO tools (machine_id, number, name, note, limit_s, reset_at, created_at)"
        " VALUES ('m1', 12, 'FRAESER', 'alt', 360000, 0, 0);"
    )
    con.close()
    db = Database(path)
    tool = db.tool("m1", 12)
    assert (tool["name"], tool["note"], tool["limit_s"]) == ("FRAESER", "alt", 360000)
    assert (tool["manufacturer"], tool["article_no"], tool["diameter"], tool["radius"], tool["warn_s"]) == ("", "", None, None, None)
    assert tools.tool_detail(db, "m1", 12)["warn_at_s"] == 360000 * 0.9  # bisheriges Verhalten bleibt
    db.close()
