"""Oberprogramme (z. B. Palettenprogramm auf der Automation), die Auftragsprogramme per CALL PGM aufrufen."""

import pytest

from app import orders

from .conftest import feed, snap

PAL = "TNC:\\PALETTE\\PAL1.H"
A = "TNC:\\AUFTRAG\\26-21055-01-01.H"
B = "TNC:\\AUFTRAG\\26-21102-01-01.H"
SUB = "TNC:\\ZYKLEN\\ENTGRATEN.H"


def pal(pgm_state: str, current: str | None = PAL, **kw):
    """Abfrage mit angewähltem Palettenprogramm; ``current`` = gerade abgearbeitetes Programm."""
    return snap(pgm_state, PAL, current_program=current, **kw)


def runs(db):
    return [(r["program"], r["started_at"], r["ended_at"], r["result"]) for r in db.runs(0, 10_000)]


def test_called_programs_get_runs_and_orders(db, make_collector):
    c = make_collector()
    feed(
        c,
        (0, pal("IDLE")),
        (10, pal("STARTED")),  # Palettenwechsel vor dem ersten Aufruf
        (20, pal("STARTED", A)),
        (30, pal("STARTED", A)),
        (50, pal("STARTED")),  # zurück im Palettenprogramm
        (60, pal("STARTED", B)),
        (90, pal("FINISHED")),
    )
    assert runs(db) == [(A, 20, 50, "finished"), (B, 60, 90, "finished")]
    assert all(r["start_observed"] for r in db.runs(0, 10_000))
    totals = {r["key"]: (r["running_s"], r["runs"], r["finished"]) for r in orders.list_orders(db)}
    assert totals == {"26-21055": (30, 1, 1), "26-21102": (30, 1, 1)}
    # Die Zeit im Palettenprogramm bleibt Laufzeit der Maschine, gehört aber zu keinem Lauf
    rows = db._query("SELECT state, program, run_id, started_at FROM state_intervals ORDER BY id")
    pal_rows = [(r["state"], r["run_id"], r["started_at"]) for r in rows if r["program"] == PAL]
    assert pal_rows == [("READY", None, 0), ("RUNNING", None, 10), ("RUNNING", None, 50), ("READY", None, 90)]
    assert db._query("SELECT COUNT(*) AS n FROM run_progress WHERE run_id NOT IN (SELECT id FROM program_runs)")[0]["n"] == 0


def test_subprogram_of_called_program_stays_in_run(db, make_collector):
    c = make_collector()
    feed(
        c,
        (0, pal("IDLE")),
        (10, pal("STARTED", A)),
        (20, pal("STARTED", SUB)),  # A ruft ein Unterprogramm auf
        (30, pal("STARTED", A)),
        (40, pal("FINISHED")),
    )
    [run] = db.runs(0, 10_000)
    assert (run["program"], run["started_at"], run["ended_at"], run["result"], run["run_s"]) == (A, 10, 40, "finished", 30)
    programs = {r["program"] for r in db._query("SELECT program FROM state_intervals WHERE run_id IS NOT NULL")}
    assert programs == {A}


def test_next_call_without_seeing_the_pallet_program(db, make_collector):
    """Die Zeilen des Palettenprogramms zwischen zwei Aufrufen dauern oft kürzer als ein Abfragetakt."""
    c = make_collector()
    feed(c, (0, pal("IDLE")), (10, pal("STARTED", A)), (40, pal("STARTED", B)), (70, pal("FINISHED")))
    assert runs(db) == [(A, 10, 40, "finished"), (B, 40, 70, "finished")]


def test_pallet_program_calling_other_programs_runs_no_run(db, make_collector):
    c = make_collector()
    feed(
        c,
        (0, pal("IDLE")),
        (10, pal("STARTED", A)),
        (20, pal("STARTED")),
        (30, pal("STARTED", SUB)),  # Palettenprogramm ruft selbst ein Programm ohne Auftrag auf (z. B. Messen)
        (40, pal("FINISHED")),
    )
    assert runs(db) == [(A, 10, 20, "finished")]


def test_cancel_in_called_program(db, make_collector):
    c = make_collector()
    feed(c, (0, pal("IDLE")), (10, pal("STARTED", A)), (20, pal("STOPPED", A)), (30, pal("CANCELLED")))
    assert runs(db) == [(A, 10, 30, "cancelled")]


def test_finished_called_runs_feed_the_forecast(db, make_collector):
    c = make_collector()
    feed(c, (0, pal("IDLE")), (10, pal("STARTED", A)), (110, pal("STARTED")), (120, pal("FINISHED")))
    feed(c, (200, pal("STARTED", A)), (240, pal("STARTED", A)))
    forecast = c.live()["forecast"]
    assert forecast["method"] == "history"
    assert forecast["remaining_s"] == pytest.approx(60)


def test_live_shows_called_program_and_caller(db, make_collector):
    c = make_collector()
    feed(c, (0, pal("IDLE")), (10, pal("STARTED", A, tool="T5")), (12, pal("STARTED", A, tool="T7")))
    live = c.live()
    assert (live["program"], live["caller"], live["order"]["key"], live["run"]["program"]) == (A, PAL, "26-21055", A)
    calls = db._query("SELECT number, program FROM tool_calls")
    assert [(r["number"], r["program"]) for r in calls] == [(7, A)]
    feed(c, (20, pal("STARTED")))
    live = c.live()
    assert (live["program"], live["caller"], live["order"], live["run"]) == (PAL, PAL, None, None)


def test_restart_inside_subprogram_keeps_open_run(db, make_collector):
    first = make_collector()
    feed(first, (0, pal("IDLE")), (10, pal("STARTED", A)), (20, pal("STARTED", A)))
    second = make_collector()  # Neustart des Tools, A ruft gerade ein Unterprogramm auf
    feed(second, (30, pal("STARTED", SUB)), (40, pal("STARTED", A)), (50, pal("FINISHED")))
    assert runs(db) == [(A, 10, 50, "finished")]


def test_programs_without_order_code_keep_main_program(db, make_collector):
    c = make_collector()
    feed(
        c,
        (0, snap("IDLE", "TNC:\\PROD\\HAUPT.H")),
        (10, snap("STARTED", "TNC:\\PROD\\HAUPT.H", current_program=SUB)),
        (20, snap("FINISHED", "TNC:\\PROD\\HAUPT.H")),
    )
    assert runs(db) == [("TNC:\\PROD\\HAUPT.H", 10, 20, "finished")]


def test_order_program_selected_directly_keeps_subprograms(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE", A)), (10, snap("STARTED", A, current_program=B)), (20, snap("FINISHED", A)))
    assert runs(db) == [(A, 10, 20, "finished")]
