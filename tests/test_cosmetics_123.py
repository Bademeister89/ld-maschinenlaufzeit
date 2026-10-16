"""Version 1.23.0: fertige Teile, Frist bis zum automatischen Abschließen, Fehlersammler,
Produktionszeit im Statusbalken."""

import time
from dataclasses import replace

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
        ("READY", None, 8000, 8060),  # Palettenwechsel
        ("RUNNING", 2, 8060, 9000),
    ]
    s = production_streak(states, 9000, 120)
    assert (s["since"], s["run_s"], s["stop_s"], s["idle_s"]) == (2000, 6640, 300, 60)
    assert "segments" not in s  # kein Zeitstreifen mehr (1.36.0)


def test_streak_survives_a_short_pause_but_not_a_long_one():
    states = [("RUNNING", 1, 0, 3600), ("READY", None, 3600, 3600 + 90)]
    s = production_streak(states, 3600 + 90, 120)  # 90 s Pause: läuft weiter
    assert (s["since"], s["idle_s"]) == (0, 90)
    assert production_streak(states, 3600 + 120, 120) is None  # ab der Pausengrenze: vorbei
    assert production_streak(states, 3600 + 600, 15 * 60)["since"] == 0  # andere Grenze je Maschine
    assert production_streak([("READY", None, 0, 100)], 100, 120) is None


def test_streak_does_not_end_on_a_long_stop():
    """NC-Stopp und Fehler im Lauf sind keine Pause: auch eine Stunde beendet die Produktion nicht."""
    states = [("RUNNING", 1, 0, 1000), ("ERROR", 1, 1000, 4600), ("RUNNING", 1, 4600, 5000)]
    s = production_streak(states, 5000, 120)
    assert (s["since"], s["stop_s"], s["idle_s"]) == (0, 3600, 0)


def test_streak_treats_missing_data_as_pause():
    """Lücke ohne Daten (App aus) zwischen zwei Läufen zählt wie eine Pause."""
    states = [("RUNNING", 1, 0, 1000), ("RUNNING", 2, 5000, 6000)]
    assert production_streak(states, 6000, 120)["since"] == 5000
    states = [("RUNNING", 1, 0, 1000), ("RUNNING", 2, 1100, 2000)]
    assert production_streak(states, 2000, 120)["since"] == 0


def test_streak_bridges_missing_data_within_the_same_run():
    """Keine Verbindung oder App aus, danach läuft derselbe Lauf weiter: kein neuer Beginn."""
    gap = [("RUNNING", 1, 0, 1000), ("RUNNING", 1, 1600, 2000)]
    s = production_streak(gap, 2000, 120)
    assert (s["since"], s["run_s"], s["idle_s"]) == (0, 1400, 600)
    offline = [("RUNNING", 1, 0, 1000), ("OFFLINE", 1, 1000, 1600), ("STOPPED", 1, 1600, 2000)]
    assert production_streak(offline, 2000, 120)["since"] == 0
    # Bereit dazwischen ist eine echte Pause – auch wenn derselbe Lauf weitergeht (Satzvorlauf)
    ready = [("RUNNING", 1, 0, 1000), ("OFFLINE", 1, 1000, 1100), ("READY", None, 1100, 1600), ("RUNNING", 1, 1600, 2000)]
    assert production_streak(ready, 2000, 120)["since"] == 1600
    # Ohne Verbindung bis jetzt: offen, ob es weitergeht – wie eine Pause
    assert production_streak([("RUNNING", 1, 0, 1000), ("OFFLINE", 1, 1000, 1600)], 1600, 120) is None


def test_live_card_has_the_streak(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE", "P1")), (10, snap("STARTED", "P1")), (400, snap("STOPPED", "P1")), (460, snap("STARTED", "P1")))
    feed(c, (1000, snap("STARTED", "P1")))
    s = c.live()["streak"]
    assert (s["since"], s["run_s"], s["stop_s"]) == (10, 930, 60)
    feed(c, (1100, None))  # keine Verbindung: kein Balken
    assert c.live()["streak"] is None


def _shift(c, t0, t1, cycle=7200):
    """Produktion von t0 bis t1: alle ``cycle`` s ein kurzer NC-Stopp (viele Zustandsabschnitte)."""
    t = t0
    while t < t1:
        feed(c, (t, snap("STARTED", "P1")), (t + cycle - 60, snap("STOPPED", "P1")))
        t += cycle
    feed(c, (t1, snap("STARTED", "P1")))


def test_streak_is_not_capped_at_24_hours(db, make_collector):
    """Version 1.27.1: DMU 70, 10.–12.10. – 55 h Produktion am Stück; die Anzeige blieb bei gut 24 h."""
    c = make_collector()
    feed(c, (0, snap("IDLE", "P1")))
    _shift(c, 10, 2.5 * DAY)
    s = c.live()["streak"]
    assert s["since"] == 10
    assert s["run_s"] + s["stop_s"] == pytest.approx(2.5 * DAY - 10)
    # danach lädt es nur noch ab kurz vor dem Beginn – und zählt weiter
    assert c._streak_from == pytest.approx(10 - 120 - 60)
    _shift(c, 2.5 * DAY, 3 * DAY)
    assert c.live()["streak"]["since"] == 10


def test_long_streak_still_starts_after_the_last_long_pause(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE", "P1")))
    _shift(c, 10, 1 * DAY)
    feed(c, (1 * DAY + 100, snap("IDLE", "P1")))  # 20 min Pause: neue Produktion
    _shift(c, 1 * DAY + 1300, 2.5 * DAY)
    assert c.live()["streak"]["since"] == 1 * DAY + 1300


def test_pause_limit_comes_from_the_machine(db, make_collector):
    """Version 1.36.0: Pausengrenze je Maschine (Standard 2 min) statt fest 15 min."""
    c = make_collector()
    feed(c, (0, snap("IDLE", "P1")), (10, snap("STARTED", "P1")), (1000, snap("IDLE", "P1")))
    feed(c, (1300, snap("STARTED", "P2")), (2000, snap("STARTED", "P2")))  # 5 min Pause
    assert c.live()["streak"]["since"] == 1300
    assert c.live()["streak"]["break_s"] == 120
    c.machine = replace(c.machine, pause_limit_min=10)  # in der Konfiguration geändert
    s = c.live()["streak"]
    assert (s["since"], s["break_s"]) == (10, 600)
    assert s["idle_s"] == pytest.approx(300)


def test_pause_limit_in_the_configuration(tmp_path):
    settings = Settings(db_path=tmp_path / "t.db")
    with TestClient(create_app(settings, run_collectors=False)) as client:
        base = {"name": "DMU 70", "host": "192.168.0.30"}
        m = client.post("/api/config/machines", json=base).json()
        assert m["pause_limit_min"] == 2  # Standard
        r = client.put(f"/api/config/machines/{m['id']}", json={**m, "pause_limit_min": "5"})
        assert r.status_code == 200 and r.json()["pause_limit_min"] == 5
        for bad in ("0", "121", "zwei", "1,5"):
            r = client.put(f"/api/config/machines/{m['id']}", json={**m, "pause_limit_min": bad})
            assert r.status_code == 400 and "Pausengrenze" in r.json()["detail"]
        assert client.get("/api/config").json()["machines"][0]["pause_limit_min"] == 5
        assert client.get("/api/machines").json()["machines"][0]["streak"] is None  # noch keine Daten
