import asyncio

import pytest

from app import collector as collector_module
from app.collector import MachineCollector
from app.forecast import Forecaster, locate
from app.nc_program import ProgramFile, count_blocks

from .conftest import MACHINE, NullAdapter, feed, snap

KLARTEXT = """0  BEGIN PGM GEHAEUSE MM
1  BLK FORM 0.1 Z  X+0  Y+0  Z-40
2  BLK FORM 0.2  X+100  Y+100  Z+0
3  TOOL CALL 12 Z S8000
4  ; Schruppen
5  L  X-20  Y-20 R0 FMAX M3
6  CYCL DEF 251 RECHTECKTASCHE
7  END PGM GEHAEUSE MM
"""


# --- Satzanzahl ------------------------------------------------------------------------


def test_count_blocks_klartext():
    assert count_blocks("TNC:\\PROD\\GEHAEUSE.H", KLARTEXT) == 7
    assert count_blocks("TNC:\\PROD\\gehaeuse.h", KLARTEXT.replace("END PGM", "end pgm")) == 7


def test_count_blocks_klartext_without_end_uses_last_number():
    assert count_blocks("X.H", "0 BEGIN PGM X MM\n1 L X+0\n2 L X+1\n") == 2


def test_count_blocks_iso_and_unknown():
    iso = "%TEIL G71 *\nN10 G00 X0 *\n\nN20 G01 X10 *\nN99999999 %TEIL G71 *\n"
    assert count_blocks("TNC:\\TEIL.I", iso) == 3
    assert count_blocks("TNC:\\TABELLE.T", "irgendwas") is None


# --- Prognose ----------------------------------------------------------------------------


def test_locate_moves_forward_only():
    samples = [(0, "P", 1), (10, "P", 50), (20, "P", 10), (30, "P", 60)]  # Satz 10 kommt zweimal
    assert locate(samples, "P", 10, 0) == 1
    assert locate(samples, "P", 10, 2) == 2  # Wiederholung: nicht zurückspringen
    assert locate(samples, "SUB", 1, 0) is None


def reference_run(c, start, lines_at, program="P1"):
    """Kompletter, vollständig beobachteter Lauf: [(Sekunde, Satz), ...], danach fertig."""
    feed(c, (start, snap("IDLE", program)))
    for offset, line in lines_at:
        c.process(snap("STARTED", program, line_no=line, current_program=program), start + 1 + offset)
    c.process(snap("FINISHED", program), start + 1 + lines_at[-1][0] + 1)


# Satz 100 ist nach 10 % der Zeit erreicht, Satz 900 erst nach 90 %: ein langer Satz dazwischen
PROFILE = [(0, 1), (60, 100), (120, 150), (540, 900), (600, 1000)]


def test_profile_forecast_uses_block_history(db, make_collector):
    c = make_collector()
    reference_run(c, 0, PROFILE)
    reference_run(c, 1000, PROFILE)
    # Neuer Lauf, Satz 900 nach 540 s (gleiches Tempo)
    feed(c, (2000, snap("IDLE")), (2001, snap("STARTED", line_no=1, current_program="P1")))
    c.process(snap("STARTED", line_no=900, current_program="P1"), 2541)
    f = c.live()["forecast"]
    assert f["method"] == "profile"
    assert f["basis_runs"] == 2
    assert f["progress"] == pytest.approx(0.9, abs=0.02)
    assert f["remaining_s"] == pytest.approx(61, abs=5)
    assert f["eta"] == pytest.approx(2541 + f["remaining_s"])


def test_copy_in_another_folder_uses_the_history_of_the_original(db, make_collector):
    """DMU 70, 7.10.: 26-21051-02-01.h lief aus dem Ordner von 21053 – eine Kopie des Programms aus
    dem Ordner von 21051. Gleicher Name = gleiches Programm, also auch dieselbe Prognose."""
    original = "TNC:\\Programme\\21051 Kupplungsdeckel\\26-21051-02-01.h"
    copy = "TNC:\\Programme\\21053 abdeckung\\26-21051-02-01.h"
    c = make_collector()
    reference_run(c, 0, PROFILE, original)
    feed(c, (2000, snap("IDLE", copy)), (2001, snap("STARTED", copy, line_no=1, current_program=copy)))
    c.process(snap("STARTED", copy, line_no=900, current_program=copy), 2541)
    f = c.live()["forecast"]
    assert (f["method"], f["basis_runs"]) == ("profile", 1)  # Satzverlauf des Originals
    assert f["progress"] == pytest.approx(0.9, abs=0.02)
    # Ein fertiger Lauf der Kopie zählt umgekehrt auch für das Original
    c.process(snap("FINISHED", copy), 2602)
    feed(c, (3000, snap("IDLE", original)), (3001, snap("STARTED", original, line_no=1, current_program=original)))
    assert c.live()["forecast"]["basis_runs"] == 2


def test_profile_forecast_adapts_to_slower_pace(db, make_collector):
    c = make_collector()
    reference_run(c, 0, PROFILE)
    # Halbes Tempo (z. B. Override 50 %): Satz 150 erst nach 240 s statt 120 s
    feed(c, (2000, snap("IDLE")), (2001, snap("STARTED", line_no=1, current_program="P1")))
    c.process(snap("STARTED", line_no=150, current_program="P1"), 2241)
    f = c.live()["forecast"]
    assert f["method"] == "profile"
    assert f["remaining_s"] == pytest.approx(2 * (601 - 121), rel=0.05)


def test_forecast_when_joining_a_running_program(db, make_collector):
    c = make_collector()
    reference_run(c, 0, PROFILE)
    # Tool neu gestartet, Programm läuft schon: erster Kontakt bei Satz 900 (90 %)
    joined = make_collector()
    joined.process(snap("STARTED", line_no=900, current_program="P1"), 5000)
    joined.process(snap("STARTED", line_no=900, current_program="P1"), 5004)
    f = joined.live()["forecast"]
    assert joined.live()["run"]["start_observed"] is False
    assert f["method"] == "profile"
    assert f["remaining_s"] == pytest.approx(0.1 * 601, abs=5)  # kein Tempo aus 4 s Beobachtung


def test_no_history_forecast_when_start_unknown(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE")), (1, snap("STARTED")), (301, snap("FINISHED")))
    joined = make_collector()
    joined.process(snap("STARTED"), 1000)
    assert joined.live()["forecast"] is None


def test_block_forecast_when_joining_uses_observed_part(db, make_collector):
    c = make_collector()
    db.save_program_file("m1", "P1", 1000, 1.0, 1000, None, 0)
    c.process(snap("STARTED", line_no=500, current_program="P1"), 0)  # Einstieg bei 50 %
    c.process(snap("STARTED", line_no=600, current_program="P1"), 100)  # 10 % in 100 s
    f = c.live()["forecast"]
    assert f["method"] == "blocks"
    assert f["remaining_s"] == pytest.approx(400)


def test_history_forecast_without_block_numbers(db, make_collector):
    c = make_collector()
    for start in (0, 1000, 2000):
        feed(c, (start, snap("IDLE")), (start + 1, snap("STARTED")), (start + 301, snap("FINISHED")))
    feed(c, (5000, snap("IDLE")), (5001, snap("STARTED")), (5101, snap("STARTED")))
    f = c.live()["forecast"]
    assert (f["method"], f["basis_runs"]) == ("history", 3)
    assert f["remaining_s"] == pytest.approx(200)
    # Länger als üblich
    c.process(snap("STARTED"), 5401)
    f = c.live()["forecast"]
    assert f["remaining_s"] == 0
    assert f["overdue_s"] == pytest.approx(100)


def test_first_run_uses_block_count(db, make_collector):
    c = make_collector()
    db.save_program_file("m1", "P1", 1000, 1.0, 2000, None, 0)
    feed(c, (0, snap("IDLE")), (1, snap("STARTED", line_no=10, current_program="P1")))
    c.process(snap("STARTED", line_no=500, current_program="P1"), 101)  # 25 % nach 100 s
    live = c.live()
    assert live["blocks"] == {"program": "P1", "line": 500, "total": 2000, "error": None}
    f = live["forecast"]
    assert f["method"] == "blocks"
    assert f["remaining_s"] == pytest.approx(300)


def test_no_forecast_without_basis(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE")), (1, snap("STARTED", line_no=10)))
    assert c.live()["forecast"] is None


def test_stopped_time_is_not_run_time(db, make_collector):
    c = make_collector()
    feed(
        c,
        (0, snap("IDLE")),
        (10, snap("STARTED")),
        (20, snap("STOPPED")),
        (80, snap("STOPPED")),
        (90, snap("STARTED")),
        (100, snap("STARTED")),
    )
    assert c.live()["run"]["run_s"] == pytest.approx(10 + 10)
    [run] = db.runs(0, 200)
    assert run["run_s"] == pytest.approx(c.live()["run"]["run_s"])


def test_progress_sampling_is_thinned_and_pruned(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE")))
    for second in range(1, 101):  # jede Sekunde ein neuer Satz
        c.process(snap("STARTED", line_no=second, current_program="P1"), second)
    run_id = c.live()["run"]["id"]
    samples = db.run_progress(run_id)
    assert 9 <= len(samples) <= 11  # höchstens alle 10 s
    assert samples[0][2] == 1
    c.process(snap("FINISHED"), 101)
    # Abgebrochene Läufe liefern keinen Referenzverlauf und werden aufgeräumt
    feed(c, (200, snap("IDLE")), (201, snap("STARTED", line_no=1, current_program="P1")), (230, snap("CANCELLED")))
    assert db.run_progress(run_id)  # fertiger Referenzlauf bleibt
    cancelled_id = db.runs(0, 1000)[-1]["id"]
    assert db.run_progress(cancelled_id) == []


def test_restart_keeps_run_time(db, make_collector):
    first = make_collector()
    feed(first, (0, snap("IDLE")), (1, snap("STARTED")), (51, snap("STARTED")))
    second = make_collector()
    feed(second, (100, snap("STARTED")), (110, snap("STARTED")))
    assert second.live()["run"]["run_s"] == pytest.approx(50 + 10)


def test_forecaster_ignores_unrelated_programs(db):
    forecaster = Forecaster(db, "m1")
    assert forecaster.update(1, None, 10, None, 5, None) is None


# --- Programme im Hintergrund lesen -------------------------------------------------


class ProgramAdapter(NullAdapter):
    def __init__(self):
        self.calls = []

    def fetch_program(self, path, known, max_bytes):
        self.calls.append((path, known))
        if known == (2800, 5.0):
            return None
        return ProgramFile(path, 2800, 5.0, 100)


def test_programs_are_fetched_once_per_run(db):
    adapter = ProgramAdapter()
    c = MachineCollector(MACHINE, adapter, db)

    async def scenario():
        for t, s in [
            (0, snap("IDLE", "P1")),
            (1, snap("STARTED", "P1", line_no=5, current_program="SUB")),
            (2, snap("STARTED", "P1", line_no=6, current_program="SUB")),
        ]:
            c.process(s, t)
            c._maybe_fetch_programs(s)
            await asyncio.sleep(0.05)

    asyncio.run(scenario())
    # IDLE: P1 gelesen; Laufstart: P1 nur geprüft (unverändert), SUB neu gelesen; danach nichts mehr
    assert adapter.calls == [("P1", None), ("P1", (2800, 5.0)), ("SUB", None)]
    assert c.live()["blocks"] == {"program": "SUB", "line": 6, "total": 100, "error": None}
    assert db.program_blocks() == {("m1", "P1"): 100, ("m1", "SUB"): 100}


def test_program_fetch_can_be_disabled(db):
    adapter = ProgramAdapter()
    c = MachineCollector(MACHINE, adapter, db, fetch_programs=False)

    async def scenario():
        s = snap("IDLE", "P1")
        c.process(s, 0)
        c._maybe_fetch_programs(s)
        await asyncio.sleep(0.05)

    asyncio.run(scenario())
    assert adapter.calls == []


def test_sim_program_blocks_match_line_numbers():
    from app.adapters.sim_adapter import SimAdapter, SimulatedMachine

    sim = SimulatedMachine(seed=1, start=0, speed=1.0)
    adapter = SimAdapter(sim, clock=lambda: 0)
    lines = {}
    t = 0.0
    while t < 20 * 3600:
        s = sim.state_at(t)
        if s and s.line_no is not None and s.program:
            lines.setdefault(s.program, set()).add(s.line_no)
        t = sim.next_change
    for program, seen in lines.items():
        info = adapter.fetch_program(program, None, 10**9)
        assert max(seen) <= info.blocks
    assert collector_module.PROGRESS_SAMPLE_S == 10
