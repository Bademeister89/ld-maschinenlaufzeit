"""Oberprogramme (z. B. Palettenprogramm auf der Automation), die Auftragsprogramme per CALL PGM aufrufen."""

import asyncio
import sqlite3

import pytest

from app import orders
from app.collector import MachineCollector
from app.db import SCHEMA_VERSION, Database
from app.nc_program import ProgramFile, call_name, program_calls

from .conftest import MACHINE, NullAdapter, feed, snap

PAL = "TNC:\\PALETTE\\PAL1.H"
A = "TNC:\\AUFTRAG\\26-21055-01-01.H"
B = "TNC:\\AUFTRAG\\26-21102-01-01.H"
SUB = "TNC:\\ZYKLEN\\ENTGRATEN.H"
CLEAN = "TNC:\\PALETTE\\REINIGUNG.H"

PAL_TEXT = """0  BEGIN PGM PAL1 MM
1  ; Palette 1 - CALL PGM KOMMENTAR zaehlt nicht
2  CALL PGM TNC:\\AUFTRAG\\26-21055-01-01.H
3  CALL PGM REINIGUNG
4  CYCL DEF 12.0 PGM CALL
5  CYCL DEF 12.1 PGM TNC:\\ZYKLEN\\MESSEN.H
6  call pgm "TNC:\\AUFTRAG\\26-21055-01-01.h"
7  SEL PGM "TNC:\\PALETTE\\Spuelen.H"
8  CALL SELECTED PGM
9  END PGM PAL1 MM
"""


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


# --- Programme, die das Oberprogramm selbst aufruft (z. B. Reinigung) ----------------------------


def test_program_calls_from_klartext():
    assert program_calls(PAL_TEXT) == ("26-21055-01-01", "REINIGUNG", "MESSEN", "SPUELEN")
    assert program_calls("0 BEGIN PGM X MM\n1 L X+0 FMAX\n2 END PGM X MM\n") == ()
    assert call_name("TNC:\\PALETTE\\Reinigung.h") == call_name("REINIGUNG") == "REINIGUNG"


def pallet_file(db, calls=("26-21055-01-01", "REINIGUNG")):
    db.save_program_file("m1", PAL, 500, 1.0, 9, None, 0, calls)


def test_cleaning_called_by_pallet_program_ends_run(db, make_collector):
    c = make_collector()
    pallet_file(db)
    feed(
        c,
        (0, pal("IDLE")),
        (10, pal("STARTED", A)),
        (100, pal("STARTED", CLEAN)),  # Palettenzeilen dazwischen nicht gesehen
        (130, pal("STARTED", A)),  # nächstes Teil, wieder A
        (220, pal("STARTED", CLEAN)),
        (250, pal("FINISHED")),
    )
    assert runs(db) == [(A, 10, 100, "finished"), (A, 130, 220, "finished")]
    [row] = orders.list_orders(db)
    assert (row["running_s"], row["runs"], row["finished"]) == (180, 2, 2)
    live = c.live()
    assert (live["program"], live["caller"], live["run"]) == (PAL, PAL, None)


def test_subprogram_not_called_by_pallet_program_stays_in_run(db, make_collector):
    c = make_collector()
    pallet_file(db)
    feed(c, (0, pal("IDLE")), (10, pal("STARTED", A)), (20, pal("STARTED", SUB)), (30, pal("STARTED", A)), (40, pal("FINISHED")))
    assert runs(db) == [(A, 10, 40, "finished")]


def test_restart_during_cleaning_ends_open_run_as_finished(db, make_collector):
    first = make_collector()
    pallet_file(db)
    feed(first, (0, pal("IDLE")), (10, pal("STARTED", A)), (20, pal("STARTED", A)))
    second = make_collector()  # Neustart des Tools während der Reinigung
    feed(second, (30, pal("STARTED", CLEAN)), (40, pal("STARTED", B)), (60, pal("FINISHED")))
    assert runs(db) == [(A, 10, 20, "finished"), (B, 40, 60, "finished")]


class CallsAdapter(NullAdapter):
    def __init__(self):
        self.fetched = []

    def fetch_program(self, path, known, max_bytes):
        self.fetched.append((path, known))
        if known == (500, 1.0):
            return None
        return ProgramFile(path, 500, 1.0, 9, calls=program_calls(PAL_TEXT) if path == PAL else ())


def test_fetched_program_stores_calls(db):
    adapter = CallsAdapter()
    c = MachineCollector(MACHINE, adapter, db)

    async def scenario():
        for t, s in [(0, pal("IDLE")), (10, pal("STARTED", A))]:
            c.process(s, t)
            c._maybe_fetch_programs(s)
            await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert db.program_file("m1", PAL)["calls"] == ("26-21055-01-01", "REINIGUNG", "MESSEN", "SPUELEN")
    assert db.program_file("m1", A)["calls"] == ()


def test_update_rereads_program_files_once(tmp_path):
    """Datenbank wie in Version 1.10.1 (Schema 9): Programmdateien ohne Aufrufe."""
    path = tmp_path / "v9.db"
    db = Database(path)
    db.ensure_machine("m1", "DMG 1", "10.0.0.1", 19000)
    db.save_program_file("m1", PAL, 500, 1.0, 9, None, 0)
    db.close()
    con = sqlite3.connect(path)
    con.executescript("ALTER TABLE program_files DROP COLUMN calls; UPDATE meta SET value = '9' WHERE key = 'schema_version';")
    con.close()
    db = Database(path)
    assert db.get_meta("schema_version") == str(SCHEMA_VERSION) == "10"
    row = db.program_file("m1", PAL)
    assert (row["size"], row["mtime"], row["blocks"], row["calls"]) == (None, None, 9, None)
    db.close()
