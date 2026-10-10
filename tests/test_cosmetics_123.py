"""Version 1.23.0: fertige Teile, Frist bis zum automatischen Abschließen, Fehlersammler,
Produktionszeit im Statusbalken."""

import time

import pytest
from fastapi.testclient import TestClient

from app import orders
from app.collector import production_streak
from app.config import MachineConfig, Settings
from app.main import create_app

from .conftest import feed, snap

DAY = 86_400


# --- Fertige Teile -------------------------------------------------------------------------


def test_finished_parts_count_the_last_program_of_the_last_setup():
    programs = [
        ("TNC:\\A\\26-21053-01-01.h", 9),
        ("TNC:\\A\\26-21053-02-01.h", 6),
        ("TNC:\\A\\26-21053-02-02.h", 5),
        ("TNC:\\B\\26-21053-02-03.h", 3),  # letztes Programm (anderer Ordner)
        ("TNC:\\A\\26-21053-02-03.h", 1),  # dasselbe Programm: zählt mit
        ("TNC:\\A\\26-21053-08-01.h", 2),  # Vorrichtung zählt nicht
        ("TNC:\\A\\26-21053V2-01-01.h", 4),  # Version V2: eigene letzte Aufspannung
    ]
    assert orders.finished_parts(programs, "order") == {"": 4, "V2": 4}


def test_finished_parts_of_a_rim_and_of_a_planned_last_program():
    rim = [("10101018-01", 7), ("10101018-01 tasche", 5)]
    assert orders.finished_parts(rim, "rim") == {"": 5}  # letzte Zeile der Spannung
    # Letztes Programm hat nur eine Planzeit, lief noch nie: noch kein Teil fertig
    assert orders.finished_parts([("26-4711-01-01", 3), ("26-4711-02-01", 0)], "order") == {"": 0}


@pytest.fixture
def client(tmp_path):
    settings = Settings(machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"),), db_path=tmp_path / "c.db", simulate=True)
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as c:
        yield c


def run_part(col, t0, program, run_s):
    feed(col, (t0, snap("IDLE", program)), (t0 + 10, snap("STARTED", program)), (t0 + 10 + run_s, snap("FINISHED", program)))
    return t0 + 10 + run_s


def test_order_list_and_detail_show_finished_parts(client):
    col = client.app.state.ctx.collectors["m1"]
    t = 1000
    for _ in range(3):
        t = run_part(col, t + 10, "TNC:\\X\\26-21055-01-01.H", 100)
    t = run_part(col, t + 10, "TNC:\\X\\26-21055-02-01.H", 100)
    [row] = client.get("/api/orders").json()["orders"]
    assert (row["finished"], row["parts"]) == (4, 1)
    detail = client.get("/api/orders/21055").json()
    assert (detail["totals"]["finished"], detail["totals"]["parts"], detail["versions"][0]["parts"]) == (4, 1, 1)


# --- Frist bis zum automatischen Abschließen ------------------------------------------------


def test_close_days_can_be_changed_and_switched_off(client):
    db = client.app.state.ctx.db
    assert client.get("/api/config/orders").json()["close_days"] == 7
    db.ensure_order("4711", 2026, "4711", time.time() - 5 * DAY)
    assert db.order("4711")["status"] == "open"
    r = client.put("/api/config/orders", json={"close_days": "3"})
    assert (r.status_code, r.json()["close_days"], r.json()["closed"]) == (200, 3, ["4711"])  # gilt sofort
    assert db.order("4711")["status"] == "closed"
    assert client.get("/api/orders").json()["close_days"] == 3

    db.ensure_order("4712", 2026, "4712", time.time() - 400 * DAY)
    assert client.put("/api/config/orders", json={"close_days": 0}).json()["closed"] == []  # 0 = nie
    assert orders.close_idle(db, time.time()) == []
    assert db.order("4712")["status"] == "open"
    for bad in ("-1", "366", "abc", ""):
        assert client.put("/api/config/orders", json={"close_days": bad}).status_code == 400


# --- Fehlersammler -------------------------------------------------------------------------


def test_error_collector_lists_messages_per_machine(client):
    db = client.app.state.ctx.db
    now = time.time()
    for ts, text in [(now - 40 * DAY, "Alt"), (now - 3600, "Puffer-Batterie wechseln"), (now - 60, "Puffer-Batterie wechseln"),
                     (now - 30, "KABINENTÜR ÖFFNEN MÖGLICH")]:
        db.add_event("m1", ts, "nc_error", {"text": text, "program": "TNC:\\X\\26-21055-01-01.H"})
    db.add_event("m1", now, "tool_change", {"from": "T1", "to": "T2"})  # kein Fehler
    data = client.get("/api/machines/m1/errors", params={"days": 30}).json()
    assert [e["text"] for e in data["errors"]] == ["KABINENTÜR ÖFFNEN MÖGLICH", "Puffer-Batterie wechseln", "Puffer-Batterie wechseln"]
    assert [(t["text"], t["count"]) for t in data["top"]] == [("Puffer-Batterie wechseln", 2), ("KABINENTÜR ÖFFNEN MÖGLICH", 1)]
    assert data["top"][0]["last"] == pytest.approx(now - 60)
    assert len(client.get("/api/machines/m1/errors", params={"days": 90}).json()["errors"]) == 4
    assert client.get("/api/machines/xx/errors").status_code == 404
    assert client.get("/api/machines/m1/errors", params={"days": 0}).status_code == 422


# --- Produktionszeit im Statusbalken ----------------------------------------------------------


def test_streak_bridges_stops_pallet_changes_and_short_pauses():
    states = [
        ("RUNNING", None, 0, 100),  # MDI vor der Pause
        ("READY", None, 100, 2000),  # lange Pause: beendet alles davor
        ("RUNNING", 1, 2000, 5000),
        ("STOPPED", 1, 5000, 5300),  # NC-Stopp
        ("RUNNING", 1, 5300, 8000),
        ("READY", None, 8000, 8200),  # Palettenwechsel
        ("RUNNING", 2, 8200, 9000),
    ]
    s = production_streak(states, 9000)
    assert (s["since"], s["run_s"], s["stop_s"], s["idle_s"]) == (2000, 6500, 300, 200)
    assert [x["kind"] for x in s["segments"]] == ["run", "stop", "run", "idle", "run"]


def test_streak_survives_a_short_pause_but_not_a_long_one():
    states = [("RUNNING", 1, 0, 3600), ("READY", None, 3600, 3600 + 600)]
    s = production_streak(states, 3600 + 600)  # 10 min Pause: läuft weiter
    assert (s["since"], s["idle_s"]) == (0, 600)
    assert production_streak(states, 3600 + 15 * 60) is None  # ab 15 min: vorbei
    assert production_streak([("READY", None, 0, 100)], 100) is None


def test_streak_treats_missing_data_as_pause():
    """Lücke ohne Daten (App aus) zählt wie eine Pause."""
    states = [("RUNNING", 1, 0, 1000), ("RUNNING", 2, 5000, 6000)]
    assert production_streak(states, 6000)["since"] == 5000
    states = [("RUNNING", 1, 0, 1000), ("RUNNING", 2, 1300, 2000)]
    assert production_streak(states, 2000)["since"] == 0


def test_live_card_has_the_streak(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE", "P1")), (10, snap("STARTED", "P1")), (400, snap("STOPPED", "P1")), (460, snap("STARTED", "P1")))
    feed(c, (1000, snap("STARTED", "P1")))
    s = c.live()["streak"]
    assert (s["since"], s["run_s"], s["stop_s"]) == (10, 930, 60)
    feed(c, (1100, None))  # keine Verbindung: kein Balken
    assert c.live()["streak"] is None
