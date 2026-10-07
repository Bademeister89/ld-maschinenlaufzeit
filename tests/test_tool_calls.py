"""Aufrufzähler je Werkzeug: wie oft ein Werkzeug in die Spindel gewechselt wurde."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import tools
from app.collector import MachineCollector
from app.config import MachineConfig, Settings
from app.db import SCHEMA_VERSION, Database
from app.main import create_app
from app.tools import is_call, spindle_number

from .conftest import MACHINE, NullAdapter, feed, snap


def calls(db, number, machine="m1"):
    return db.tool(machine, number)["calls"]


def call_rows(db):
    return [(r["machine_id"], r["number"], r["called_at"]) for r in db._query("SELECT * FROM tool_calls ORDER BY id")]


# --- Regeln -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("T12", 12), ("T12 FRAESER_D10", 12), ("  T5 ", 5), ("T0", 0), (None, None), ("", None), ("Werkzeug 5", None)],
)
def test_spindle_number(text, expected):
    assert spindle_number(text) == expected


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (1, 5, True),  # Wechsel
        (0, 5, True),  # aus leerer Spindel
        (5, 5, False),  # gleiches Werkzeug
        (5, 0, False),  # Spindel geleert: kein Aufruf
        (None, 5, False),  # vorher unbekannt (Start)
        (5, None, False),  # keine Angabe
    ],
)
def test_is_call(before, after, expected):
    assert is_call(before, after) is expected


# --- Erfassung --------------------------------------------------------------------------------


def test_collector_counts_tool_changes(db, make_collector):
    c = make_collector()
    feed(
        c,
        (0, snap("IDLE", tool="T1")),  # erstes gesehenes Werkzeug: kein Aufruf (vorher unbekannt)
        (10, snap("STARTED", tool="T1")),
        (20, snap("STARTED", tool="T5")),  # Aufruf T5
        (30, snap("STARTED", tool="T5 FRAESER")),  # nur der Name kommt dazu: kein Aufruf
        (40, snap("STARTED", tool=None)),  # keine Angabe: ändert nichts
        (50, snap("STARTED", tool="T1")),  # Aufruf T1
        (60, snap("IDLE", tool="T0")),  # Spindel leer
        (70, snap("IDLE", exec_mode="MANUAL", tool="T1")),  # Aufruf T1 im Handbetrieb zählt auch
        (80, None),  # Verbindung weg
        (200, snap("STARTED", tool="T1")),  # gleiches Werkzeug wie vorher: kein Aufruf
        (210, snap("STARTED", tool="T7")),  # Aufruf T7
    )
    assert (calls(db, 1), calls(db, 5), calls(db, 7)) == (2, 1, 1)
    assert [r[1:] for r in call_rows(db)] == [(5, 20), (1, 50), (1, 70), (7, 210)]
    assert db._query("SELECT program FROM tool_calls LIMIT 1")[0]["program"] == "P1"


def test_restart_does_not_count_tool_already_in_spindle(db, make_collector):
    feed(make_collector(), (0, snap("STARTED", tool="T1")), (10, snap("STARTED", tool="T5")))
    restarted = make_collector()  # App neu gestartet: T5 steckt noch, das ist kein neuer Aufruf
    feed(restarted, (100, snap("STARTED", tool="T5")), (110, snap("STARTED", tool="T9")))
    assert (calls(db, 1), calls(db, 5), calls(db, 9)) == (0, 1, 1)


def test_machines_are_counted_separately(db):
    m2 = MachineConfig(id="m2", name="Maschine 2", host="127.0.0.2")
    c1 = MachineCollector(MACHINE, NullAdapter(), db, poll_interval_s=2)
    c2 = MachineCollector(m2, NullAdapter(), db, poll_interval_s=2)
    for t, tool in ((0, "T1"), (10, "T5"), (20, "T1"), (30, "T5")):
        feed(c1, (t, snap("STARTED", tool=tool)))
    feed(c2, (0, snap("STARTED", tool="T1")), (10, snap("STARTED", tool="T5")))
    assert (calls(db, 5, "m1"), calls(db, 1, "m1"), calls(db, 5, "m2"), calls(db, 1, "m2")) == (2, 1, 1, 0)


def test_reset_and_delete_keep_calls(db, make_collector):
    """Aufrufe zählen über die ganze Erfassung: Zurücksetzen (neue Schneide) ändert nichts."""
    c = make_collector()
    feed(c, (0, snap("STARTED", tool="T1")), (10, snap("STARTED", tool="T5")), (20, snap("STARTED", tool="T1")))
    db.reset_tool("m1", 1, 30)
    assert calls(db, 1) == 1 and db.tool("m1", 1)["used_s"] == 0
    db.delete_tool("m1", 1)
    c.forget_tool(1)
    feed(c, (40, snap("STARTED", tool="T1")))  # neu angelegt; die bisherigen Aufrufe der T-Nummer bleiben
    assert calls(db, 1) == 1


# --- Nachtragen aus den bisherigen Werkzeugwechseln (Update auf Schema 9) ----------------------


def _old_database(path, events):
    """Datenbank wie in Version 1.8 (Schema 8): ohne tool_calls, mit Werkzeugwechseln als Ereignisse."""
    db = Database(path)
    db.ensure_machine("m1", "DMG 1", "10.0.0.1", 19000)
    db.ensure_machine("m2", "DMG 2", "10.0.0.2", 19000)
    for machine, number in (("m1", 1), ("m1", 5), ("m2", 5)):
        db.ensure_tool(machine, number, "", 0)
    db.close()
    con = sqlite3.connect(path)
    con.executescript("DROP TABLE tool_calls; UPDATE meta SET value = '8' WHERE key = 'schema_version';")
    con.execute("DELETE FROM meta WHERE key = 'tool_calls_backfill'")
    con.executemany(
        "INSERT INTO events(machine_id, ts, type, payload) VALUES (?, ?, 'tool_change', ?)",
        [(m, ts, json.dumps({"from": a, "to": b, "program": "P1"})) for m, ts, a, b in events],
    )
    con.execute("INSERT INTO events(machine_id, ts, type, payload) VALUES ('m1', 5, 'nc_error', '{}')")
    con.commit()
    con.close()


OLD_EVENTS = [
    ("m1", 10, "T1", "T5"),  # Aufruf T5
    ("m1", 20, "T5 FRAESER", "T5 SCHRUPPER"),  # nur Name anders: kein Aufruf
    ("m1", 30, "T5", "T0"),  # Spindel geleert
    ("m1", 40, "T0", "T5"),  # Aufruf T5
    ("m1", 50, "T5", "T1 BOHRER"),  # Aufruf T1
    ("m2", 60, "T1", "T5"),  # Aufruf T5 an Maschine 2
]


def test_backfill_after_update(tmp_path):
    path = tmp_path / "v8.db"
    _old_database(path, OLD_EVENTS)
    db = Database(path)
    assert db.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert db.get_meta("tool_calls_backfill") == "pending"
    assert tools.backfill_calls(db) == 4
    assert call_rows(db) == [("m1", 5, 10), ("m1", 5, 40), ("m1", 1, 50), ("m2", 5, 60)]
    assert (calls(db, 5), calls(db, 1), calls(db, 5, "m2")) == (2, 1, 1)
    assert tools.backfill_calls(db) == 0  # nur einmal
    db.close()
    db = Database(path)  # erneuter Start: kein zweites Nachtragen
    assert db.get_meta("tool_calls_backfill") == "done"
    assert tools.backfill_calls(db) == 0 and len(call_rows(db)) == 4
    db.close()


def test_app_start_backfills_once(tmp_path):
    path = tmp_path / "v8.db"
    _old_database(path, OLD_EVENTS)
    settings = Settings(machines=(), db_path=path, simulate=True)
    for _ in range(2):  # zweimal starten: gleiche Zahlen
        with TestClient(create_app(settings, run_collectors=False)) as c:
            rows = {(t["machine_id"], t["number"]): t["calls"] for t in c.get("/api/tools").json()["tools"]}
            assert rows == {("m1", 1): 1, ("m1", 5): 2, ("m2", 5): 1}


def test_new_database_is_never_backfilled(db, make_collector):
    """Eine Datenbank, die von Anfang an Aufrufe zählt, darf die Wechsel-Ereignisse nicht noch einmal zählen."""
    feed(make_collector(), (0, snap("STARTED", tool="T1")), (10, snap("STARTED", tool="T5")))
    assert [e["type"] for e in db.events(0, 100)].count("tool_change") == 1
    assert db.get_meta("tool_calls_backfill") is None
    assert tools.backfill_calls(db) == 0
    assert calls(db, 5) == 1


# --- API ----------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    settings = Settings(machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"),), db_path=tmp_path / "t.db", simulate=True)
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as c:
        col = app.state.ctx.collectors["m1"]
        for i, tool in enumerate(["T109", "T5", "T109", "T7", "T109", "T5"]):
            feed(col, (1000 + i * 60, snap("STARTED", tool=tool)))
        yield c


def test_api_lists_calls(client):
    rows = {t["number"]: t["calls"] for t in client.get("/api/tools").json()["tools"]}
    assert rows == {109: 2, 5: 2, 7: 1}  # das erste T109 war schon in der Spindel
    assert client.get("/api/tools/m1/109").json()["calls"] == 2


def test_csv_has_calls_and_total_runtime_as_last_columns(client):
    text = client.get("/api/tools/export.csv").content.decode("utf-8").lstrip("﻿")
    header, *rows = text.strip().split("\r\n")
    assert header.split(";")[-2:] == ["Aufrufe", "Laufzeit gesamt (h)"]
    # T109 dreimal je 60 s in der Spindel, T5 und T7 je 60 s (das letzte T5 läuft gerade erst)
    assert {r.split(";")[1]: r.split(";")[-2:] for r in rows} == {
        "T5": ["2", "0,02"], "T7": ["1", "0,02"], "T109": ["2", "0,05"],
    }


def test_total_runtime_is_not_reset(client):
    client.post("/api/tools/m1/109/reset")
    tool = client.get("/api/tools/m1/109").json()
    assert (tool["used_s"], tool["total_s"]) == (0, 180)  # Zurücksetzen: nur die Standzeit beginnt neu


# --- Werkzeugplätze: die meistgebrauchten Werkzeuge je Magazin ---------------------------------


def test_rank_by_calls_marks_top_per_machine():
    rows = [
        {"machine_id": "m1", "number": n, "calls": c}
        for n, c in ((1, 5), (2, 9), (3, 5), (4, 0), (5, 1))
    ] + [{"machine_id": "m2", "number": 1, "calls": 3}]
    tools.rank_by_calls(rows, {"m1": 3, "m2": None})
    got = {(r["machine_id"], r["number"]): (r["rank"], r["top"]) for r in rows}
    assert got == {
        ("m1", 2): (1, True),
        ("m1", 1): (2, True),  # Gleichstand mit T3: kleinere Nummer zuerst
        ("m1", 3): (3, True),
        ("m1", 5): (4, False),  # außerhalb der 3 Plätze
        ("m1", 4): (None, False),  # nie aufgerufen
        ("m2", 1): (1, False),  # keine Werkzeugplätze eingetragen: keine Markierung
    }


def machine_payload(**extra):
    return {"name": "DMG 1", "host": "10.0.0.1", "port": 19000, **extra}


def test_tool_slots_in_config_and_tool_list(client):
    assert client.get("/api/config").json()["machines"][0]["tool_slots"] is None
    r = client.put("/api/config/machines/m1", json=machine_payload(tool_slots="2"))
    assert r.status_code == 200 and r.json()["tool_slots"] == 2
    data = client.get("/api/tools").json()
    assert data["machines"][0]["tool_slots"] == 2  # wirkt sofort, ohne Neustart
    got = {t["number"]: (t["calls"], t["rank"], t["top"]) for t in data["tools"]}
    assert got == {5: (2, 1, True), 109: (2, 2, True), 7: (1, 3, False)}
    # leer = keine Angabe
    assert client.put("/api/config/machines/m1", json=machine_payload(tool_slots="")).json()["tool_slots"] is None
    assert not any(t["top"] for t in client.get("/api/tools").json()["tools"])


@pytest.mark.parametrize("value", [0, -1, 1001, "abc", "2,5", 2.5])
def test_tool_slots_validation(client, value):
    r = client.put("/api/config/machines/m1", json=machine_payload(tool_slots=value))
    assert r.status_code == 400
    assert "Werkzeugplätze" in r.json()["detail"]


def test_new_machine_with_tool_slots(client):
    r = client.post("/api/config/machines", json={"name": "DMU 70", "host": "10.0.0.9", "tool_slots": 60})
    assert r.status_code == 201 and r.json()["tool_slots"] == 60


def test_machines_table_gets_tool_slots_column(tmp_path):
    path = tmp_path / "alt.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES ('schema_version', '8');"
        "CREATE TABLE machines (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL,"
        " note TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0, image TEXT,"
        " removed INTEGER NOT NULL DEFAULT 0, check_host TEXT NOT NULL DEFAULT '');"
        "INSERT INTO machines(id, name, host, port) VALUES ('m1', 'DMG 1', '10.0.0.1', 19000);"
    )
    con.commit()
    con.close()
    db = Database(path)
    assert db.machine("m1")["tool_slots"] is None
    db.update_machine("m1", "DMG 1", "10.0.0.1", 19000, "", "", 30)
    assert db.machine("m1")["tool_slots"] == 30
    db.close()
