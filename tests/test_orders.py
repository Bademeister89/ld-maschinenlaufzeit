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
        (P11, ("21055", 2026, "21055", 1, 1, "26-21055-01-01")),
        ("26-4711-02-03.H", ("4711", 2026, "4711", 2, 3, "26-4711-02-03")),
        ("TNC:/nc_prog/25-10001-01-12.h", ("10001", 2025, "10001", 1, 12, "25-10001-01-12")),
        ("26_21055_1_1.H", ("21055", 2026, "21055", 1, 1, "26_21055_1_1")),
        ("26-21055-01-01_Schlichten.H", ("21055", 2026, "21055", 1, 1, "26-21055-01-01_Schlichten")),
    ],
)
def test_parse_program(path, expected):
    code = parse_program(path)
    assert (code.key, code.year, code.order, code.setup, code.program, code.name) == expected
    assert code.version == ""


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # Versionen gehören zum Grundauftrag
        ("TNC:\\AUFTRAG\\26-21053V1-01-01.H", ("21053", "21053", "V1", 1, 1, "26-21053V1-01-01")),
        ("26-21053V2-02-03.H", ("21053", "21053", "V2", 2, 3, "26-21053V2-02-03")),
        ("26-21053v2-01-01.h", ("21053", "21053", "V2", 1, 1, "26-21053v2-01-01")),  # klein = groß
        ("26-4711V12-01-01_Schlichten.H", ("4711", "4711", "V12", 1, 1, "26-4711V12-01-01_Schlichten")),
        ("26_21053V1_1_1.H", ("21053", "21053", "V1", 1, 1, "26_21053V1_1_1")),
    ],
)
def test_parse_program_version(path, expected):
    code = parse_program(path)
    assert (code.key, code.order, code.version, code.setup, code.program, code.name) == expected


@pytest.mark.parametrize(
    "path",
    [
        None, "", "TNC:\\PROD\\GEHAEUSE_A12.H", "26-210-01-01.H", "26-210555-01-01.H", "2026-21055-01-01.H", "26-21055-01.H",
        "26-21053X1-01-01.H",  # nur V kennzeichnet eine Version
        "26-21053V-01-01.H",  # Version ohne Nummer
        "26-21053V123-01-01.H",  # höchstens zweistellig
        "26-21053-V1-01-01.H",  # Version gehört direkt an die Nummer
        "26-21053V1-01.H",
    ],
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
    assert (order["key"], order["year"], order["number"], order["status"]) == ("21055", 2026, "21055", "open")
    assert order["created_at"] == 0
    [run] = db.runs(0, 1000)
    assert db._query("SELECT order_key FROM program_runs")[0]["order_key"] == "21055"
    keys = {r["order_key"] for r in db._query("SELECT order_key FROM state_intervals")}
    assert keys == {"21055"}
    types = [e["type"] for e in db.events(0, 1000)]
    assert types.count("order_created") == 1
    assert c.live()["order"]["setup"] == 1


def test_versions_belong_to_the_base_order(db, make_collector):
    """Versionen sind andere Ausführungen des Teils: ein Auftrag, Laufzeit über alle, Ø je Teil je Version."""
    c = make_collector()
    t = run_part(c, 0, P11, 300)
    t = run_part(c, t + 10, "TNC:\\AUFTRAG\\26-21055V1-01-01.H", 200)
    t = run_part(c, t + 10, "TNC:\\AUFTRAG\\26-21055V2-01-01.H", 100)
    run_part(c, t + 10, "TNC:\\AUFTRAG\\26-21055v2-02-01.H", 50)  # kleines v: gleiche Version
    [row] = orders.list_orders(db)
    assert (row["key"], row["number"], row["running_s"], row["finished"]) == ("21055", "21055", 650, 4)
    assert (row["versions"], row["setups"]) == (["V1", "V2"], [1, 2])
    live = c.live()["order"]
    assert (live["key"], live["version"]) == ("21055", "V2")
    detail = orders.order_detail(db, "21055", TZ)
    blocks = [(v["version"], [s["setup"] for s in v["setups"]], v["part_run_s"], v["running_s"]) for v in detail["versions"]]
    assert blocks == [("", [1], 300, 300), ("V1", [1], 200, 200), ("V2", [1, 2], 150, 150)]
    # Mehrere Ausführungen: keine gemeinsame Ø-Zeit je Teil, dafür je Version
    assert (detail["totals"]["running_s"], detail["totals"]["part_run_s"], detail["totals"]["part_complete"]) == (650, None, False)
    assert [s["version"] for s in detail["setups"]] == ["", "V1", "V2", "V2"]


def test_year_in_the_program_name_does_not_split_the_order(db, make_collector):
    """DMU 70, 7.10.: 21-21053v1-02-01 (Jahr 21) gehört wie 26-21053-01-01 zum Auftrag 21053."""
    c = make_collector()
    t = run_part(c, 0, "TNC:\\AUFTRAG\\26-21053-01-01.H", 300)
    run_part(c, t + 10, "TNC:\\AUFTRAG\\21-21053v1-02-01.h", 200)
    [row] = orders.list_orders(db)
    assert (row["key"], row["running_s"], row["versions"]) == ("21053", 500, ["V1"])
    blocks = [(v["version"], [s["setup"] for s in v["setups"]]) for v in orders.order_detail(db, "21053", TZ)["versions"]]
    assert blocks == [("", [1]), ("V1", [2])]


def test_copy_of_a_program_in_another_folder_is_one_program(db, make_collector):
    """DMU 70, 7.10.: 26-21051-02-01.h lief auch aus dem Ordner von 21053 – bisher zwei Zeilen."""
    from app import stats

    original = "TNC:\\Programme\\21051 Kupplungsdeckel CVO\\26-21051-02-01.h"
    copy = "TNC:\\Programme\\21053 abdeckung rechts 1 cvo\\26-21051-02-01.h"
    c = make_collector()
    t = run_part(c, 0, original, 300)
    t = run_part(c, t + 10, original, 500)
    run_part(c, t + 10, copy, 400)
    [setup] = orders.order_detail(db, "21051", TZ)["setups"]
    [program] = setup["programs"]
    assert (program["name"], program["runs"], program["finished"], program["avg_run_s"]) == ("26-21051-02-01", 3, 3, 400)
    assert program["paths"] == [original, copy]
    assert orders.list_orders(db)[0]["programs"] == 1
    [row] = stats.programs(db, db.intervals(0, 10_000), 0, 10_000)
    assert (row["runs"], row["running_s"], row["avg_run_s"], len(row["paths"])) == (3, 1200, 400, 2)


def test_update_merges_orders_of_different_years(tmp_path):
    """Bis 1.17.0 war das Jahr Teil des Schlüssels (21-21053 und 26-21053 getrennt)."""
    from app.db import Database

    path = tmp_path / "v13.db"
    db = Database(path)
    db.ensure_machine("m1", "DMG 1", "10.0.0.1", 19000)
    for key, t in (("21-21053", 300.0), ("26-21053", 100.0), ("26-4711", 50.0)):
        db.ensure_order(key, 2000 + int(key[:2]), key[3:], t)
        run_id = db.start_run("m1", f"TNC:\\{key}-01-01.H", t, True, key)
        iv = db.open_interval("m1", "RUNNING", "STARTED", "AUTOMATIC", f"TNC:\\{key}-01-01.H", run_id, t, t + 10, order_key=key)
        db.close_interval(iv, t + 10)
        db.end_run(run_id, t + 10, "finished")
    db.update_order("26-21053", "Abdeckung rechts", "open", 0.0)
    db._execute("DELETE FROM meta WHERE key = 'order_years_merged'")
    db.close()

    db = Database(path)
    assert sorted(o["key"] for o in db.orders()) == ["21053", "4711"]
    merged = db.order("21053")
    assert (merged["title"], merged["number"], merged["created_at"]) == ("Abdeckung rechts", "21053", 100.0)
    rows = {r["key"]: (r["running_s"], r["runs"]) for r in orders.list_orders(db)}
    assert rows == {"21053": (20, 2), "4711": (10, 1)}
    db.close()


def test_versions_are_sorted_by_number(db, make_collector):
    c = make_collector()
    t = 0
    for version in ("V10", "V2", ""):
        t = run_part(c, t + 10, f"TNC:\\AUFTRAG\\26-21055{version}-01-01.H", 100)
    assert [v["version"] for v in orders.order_detail(db, "21055", TZ)["versions"]] == ["", "V2", "V10"]
    assert orders.list_orders(db)[0]["versions"] == ["V2", "V10"]


def test_update_merges_version_orders_into_the_base_order(tmp_path):
    """Bis 1.16.0 waren Versionen eigene Aufträge (Schema 12)."""
    import sqlite3

    from app.db import Database

    path = tmp_path / "v12.db"
    db = Database(path)
    db.ensure_machine("m1", "DMG 1", "10.0.0.1", 19000)
    for key, number, t in (("26-21055", "21055", 100.0), ("26-21055V1", "21055V1", 50.0), ("26-21053V2", "21053V2", 70.0)):
        db.ensure_order(key, 2026, number, t)
        run_id = db.start_run("m1", f"TNC:\\{number}.H", t, True, key)
        iv = db.open_interval("m1", "RUNNING", "STARTED", "AUTOMATIC", f"TNC:\\{number}.H", run_id, t, t + 10, order_key=key)
        db.close_interval(iv, t + 10)
        db.end_run(run_id, t + 10, "finished")
    db.update_order("26-21055", "", "closed", 500.0)
    db.update_order("26-21055V1", "Kunde X", "open", 0.0)
    db.set_order_image("26-21055V1", "26-21055V1-abc.jpg")
    db._execute("DELETE FROM meta WHERE key IN ('order_versions_merged', 'order_years_merged')")
    db.close()
    con = sqlite3.connect(path)
    con.execute("UPDATE meta SET value = '12' WHERE key = 'schema_version'")
    con.commit()
    con.close()

    db = Database(path)
    assert db.get_meta("schema_version") == "14"
    assert sorted(o["key"] for o in db.orders()) == ["21053", "21055"]  # ohne Jahr und Version
    merged = db.order("21055")
    assert (merged["title"], merged["image"], merged["status"], merged["created_at"]) == ("Kunde X", "26-21055V1-abc.jpg", "open", 50.0)
    renamed = db.order("21053")  # Grundauftrag gab es nicht: die Version wird zu ihm
    assert (renamed["number"], renamed["created_at"]) == ("21053", 70.0)
    keys = {r["order_key"] for r in db._query("SELECT order_key FROM program_runs UNION SELECT order_key FROM state_intervals")}
    assert keys == {"21055", "21053"}
    rows = {r["key"]: (r["running_s"], r["runs"]) for r in orders.list_orders(db)}
    assert rows == {"21055": (20, 2), "21053": (10, 1)}
    db.close()


def test_backfill_assigns_versions_recorded_before_update(db, make_collector):
    """Vor dem Update wurden Versionen nicht erkannt: ihre Läufe stehen ohne Auftrag in der Datenbank."""
    make_collector()
    program = "TNC:\\AUFTRAG\\26-21053V1-01-01.H"
    run_id = db.start_run("m1", program, 100, True)  # ohne order_key, wie bisher erfasst
    iv = db.open_interval("m1", "RUNNING", "STARTED", "AUTOMATIC", program, run_id, 100, 100)
    db.close_interval(iv, 400)
    db.end_run(run_id, 400, "finished")
    assert db.orders() == []
    assert orders.backfill(db) == 1
    [row] = orders.list_orders(db)
    assert (row["key"], row["number"], row["running_s"], row["finished"], row["versions"]) == ("21053", "21053", 300, 1, ["V1"])


def test_programs_without_code_have_no_order(db, make_collector):
    c = make_collector()
    run_part(c, 0, "TNC:\\PROD\\GEHAEUSE_A12.H", 100)
    assert db.orders() == []
    assert c.live()["order"] is None


def test_closed_order_reopens_when_it_runs_again(db, make_collector):
    c = make_collector()
    run_part(c, 0, P11, 100)
    db.update_order("21055", "Flansch", "closed", 500)
    assert db.order("21055")["closed_at"] == 500
    feed(c, (600, snap("IDLE", P11)))  # nur angewählt: bleibt abgeschlossen
    assert db.order("21055")["status"] == "closed"
    feed(c, (700, snap("STARTED", P11)))
    order = db.order("21055")
    assert (order["status"], order["closed_at"], order["title"]) == ("open", None, "Flansch")


def test_order_totals_count_only_run_time(db, make_collector):
    c = make_collector()
    t = run_part(c, 0, P11, 300, stop_s=60)  # 300 s Laufzeit + 60 s Stopp
    t = run_part(c, t + 100, P12, 200)
    t = run_part(c, t + 100, P21, 400)
    # Programm bleibt danach lange angewählt – zählt nicht zum Auftrag
    feed(c, (t + 50_000, snap("FINISHED", P21)))
    run_part(c, t + 60_000, OTHER, 50)

    [row] = [r for r in orders.list_orders(db) if r["key"] == "21055"]
    assert row["running_s"] == pytest.approx(900)
    assert row["stopped_s"] == pytest.approx(60)
    assert (row["runs"], row["finished"], row["programs"], row["setups"], row["machines"]) == (3, 3, 3, [1, 2], ["m1"])
    assert row["first_activity"] == 10
    assert row["last_activity"] < t + 1
    assert [r["key"] for r in orders.list_orders(db)] == ["4711", "21055"]  # zuletzt aktiv zuerst
    db.update_order("4711", "", "closed", 1)
    assert [r["key"] for r in orders.list_orders(db, "open")] == ["21055"]
    assert [r["key"] for r in orders.list_orders(db, "closed")] == ["4711"]


def test_order_detail_by_setup_and_program(db, make_collector):
    c = make_collector()
    t = 0
    for _ in range(2):  # zwei Teile: je Teil Programm 01 und 02 in Aufspannung 1
        t = run_part(c, t + 10, P11, 300)
        t = run_part(c, t + 10, P12, 100)
    t = run_part(c, t + 10, P21, 250)
    detail = orders.order_detail(db, "21055", TZ)
    assert detail["order"]["key"] == "21055"
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
    assert orders.order_detail(db, "99999", TZ) is None


def test_fixture_setups_08_09_do_not_count_per_part(db, make_collector):
    """Spannung 08 und 09 sind Vorrichtungsbau: Laufzeit des Auftrags ja, Ø-Zeit je Teil nein."""
    c = make_collector()
    t = run_part(c, 0, "TNC:\\AUFTRAG\\26-21055-08-01.H", 100)  # Vorrichtung zuerst gebaut
    t = run_part(c, t + 10, "TNC:\\AUFTRAG\\26-21055-09-01.H", 50)
    t = run_part(c, t + 10, P11, 300)
    run_part(c, t + 10, P21, 200)
    detail = orders.order_detail(db, "21055", TZ)
    assert [(s["setup"], s["fixture"]) for s in detail["setups"]] == [(1, False), (2, False), (8, True), (9, True)]
    totals = detail["totals"]
    assert totals["part_run_s"] == pytest.approx(500)  # nur Spannung 1 + 2
    assert totals["part_complete"] is True
    assert totals["fixture_s"] == pytest.approx(150)
    assert totals["running_s"] == pytest.approx(650)  # Gesamtaufwand inkl. Vorrichtung

    # Auftrag, von dem bisher nur die Vorrichtung gebaut wurde: noch keine Ø-Zeit je Teil
    run_part(c, 10_000, "TNC:\\AUFTRAG\\26-4711-08-01.H", 120)
    only_fixture = orders.order_detail(db, "4711", TZ)["totals"]
    assert (only_fixture["part_run_s"], only_fixture["part_complete"]) == (None, False)
    assert only_fixture["fixture_s"] == pytest.approx(120)


def test_order_days_split_at_midnight(db, make_collector):
    c = make_collector()
    start = datetime(2026, 9, 21, 23, 0, tzinfo=TZ).timestamp()
    feed(c, (start, snap("IDLE", P11)), (start + 1800, snap("STARTED", P11)), (start + 5400, snap("FINISHED", P11)))
    days = orders.order_detail(db, "21055", TZ)["days"]
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
    assert (order["key"], order["created_at"]) == ("21055", 100)
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
    assert row["key"] == "21055"
    assert row["active"][0]["machine"] == "DMG 1"
    assert row["active"][0]["program"] == "26-21055-01-02"
    detail = client.get("/api/orders/21055").json()
    assert detail["totals"]["finished"] == 1
    assert detail["active"][0]["state"] == "RUNNING"
    assert client.get("/api/orders/99-1").status_code == 404
    assert client.get("/api/orders?status=bogus").status_code == 422
    live = client.get("/api/machines").json()["machines"][0]
    assert live["order"]["key"] == "21055"


def test_api_update(client):
    r = client.put("/api/orders/21055", json={"title": "  Gehäuse Kunde Müller ", "status": "closed"})
    assert r.status_code == 200
    assert (r.json()["title"], r.json()["status"]) == ("Gehäuse Kunde Müller", "closed")
    assert client.get("/api/orders?status=closed").json()["orders"][0]["key"] == "21055"
    assert client.put("/api/orders/21055", json={"status": "weg"}).status_code == 400
    assert client.put("/api/orders/21055", json={"title": "x" * 121}).status_code == 400
    assert client.put("/api/orders/99-1", json={}).status_code == 404


def test_api_export(client):
    r = client.get("/api/orders/21055/export.csv")
    lines = r.content.decode("utf-8").lstrip("﻿").strip().split("\r\n")
    assert lines[0].split(";")[:5] == ["Auftrag", "Version", "Aufspannung", "Programm", "Lauf-Nr."]
    first = lines[1].split(";")
    assert (first[0], first[1], first[2], first[3], first[8], first[9]) == ("21055", "", "1", "26-21055-01-01", "fertig", "5,00")


def test_delete_run_removes_it_from_order_but_keeps_machine_time(client):
    db = client.app.state.ctx.db
    finished, running = sorted(r["id"] for r in db.order_runs("21055"))
    stats_before = client.get("/api/stats", params={"from": 0}).json()
    machine_running = stats_before["machines"]["m1"]["totals"]["RUNNING"]
    assert client.get("/api/orders/21055").json()["totals"]["running_s"] == pytest.approx(300 + 0)

    r = client.delete(f"/api/orders/21055/runs/{finished}")
    assert r.status_code == 204
    detail = client.get("/api/orders/21055").json()
    assert [run["id"] for run in detail["runs"]] == [running]
    assert (detail["totals"]["finished"], detail["totals"]["running_s"]) == (0, 0)
    assert detail["totals"]["part_complete"] is False  # kein fertiger Lauf mehr für die Ø-Zeit
    [row] = client.get("/api/orders").json()["orders"]
    assert (row["runs"], row["finished"], row["running_s"]) == (1, 0, 0)
    assert len(client.get("/api/orders/21055/export.csv").content.decode("utf-8").strip().split("\r\n")) == 2

    # Die Maschine ist trotzdem gelaufen: Maschinenzeit bleibt, der Lauf fehlt in den Stückzeiten
    stats = client.get("/api/stats", params={"from": 0}).json()
    assert stats["machines"]["m1"]["totals"]["RUNNING"] == pytest.approx(machine_running)
    assert not [p for p in stats["programs"] if p["program"] == P11 and p["runs"]]
    assert db.reference_runs("m1", [P11], 5) == []

    [event] = [e for e in db.events(0, 1e10) if e["type"] == "run_deleted"]
    assert event["payload"]["run"] == finished
    assert (event["payload"]["program"], event["payload"]["result"], event["payload"]["run_s"]) == (P11, "finished", 300)


def test_delete_run_checks(client):
    db = client.app.state.ctx.db
    finished, running = sorted(r["id"] for r in db.order_runs("21055"))
    assert client.delete(f"/api/orders/21055/runs/{running}").status_code == 409  # läuft noch
    assert client.delete("/api/orders/21055/runs/99999").status_code == 404
    assert client.delete(f"/api/orders/99-1/runs/{finished}").status_code == 404
    db.ensure_order("4711", 2026, "4711", 0)
    r = client.delete(f"/api/orders/4711/runs/{finished}")  # gehört zu einem anderen Auftrag
    assert r.status_code == 404 and "gehört nicht" in r.json()["detail"]
    assert len(db.order_runs("21055")) == 2  # nichts gelöscht


def test_deleted_run_is_not_resumed(db, make_collector):
    """Der Collector merkt sich den letzten Lauf für einen Satzvorlauf – nach dem Löschen nicht mehr."""
    c = make_collector()
    run_part(c, 0, P11, 300)
    [ended] = c._ended
    c.forget_run(ended.id + 1, P11)  # ein anderer Lauf: bleibt
    assert list(c._ended) == [ended]
    c.forget_run(ended.id, P11)
    assert not c._ended


DAY = 86_400


def status(db, key):
    order = db.order(key)
    return order["status"], order["closed_auto"]


def test_orders_close_after_7_days_without_run(db, make_collector):
    c = make_collector()
    end = run_part(c, 0, P11, 300)
    feed(c, (end + 10, snap("IDLE", OTHER)), (end + 20, snap("STARTED", OTHER)))  # 4711 läuft noch (offen)
    assert orders.close_idle(db, end + 7 * DAY - 60) == []  # noch keine 7 Tage
    assert orders.close_idle(db, end + 7 * DAY + 60) == ["21055"]
    assert status(db, "21055") == ("closed", 1)
    assert db.order("21055")["closed_at"] == end + 7 * DAY + 60
    assert status(db, "4711") == ("open", 0)  # laufender Lauf hält den Auftrag offen


def test_manually_reopened_order_gets_7_new_days(db, make_collector):
    c = make_collector()
    end = run_part(c, 0, P11, 300)
    orders.close_idle(db, end + 8 * DAY)
    db.update_order("21055", "", "open", end + 9 * DAY)  # von Hand wieder geöffnet
    assert status(db, "21055") == ("open", 0)
    assert orders.close_idle(db, end + 10 * DAY) == []
    assert orders.close_idle(db, end + 16 * DAY + 1) == ["21055"]
    db.update_order("21055", "Kunde X", "closed", end + 17 * DAY)  # nur Bezeichnung geändert
    assert status(db, "21055") == ("closed", 1)


def test_auto_closed_order_reopens_when_it_runs_again(db, make_collector):
    c = make_collector()
    end = run_part(c, 0, P11, 300)
    orders.close_idle(db, end + 8 * DAY)
    feed(c, (end + 9 * DAY, snap("IDLE", P11)))  # nur angewählt: bleibt zu
    assert status(db, "21055") == ("closed", 1)
    run_part(c, end + 9 * DAY + 100, P11, 300)
    order = db.order("21055")
    assert (order["status"], order["closed_auto"], order["opened_at"]) == ("open", 0, end + 9 * DAY + 110)


def test_update_adds_auto_close_columns(tmp_path):
    """Datenbank wie in Version 1.15.1 (Schema 11): Aufträge ohne opened_at/closed_auto."""
    import sqlite3

    from app.db import Database

    path = tmp_path / "v11.db"
    db = Database(path)
    db.ensure_order("21055", 2026, "21055", 100.0)
    db.close()
    con = sqlite3.connect(path)
    con.executescript(
        "ALTER TABLE orders DROP COLUMN opened_at; ALTER TABLE orders DROP COLUMN closed_auto; "
        "UPDATE meta SET value = '11' WHERE key = 'schema_version';"
    )
    con.close()
    db = Database(path)
    order = db.order("21055")
    assert (order["opened_at"], order["closed_auto"], db.get_meta("schema_version")) == (None, 0, "14")
    assert orders.close_idle(db, 100 + 7 * DAY + 1) == ["21055"]  # ab dem Anlegen gerechnet
    db.close()


def test_order_without_runs_closes_7_days_after_creation(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE", P11)))  # nur angewählt, nie gelaufen
    assert orders.close_idle(db, 7 * DAY + 1) == ["21055"]


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
        if (prev.key, prev.version) == (cur.key, cur.version) and cur.setup != prev.setup:
            assert cur.setup == prev.setup + 1 or cur.setup == 1
    assert len({c.key for c in codes}) >= 2
    assert {k.split("-")[1].removesuffix("V1") for k in ORDER_SETUPS} >= {c.key for c in codes}


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
        assert (row["key"], row["running_s"], row["finished"]) == ("21055", 300, 1)
        assert c.get("/api/machines").json()["machines"][0]["name"] == "DMG 1"


# --- Felgen: BBDDBBZZ-SS[ Zusatz] -------------------------------------------------------------

RIM_DIR = "TNC:/Felgen/10-999/"
RIM_1 = RIM_DIR + "10101018-01.h"
RIM_1_POCKET = RIM_DIR + "10101018-01 tasche.h"
RIM_1_ARM = RIM_DIR + "10101018-01 einarm.h"
RIM_2 = RIM_DIR + "10101018-02.h"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("10101018-01.h", ("10101018", 1, "", "10101018-01")),
        ("10101018-01 tasche.h", ("10101018", 1, "tasche", "10101018-01 tasche")),
        ("10101018-01 einarm.h", ("10101018", 1, "einarm", "10101018-01 einarm")),
        ("10101018-01normal.h", ("10101018", 1, "normal", "10101018-01normal")),
        ("10101018-02 tasche.h", ("10101018", 2, "tasche", "10101018-02 tasche")),
        ("11208520-01.H", ("11208520", 1, "", "11208520-01")),  # dreiteilig, Z06, 8,5 × 20
    ],
)
def test_parse_rim_program(name, expected):
    code = parse_program(RIM_DIR + name)
    assert (code.key, code.setup, code.variant, code.name) == expected
    assert (code.kind, code.year, code.order, code.program) == ("rim", 0, expected[0], 0)


@pytest.mark.parametrize("name", ["1301201.h", "1010101-01.h", "101010180-01.h", "10101018.h", "20101018-01.h", "10101018-1x2.h"])
def test_other_names_are_no_rims(name):
    assert parse_program(RIM_DIR + name) is None


def test_order_scheme_wins_over_rim_scheme():
    assert parse_program("26-21055-01-01.H").kind == "order"


@pytest.mark.parametrize(
    ("key", "label", "width"),
    [
        ("10101018", "999 · einteilig · 10 × 18″", 10.0),
        ("11208520", "Z06 · dreiteilig · 8,5 × 20″", 8.5),
        ("10901030", "Sonder · einteilig · 10 × 30″", 10.0),  # Durchmesser bis 30″
        ("10907521", "Sonder · einteilig · 7,5 × 21″", 7.5),  # ab 20: Zehntel
        ("10959519", "Design 95 · einteilig · 9,5 × 19″", 9.5),  # Design nicht in der Liste
        ("12101018", "999 · Bauart 12 · 10 × 18″", 10.0),
    ],
)
def test_rim_info(key, label, width):
    info = orders.rim_info(key, orders.RIM_DESIGNS)
    assert (info["label"], info["width"]) == (label, width)
    assert orders.rim_info("21055", orders.RIM_DESIGNS) is None


def test_rim_gets_its_own_entry_and_sums_its_programs(db, make_collector):
    """Spannung 1: Hauptprogramm, Tasche und Einarm nacheinander; die Ø-Zeit je Felge ist die Summe."""
    c = make_collector()
    t = run_part(c, 0, RIM_1, 600)
    t = run_part(c, t + 10, RIM_1_POCKET, 300)
    t = run_part(c, t + 10, RIM_1_ARM, 120)
    run_part(c, t + 10, RIM_2, 900)
    [row] = orders.list_orders(db)
    assert (row["key"], row["kind"], row["year"], row["setups"], row["programs"]) == ("10101018", "rim", 0, [1, 2], 4)
    assert row["rim"]["label"] == "999 · einteilig · 10 × 18″"
    detail = orders.order_detail(db, "10101018", TZ)
    assert detail["order"]["rim"]["design_name"] == "999"
    first, second = detail["setups"]
    assert [p["name"] for p in first["programs"]] == ["10101018-01", "10101018-01 einarm", "10101018-01 tasche"]
    assert (first["part_run_s"], second["part_run_s"]) == (1020, 900)
    assert (detail["totals"]["part_run_s"], detail["totals"]["part_complete"]) == (1920, True)


def test_rim_setups_08_and_09_are_no_fixtures(db, make_collector):
    c = make_collector()
    run_part(c, 0, RIM_DIR + "10101018-08.h", 300)
    [setup] = orders.order_detail(db, "10101018", TZ)["setups"]
    assert not setup["fixture"]


def test_live_card_names_the_rim(client):
    col = client.app.state.ctx.collectors["m1"]
    feed(col, (5000, snap("IDLE", RIM_1_POCKET)), (5010, snap("STARTED", RIM_1_POCKET)))
    order = client.get("/api/machines").json()["machines"][0]["order"]
    assert (order["kind"], order["setup"], order["variant"]) == ("rim", 1, "tasche")
    assert order["rim"]["label"] == "999 · einteilig · 10 × 18″"


def test_rim_designs_can_be_edited(client):
    designs = client.get("/api/config/rim-designs").json()["designs"]
    assert [(d["code"], d["name"]) for d in designs][:2] == [(10, "999"), (20, "Z06")]
    assert len(designs) == 9
    r = client.put("/api/config/rim-designs/95", json={"name": "Neu 95"})
    assert r.status_code == 200 and r.json()["created"] is True
    assert client.put("/api/config/rim-designs/10", json={"name": "999 Evo"}).json()["created"] is False
    assert client.put("/api/config/rim-designs/100", json={"name": "x"}).status_code == 400
    assert client.put("/api/config/rim-designs/30", json={"name": " "}).status_code == 400
    assert client.delete("/api/config/rim-designs/90").status_code == 204
    assert client.delete("/api/config/rim-designs/90").status_code == 404
    names = {d["code"]: d["name"] for d in client.get("/api/config/rim-designs").json()["designs"]}
    assert (names[10], names[95], 90 in names) == ("999 Evo", "Neu 95", False)
    col = client.app.state.ctx.collectors["m1"]
    run_part(col, 6000, RIM_1, 100)
    rim = next(o for o in client.get("/api/orders").json()["orders"] if o["kind"] == "rim")
    assert rim["rim"]["label"] == "999 Evo · einteilig · 10 × 18″"  # neuer Name gilt sofort


def test_rim_image_key_is_valid():
    from app.order_images import valid_key

    assert valid_key("10101018") and valid_key("21055") and not valid_key("2610101018")
