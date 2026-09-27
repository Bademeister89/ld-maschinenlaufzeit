from app.collector import MachineCollector

from .conftest import NullAdapter, feed, snap


def intervals(db, machine_id="m1"):
    return db._query(
        "SELECT state, pgm_state, program, run_id, started_at, ended_at, last_seen FROM state_intervals "
        "WHERE machine_id = ? ORDER BY id",
        (machine_id,),
    )


def runs(db):
    return db.runs(0, 10_000)


def test_same_state_extends_interval(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED")), (2, snap("STARTED")), (4, snap("STARTED")), (6, snap("STOPPED")))
    rows = intervals(db)
    assert [(r["state"], r["started_at"], r["ended_at"]) for r in rows] == [
        ("RUNNING", 0, 6),
        ("STOPPED", 6, None),
    ]
    assert rows[1]["last_seen"] == 6


def test_run_lifecycle_counts_run_and_stop_time(db, make_collector):
    c = make_collector()
    feed(
        c,
        (0, snap("IDLE")),
        (10, snap("STARTED")),
        (20, snap("STARTED")),
        (30, snap("STOPPED")),
        (40, snap("STARTED")),
        (100, snap("FINISHED")),
        (110, snap("FINISHED")),
    )
    [run] = runs(db)
    assert (run["started_at"], run["ended_at"], run["result"]) == (10, 100, "finished")
    assert run["run_s"] == (30 - 10) + (100 - 40)
    assert run["stop_s"] == 10
    assert run["start_observed"] == 1
    assert c.live()["run"] is None


def test_going_offline_ends_state_at_last_contact(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED")), (2, snap("STARTED")), (10, None), (15, None))
    rows = intervals(db)
    assert [(r["state"], r["started_at"], r["ended_at"]) for r in rows] == [
        ("RUNNING", 0, 2),
        ("OFFLINE", 2, None),
    ]
    assert rows[1]["last_seen"] == 15


def test_short_offline_does_not_split_run(db, make_collector):
    c = make_collector()
    feed(
        c,
        (0, snap("STARTED")),
        (2, snap("STARTED")),
        (4, None),
        (9, None),
        (10, snap("STARTED")),
        (20, snap("FINISHED")),
    )
    [run] = runs(db)
    assert (run["started_at"], run["ended_at"], run["result"]) == (0, 20, "finished")
    assert run["run_s"] == 2 + 10


def test_finished_while_offline_ends_run_at_last_contact(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED")), (2, snap("STARTED")), (4, None), (10, snap("FINISHED")))
    [run] = runs(db)
    assert (run["ended_at"], run["result"], run["run_s"]) == (2, "finished", 2)


def test_restart_continues_open_run_and_leaves_gap(db, make_collector):
    first = make_collector()
    feed(first, (0, snap("IDLE")), (1, snap("STARTED")), (3, snap("STARTED")))
    # Tool abgestürzt/neu gestartet: neuer Collector auf derselben Datenbank
    second = make_collector()
    rows = intervals(db)
    assert rows[-1]["ended_at"] == 3  # offenes Intervall am letzten Lebenszeichen geschlossen
    feed(second, (50, snap("STARTED")), (60, snap("FINISHED")))
    [run] = runs(db)
    assert (run["started_at"], run["ended_at"], run["result"]) == (1, 60, "finished")
    assert run["run_s"] == 2 + 10
    assert run["start_observed"] == 1


def test_run_already_running_at_startup_is_marked_unobserved(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED")), (5, snap("FINISHED")))
    [run] = runs(db)
    assert run["start_observed"] == 0


def test_error_during_run(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE")), (1, snap("STARTED")), (5, snap("ERROR")), (10, snap("ERROR_CLEARED")))
    [run] = runs(db)
    assert (run["result"], run["had_error"], run["run_s"], run["stop_s"]) == ("error", 1, 4, 5)


def test_program_change_starts_new_run(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE")), (1, snap("STARTED", "P1")), (10, snap("STARTED", "P2")), (20, snap("FINISHED", "P2")))
    first, second = runs(db)
    assert (first["program"], first["ended_at"], first["result"]) == ("P1", 10, "aborted")
    assert (second["program"], second["started_at"], second["ended_at"], second["result"]) == ("P2", 10, 20, "finished")


def test_unknown_state_suspends_but_does_not_end_run(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE")), (1, snap("STARTED")), (3, snap("UNDEFINED")), (5, snap("STARTED")), (9, snap("FINISHED")))
    [run] = runs(db)
    assert (run["started_at"], run["ended_at"], run["run_s"]) == (1, 9, 2 + 4)


def test_events(db, make_collector):
    c = make_collector()
    feed(
        c,
        (0, snap("STARTED", tool="T1 BOHRER")),
        (2, snap("STARTED", tool="T5 FRAESER")),
        (4, snap("ERROR", tool="T5 FRAESER", errors=("Werkzeugbruch",))),
        (6, snap("ERROR", tool="T5 FRAESER", errors=("Werkzeugbruch",))),
        (8, None),
        (10, None),
    )
    ev = sorted(db.events(0, 100), key=lambda e: e["ts"])
    assert [(e["ts"], e["type"]) for e in ev] == [(2, "tool_change"), (4, "nc_error"), (8, "offline")]
    assert ev[0]["payload"] == {"from": "T1 BOHRER", "to": "T5 FRAESER", "program": "P1"}


def test_live_status(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE")), (10, snap("STARTED", line_no=42, tool="T3")), (12, snap("STARTED", line_no=50, tool="T3")))
    live = c.live()
    assert live["state"] == "RUNNING"
    assert live["state_since"] == 10
    assert live["line_no"] == 50
    assert live["run"]["started_at"] == 10
    assert live["run"]["program"] == "P1"


def test_machines_are_separated(db):
    from app.config import MachineConfig

    a = MachineCollector(MachineConfig("a", "A", "h"), NullAdapter(), db)
    b = MachineCollector(MachineConfig("b", "B", "h"), NullAdapter(), db)
    feed(a, (0, snap("STARTED")))
    feed(b, (0, snap("IDLE")))
    feed(a, (5, snap("FINISHED")))
    assert [r["state"] for r in intervals(db, "a")] == ["RUNNING", "READY"]
    assert [r["state"] for r in intervals(db, "b")] == ["READY"]
