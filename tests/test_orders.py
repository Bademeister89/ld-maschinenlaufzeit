"""Aufträge aus Programmnamen (JJ-AUFTRAG-AUFSPANNUNG-PROGRAMM)."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app import orders
from app.adapters.sim_adapter import ORDER_SETUPS, PROGRAMS, SimulatedMachine
from app.config import MachineConfig, Settings
from app.main import create_app
from app.orders import parse_program

from .conftest import feed, snap

TZ = ZoneInfo("Europe/Berlin")
P11 = "TNC:\\AUFTRAG\\26-21055-01-01.H"
P12 = "TNC:\\AUFTRAG\\26-21055-01-02.H"
P21 = "TNC:\\AUFTRAG\\26-21055-02-01.H"
OTHER = "TNC:\\AUFTRAG\\26-4711-01-01.H"


# --- Namen erkennen ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        (P11, ("26-21055", 2026, "21055", 1, 1, "26-21055-01-01")),
        ("26-4711-02-03.H", ("26-4711", 2026, "4711", 2, 3, "26-4711-02-03")),
        ("TNC:/nc_prog/25-10001-01-12.h", ("25-10001", 2025, "10001", 1, 12, "25-10001-01-12")),
        ("26_21055_1_1.H", ("26-21055", 2026, "21055", 1, 1, "26_21055_1_1")),
        ("26-21055-01-01_Schlichten.H", ("26-21055", 2026, "21055", 1, 1, "26-21055-01-01_Schlichten")),
    ],
)
def test_parse_program(path, expected):
    code = parse_program(path)
    assert (code.key, code.year, code.order, code.setup, code.program, code.name) == expected


@pytest.mark.parametrize(
    "path",
    [None, "", "TNC:\\PROD\\GEHAEUSE_A12.H", "26-210-01-01.H", "26-210555-01-01.H", "2026-21055-01-01.H", "26-21055-01.H"],
)
def test_parse_program_rejects(path):
    assert parse_program(path) is None


# --- Erfassung ---------------------------------------------------------------------------


def run_part(c, t0, program, run_s, stop_s=0):
    """Ein vollständiger Lauf: angewählt, läuft, ggf. Stopp, fertig."""
    feed(c, (t0, snap("IDLE", program)), (t0 + 10, snap("STARTED", program)))
    t = t0 + 10 + run_s
    if stop_s:
        feed(c, (t, snap("STOPPED", program)))
        t += stop_s
        feed(c, (t, snap("STARTED", program)))
    feed(c, (t, snap("FINISHED", program)))
    return t


def test_collector_creates_order_and_tags_data(db, make_collector):
    c = make_collector()
    run_part(c, 0, P11, 300)
    [order] = db.orders()
    assert (order["key"], order["year"], order["number"], order["status"]) == ("26-21055", 2026, "21055", "open")
    assert order["created_at"] == 0
    [run] = db.runs(0, 1000)
    assert db._query("SELECT order_key FROM program_runs")[0]["order_key"] == "26-21055"
    keys = {r["order_key"] for r in db._query("SELECT order_key FROM state_intervals")}
    assert keys == {"26-21055"}
    types = [e["type"] for e in db.events(0, 1000)]
    assert types.count("order_created") == 1
    assert c.live()["order"]["setup"] == 1


def test_programs_without_code_have_no_order(db, make_collector):
    c = make_collector()
    run_part(c, 0, "TNC:\\PROD\\GEHAEUSE_A12.H", 100)
    assert db.orders() == []
    assert c.live()["order"] is None


def test_closed_order_reopens_when_it_runs_again(db, make_collector):
    c = make_collector()
    run_part(c, 0, P11, 100)
    db.update_order("26-21055", "Flansch", "closed", 500)
    assert db.order("26-21055")["closed_at"] == 500
    feed(c, (600, snap("IDLE", P11)))  # nur angewählt: bleibt abgeschlossen
    assert db.order("26-21055")["status"] == "closed"
    feed(c, (700, snap("STARTED", P11)))
    order = db.order("26-21055")
    assert (order["status"], order["closed_at"], order["title"]) == ("open", None, "Flansch")


def test_order_totals_count_only_run_time(db, make_collector):
    c = make_collector()
    t = run_part(c, 0, P11, 300, stop_s=60)  # 300 s Laufzeit + 60 s Stopp
    t = run_part(c, t + 100, P12, 200)
    t = run_part(c, t + 100, P21, 400)
    # Programm bleibt danach lange angewählt – zählt nicht zum Auftrag
    feed(c, (t + 50_000, snap("FINISHED", P21)))
    run_part(c, t + 60_000, OTHER, 50)

    [row] = [r for r in orders.list_orders(db) if r["key"] == "26-21055"]
    assert row["running_s"] == pytest.approx(900)
    assert row["stopped_s"] == pytest.approx(60)
    assert (row["runs"], row["finished"], row["programs"], row["setups"], row["machines"]) == (3, 3, 3, [1, 2], ["m1"])
    assert row["first_activity"] == 10
    assert row["last_activity"] < t + 1
    assert [r["key"] for r in orders.list_orders(db)] == ["26-4711", "26-21055"]  # zuletzt aktiv zuerst
    db.update_order("26-4711", "", "closed", 1)
    assert [r["key"] for r in orders.list_orders(db, "open")] == ["26-21055"]
    assert [r["key"] for r in orders.list_orders(db, "closed")] == ["26-4711"]


def test_order_detail_by_setup_and_program(db, make_collector):
    c = make_collector()
    t = 0
    for _ in range(2):  # zwei Teile: je Teil Programm 01 und 02 in Aufspannung 1
        t = run_part(c, t + 10, P11, 300)
        t = run_part(c, t + 10, P12, 100)
    t = run_part(c, t + 10, P21, 250)
    detail = orders.order_detail(db, "26-21055", TZ)
    assert detail["order"]["key"] == "26-21055"
    s1, s2 = detail["setups"]
    assert (s1["setup"], [p["name"] for p in s1["programs"]]) == (1, ["26-21055-01-01", "26-21055-01-02"])
    assert s1["running_s"] == pytest.approx(800)
    assert s1["part_run_s"] == pytest.approx(400)  # Ø je Teil: 300 + 100
    assert s2["part_run_s"] == pytest.approx(250)
    assert detail["totals"]["part_run_s"] == pytest.approx(650)
    assert detail["totals"]["part_complete"] is True
    assert (detail["totals"]["runs"], detail["totals"]["finished"]) == (5, 5)
    assert len(detail["runs"]) == 5
    assert sum(d["running_s"] for d in detail["days"]) == pytest.approx(1050)
    assert orders.order_detail(db, "99-99999", TZ) is None


def test_order_days_split_at_midnight(db, make_collector):
    c = make_collector()
    start = datetime(2026, 9, 21, 23, 0, tzinfo=TZ).timestamp()
    feed(c, (start, snap("IDLE", P11)), (start + 1800, snap("STARTED", P11)), (start + 5400, snap("FINISHED", P11)))
    days = orders.order_detail(db, "26-21055", TZ)["days"]
    assert [(d["date"], d["running_s"]) for d in days] == [("2026-09-21", 1800), ("2026-09-22", 1800)]


def test_backfill_assigns_existing_data(db, make_collector):
    make_collector()  # legt die Maschine an
    # Daten aus der Zeit vor der Auftragsauswertung (ohne order_key)
    run_id = db.start_run("m1", P11, 100, True)
    iv = db.open_interval("m1", "RUNNING", "STARTED", "AUTOMATIC", P11, run_id, 100, 100)
    db.close_interval(iv, 400)
    db.end_run(run_id, 400, "finished")
    db.open_interval("m1", "READY", "IDLE", "MANUAL", "TNC:\\PROD\\ALT.H", None, 400, 500)
    assert db.orders() == []

    assert orders.backfill(db) == 1
    [order] = db.orders()
    assert (order["key"], order["created_at"]) == ("26-21055", 100)
    [row] = orders.list_orders(db)
    assert (row["running_s"], row["finished"]) == (300, 1)
    assert orders.backfill(db) == 0  # idempotent


# --- API ---------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    settings = Settings(machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"),), db_path=tmp_path / "o.db", simulate=True)
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as c:
        col = app.state.ctx.collectors["m1"]
        t = run_part(col, 1000, P11, 300)
        feed(col, (t + 10, snap("IDLE", P12)), (t + 20, snap("STARTED", P12, line_no=5)))
        yield c


def test_api_list_and_detail(client):
    [row] = client.get("/api/orders").json()["orders"]
    assert row["key"] == "26-21055"
    assert row["active"][0]["machine"] == "DMG 1"
    assert row["active"][0]["program"] == "26-21055-01-02"
    detail = client.get("/api/orders/26-21055").json()
    assert detail["totals"]["finished"] == 1
    assert detail["active"][0]["state"] == "RUNNING"
    assert client.get("/api/orders/99-1").status_code == 404
    assert client.get("/api/orders?status=bogus").status_code == 422
    live = client.get("/api/machines").json()["machines"][0]
    assert live["order"]["key"] == "26-21055"


def test_api_update(client):
    r = client.put("/api/orders/26-21055", json={"title": "  Gehäuse Kunde Müller ", "status": "closed"})
    assert r.status_code == 200
    assert (r.json()["title"], r.json()["status"]) == ("Gehäuse Kunde Müller", "closed")
    assert client.get("/api/orders?status=closed").json()["orders"][0]["key"] == "26-21055"
    assert client.put("/api/orders/26-21055", json={"status": "weg"}).status_code == 400
    assert client.put("/api/orders/26-21055", json={"title": "x" * 121}).status_code == 400
    assert client.put("/api/orders/99-1", json={}).status_code == 404


def test_api_export(client):
    r = client.get("/api/orders/26-21055/export.csv")
    lines = r.content.decode("utf-8").lstrip("﻿").strip().split("\r\n")
    assert lines[0].split(";")[:4] == ["Auftrag", "Aufspannung", "Programm", "Lauf-Nr."]
    first = lines[1].split(";")
    assert (first[0], first[1], first[2], first[7], first[8]) == ("26-21055", "1", "26-21055-01-01", "fertig", "5,00")


def test_meta_has_version_and_page(client):
    assert client.get("/api/meta").json()["version"]
    assert client.get("/api/config").json()["settings"]["version"]
    assert "Aufträge" in client.get("/auftraege.html").text


# --- Simulation --------------------------------------------------------------------------


def test_simulation_uses_order_scheme():
    assert all(parse_program(p) for p in PROGRAMS)
    assert any(parse_program(p).order == "4711" for p in PROGRAMS)  # auch 4-stellige Nummern
    sim = SimulatedMachine(seed=5, start=0, speed=1.0)
    seen = []
    t = 0.0
    while t < 24 * 3600:
        s = sim.state_at(t)
        if s and s.pgm_state == "STARTED" and (not seen or seen[-1] != s.program):
            seen.append(s.program)
        t = sim.next_change
    codes = [parse_program(p) for p in seen]
    # Innerhalb eines Auftrags werden die Aufspannungen der Reihe nach gefahren
    for prev, cur in zip(codes, codes[1:]):
        if prev.key == cur.key and cur.setup != prev.setup:
            assert cur.setup == prev.setup + 1 or cur.setup == 1
    assert len({c.key for c in codes}) >= 2
    assert set(ORDER_SETUPS) >= {c.key for c in codes}


def test_update_from_schema_v4_database(tmp_path):
    """Datenbank eines laufenden Containers (Schema 4, ohne Auftragsspalten) nach dem Update."""
    import sqlite3

    path = tmp_path / "v4.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES ('schema_version', '4');"
        "CREATE TABLE machines (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL,"
        " note TEXT NOT NULL DEFAULT '', sort_order INTEGER NOT NULL DEFAULT 0, image TEXT,"
        " removed INTEGER NOT NULL DEFAULT 0, check_host TEXT NOT NULL DEFAULT '');"
        "INSERT INTO machines(id, name, host, port) VALUES ('m1', 'DMG 1', '10.0.0.1', 19000);"
        "CREATE TABLE program_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, machine_id TEXT NOT NULL, program TEXT,"
        " started_at REAL NOT NULL, ended_at REAL, result TEXT, had_error INTEGER NOT NULL DEFAULT 0,"
        " start_observed INTEGER NOT NULL DEFAULT 1);"
        "CREATE TABLE state_intervals (id INTEGER PRIMARY KEY AUTOINCREMENT, machine_id TEXT NOT NULL, state TEXT NOT NULL,"
        " pgm_state TEXT, exec_mode TEXT, program TEXT, run_id INTEGER, started_at REAL NOT NULL, ended_at REAL,"
        " last_seen REAL NOT NULL);"
        "INSERT INTO program_runs VALUES (1, 'm1', 'TNC:/AUFTRAG/26-21055-01-01.H', 100, 400, 'finished', 0, 1);"
        "INSERT INTO state_intervals VALUES (1, 'm1', 'RUNNING', 'STARTED', 'AUTOMATIC',"
        " 'TNC:/AUFTRAG/26-21055-01-01.H', 1, 100, 400, 400);"
        "INSERT INTO state_intervals VALUES (2, 'm1', 'READY', 'IDLE', 'MANUAL', 'TNC:/PROD/ALT.H', NULL, 400, NULL, 500);"
    )
    con.commit()
    con.close()

    settings = Settings(machines=(), db_path=path, simulate=True)
    with TestClient(create_app(settings, run_collectors=False)) as c:
        [row] = c.get("/api/orders").json()["orders"]
        assert (row["key"], row["running_s"], row["finished"]) == ("26-21055", 300, 1)
        assert c.get("/api/machines").json()["machines"][0]["name"] == "DMG 1"
