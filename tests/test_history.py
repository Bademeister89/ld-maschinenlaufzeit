"""Einmalige Bereinigung schon erfasster Läufe (Version 1.11.1)."""

from app import history
from app.db import Database

P = "TNC:\\AUFTRAG\\26-21048-01-01.H"


def run(db, intervals, result, observed=True, program=P, progress=None):
    """Lauf mit Zustandsabschnitten [(Zustand, Programmstatus, Betriebsart, von, bis, gehört zum Lauf)]."""
    run_id = db.start_run("m1", program, intervals[0][3], observed)
    for state, pgm_state, mode, t0, t1, own in intervals:
        iv = db.open_interval("m1", state, pgm_state, mode, program, run_id if own else None, t0, t1)
        db.close_interval(iv, t1)
    ended = max(t1 for *_, t1, own in intervals if own)
    db.end_run(run_id, ended, result)
    for t, line in progress or ():
        db.add_progress(run_id, t, program, line)
    return run_id


def results(db):
    return {r["id"]: (r["result"], r["start_observed"]) for r in db.runs(0, 1e12)}


def test_cleanup_applies_the_new_rules(tmp_path):
    db = Database(tmp_path / "alt.db")
    db.ensure_machine("m1", "DMU 105", "10.0.0.1", 19000)
    db.save_program_file("m1", P, 5000, 1.0, 92281, None, 0, ())
    # Programmende: aus dem Programmlauf direkt "inaktiv" – war "unterbrochen"
    end = run(db, [("RUNNING", "STARTED", "AUTOMATIC", 0, 3600, True), ("READY", "IDLE", "AUTOMATIC", 3600, 3700, False)], "aborted")
    # Störung, dann abgebrochen – Version 1.11.0 hat "fertig" daraus gemacht
    stop = run(db, [("RUNNING", "STARTED", "AUTOMATIC", 4000, 7000, True), ("STOPPED", "INTERRUPTED", "AUTOMATIC", 7000, 7300, True),
                    ("READY", "IDLE", "AUTOMATIC", 7300, 7400, False)], "finished")
    # MDI-Satz als Lauf gebucht
    mdi = run(db, [("RUNNING", "STARTED", "MDI", 8000, 8005, True), ("READY", "IDLE", "MDI", 8005, 8100, False)], "finished")
    # Satzvorlauf bei Satz 91059: eigener kurzer "fertiger" Lauf
    resume = run(db, [("RUNNING", "STARTED", "AUTOMATIC", 9000, 9360, True), ("READY", "IDLE", "AUTOMATIC", 9360, 9400, False)],
                 "finished", progress=[(0, 91059), (10, 91200)])
    # Palettentabelle als eigener Lauf
    table = run(db, [("RUNNING", "STARTED", "AUTOMATIC", 10000, 10047, True), ("READY", "IDLE", "AUTOMATIC", 10047, 10100, False)],
                "finished", program="TNC:\\Programme\\Palette.p")
    # Unverändert: "beendet" gemeldet, Abbruch mitten im Programm, Wechsel zum nächsten Programm
    ok = run(db, [("RUNNING", "STARTED", "AUTOMATIC", 11000, 12000, True), ("READY", "FINISHED", "AUTOMATIC", 12000, 12100, False)],
             "finished", progress=[(0, 15)])
    cancelled = run(db, [("RUNNING", "STARTED", "AUTOMATIC", 13000, 13100, True), ("READY", "CANCELLED", "AUTOMATIC", 13100, 13200, False)],
                    "cancelled")
    switched = run(db, [("RUNNING", "STARTED", "AUTOMATIC", 14000, 14100, True), ("RUNNING", "STARTED", "AUTOMATIC", 14100, 14200, False)],
                   "aborted")

    assert history.cleanup_runs(db) == {"verworfen": 2, "fertig": 1, "unterbrochen": 1, "teillauf": 1}
    after = results(db)
    assert after[end] == ("finished", 1)
    assert after[stop] == ("aborted", 1)
    assert after[resume] == ("finished", 0)  # zählt nicht mehr in die Ø-Stückzeit
    assert mdi not in after and table not in after
    assert {after[ok], after[cancelled], after[switched]} == {("finished", 1), ("cancelled", 1), ("aborted", 1)}
    # Die Zeit bleibt erhalten, nur ohne Lauf
    assert db._query("SELECT COUNT(*) AS n FROM state_intervals WHERE started_at = 8000 AND run_id IS NULL")[0]["n"] == 1
    # Nur einmal
    assert history.cleanup_runs(db) == {}
    db.close()
