from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app import stats
from app.adapters.sim_adapter import SimulatedMachine

from .conftest import feed, snap

TZ = ZoneInfo("Europe/Berlin")


def ts(text: str) -> float:
    return datetime.fromisoformat(text).replace(tzinfo=TZ).timestamp()


def test_totals_add_up_to_period_including_no_data(db, make_collector):
    c = make_collector()
    base = ts("2026-09-21 08:00")
    feed(c, (base, snap("IDLE")), (base + 600, snap("STARTED")), (base + 3000, snap("FINISHED")), (base + 3600, None))
    t0, t1 = ts("2026-09-21 00:00"), ts("2026-09-21 12:00")
    result = stats.summarize(db, ["m1"], t0, t1, TZ, now=t1)
    totals = result["machines"]["m1"]["totals"]
    assert totals["RUNNING"] == 2400
    # Letzter Kontakt im Zustand BEREIT war base+3000 → ab dort gilt die Maschine als offline;
    # das offene Offline-Intervall endet beim letzten Abfrageversuch (base+3600).
    assert totals["READY"] == 600
    assert totals["OFFLINE"] == 600
    assert sum(totals.values()) == pytest.approx(t1 - t0)
    assert result["machines"]["m1"]["utilization"] == pytest.approx(2400 / 3000)


def test_days_are_split_at_local_midnight_and_handle_dst(db, make_collector):
    c = make_collector()
    # Zeitumstellung 29.03.2026: der Tag hat nur 23 Stunden
    feed(c, (ts("2026-03-28 22:00"), snap("STARTED")), (ts("2026-03-29 04:00"), snap("STARTED")))
    t0, t1 = ts("2026-03-28 00:00"), ts("2026-03-30 00:00")
    days = stats.summarize(db, ["m1"], t0, t1, TZ, now=t1)["machines"]["m1"]["days"]
    assert [d["date"] for d in days] == ["2026-03-28", "2026-03-29"]
    assert days[0]["period_s"] == 24 * 3600
    assert days[1]["period_s"] == 23 * 3600
    assert days[0]["states"]["RUNNING"] == 2 * 3600
    assert days[1]["states"]["RUNNING"] == 3 * 3600  # 00:00–04:00 Uhr, aber 02:00–03:00 fehlt
    for d in days:
        assert sum(d["states"].values()) == pytest.approx(d["period_s"])


def test_range_is_capped_at_now(db, make_collector):
    t0 = ts("2026-09-21 00:00")
    result = stats.summarize(db, ["m1"], t0, t0 + 86400, TZ, now=t0 + 3600)
    assert result["period_s"] == 3600
    assert result["machines"]["m1"]["totals"]["NO_DATA"] == 3600
    assert result["machines"]["m1"]["utilization"] is None


def test_program_statistics(db, make_collector):
    c = make_collector()
    b = ts("2026-09-21 08:00")
    feed(
        c,
        (b, snap("IDLE")),
        (b + 100, snap("STARTED")),
        (b + 400, snap("STOPPED")),
        (b + 500, snap("STARTED")),
        (b + 700, snap("FINISHED")),
        (b + 800, snap("STARTED")),
        (b + 1300, snap("FINISHED")),
        (b + 1400, snap("STARTED", "P2")),
        (b + 1500, snap("CANCELLED", "P2")),
    )
    progs = stats.summarize(db, ["m1"], b, b + 2000, TZ, now=b + 2000)["programs"]
    p1 = next(p for p in progs if p["program"] == "P1")
    assert p1["runs"] == 2
    assert p1["finished"] == 2
    assert p1["running_s"] == 300 + 200 + 500
    assert p1["stopped_s"] == 100
    assert p1["avg_run_s"] == pytest.approx((500 + 500) / 2)
    assert p1["avg_total_s"] == pytest.approx((600 + 500) / 2)
    p2 = next(p for p in progs if p["program"] == "P2")
    assert (p2["runs"], p2["finished"], p2["avg_run_s"]) == (1, 0, None)


def test_simulated_history_is_consistent(db, make_collector):
    # Eine Woche Simulation mit Schichtbetrieb durch den echten Collector schicken
    c = make_collector()
    start = ts("2026-09-14 00:00")
    end = start + 7 * 86400
    sim = SimulatedMachine(seed=3, start=start, speed=1.0, shift=(6, 22))
    t = start
    while t < end:
        c.process(sim.state_at(t), t)
        t = min(sim.next_change, t + 60)
    result = stats.summarize(db, ["m1"], start, end, TZ, now=end)
    m = result["machines"]["m1"]
    assert sum(m["totals"].values()) == pytest.approx(7 * 86400)
    assert m["totals"]["RUNNING"] > 0
    assert m["totals"]["OFFLINE"] > 2 * 86400  # Nächte und Wochenende
    # Wochenende: keine Laufzeit
    assert m["days"][5]["states"]["RUNNING"] == 0
    assert m["days"][6]["states"]["RUNNING"] == 0
    assert any(p["finished"] > 0 for p in result["programs"])
