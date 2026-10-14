"""Gestartet, aber der Satz steht (DMU 70, 14.10.: PNEUMATIKDRUCK UNTERSCHRITTEN, Status blieb "gestartet")."""

from app.collector import STALL_MESSAGE_S, STALL_S

from .conftest import feed, snap

PRESSURE = "PNEUMATIKDRUCK UNTERSCHRITTEN"


def intervals(db):
    return [
        (r["state"], r["pgm_state"], r["started_at"], r["ended_at"])
        for r in db._query("SELECT * FROM state_intervals WHERE machine_id = 'm1' ORDER BY id")
    ]


def standing(t0: float, t1: float, line: int, step: float = 2, **kw):
    """Abfragen von t0 bis t1, der Satz bleibt stehen."""
    t = t0
    while t <= t1:
        yield (t, snap("STARTED", line_no=line, **kw))
        t += step


def test_new_message_while_block_stands_counts_as_stopped_from_last_block_change(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED", line_no=10)), (500, snap("STARTED", line_no=20)))
    feed(c, *standing(502, 838, 20))
    assert c.live()["state"] == "RUNNING"  # langer Satz ohne Meldung: läuft noch

    feed(c, (839, snap("STARTED", line_no=20, errors=(PRESSURE,))))
    live = c.live()
    assert live["state"] == "STOPPED"
    assert live["pgm_state"] == "STARTED"
    assert live["state_since"] == 500  # ab dem letzten Satzwechsel
    assert live["stall"] == {"since": 500, "detected_at": 839, "line": 20, "reason": "message"}
    assert intervals(db) == [("RUNNING", "STARTED", 0, 500), ("STOPPED", "STARTED", 500, None)]
    assert c.live()["run"]["run_s"] == 500

    # Druck wieder da, es geht weiter: läuft wieder ab dem Satzwechsel
    feed(c, (900, snap("STARTED", line_no=20, errors=(PRESSURE,))), (902, snap("STARTED", line_no=21)))
    assert c.live()["state"] == "RUNNING"
    assert c.live()["stall"] is None
    assert intervals(db)[-2:] == [("STOPPED", "STARTED", 500, 902), ("RUNNING", "STARTED", 902, None)]

    feed(c, (1000, snap("FINISHED")))
    [run] = db.runs(0, 10_000)
    assert run["run_s"] == 500 + (1000 - 902)
    assert run["stop_s"] == 902 - 500

    [event] = db._query("SELECT payload FROM events WHERE type = 'stall'")
    assert PRESSURE in event["payload"]


def test_block_standing_long_without_message_counts_as_stopped(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED", line_no=10)), (100, snap("STARTED", line_no=11)))
    feed(c, *standing(102, 100 + STALL_S - 2, 11, step=10))
    assert c.live()["state"] == "RUNNING"
    feed(c, (100 + STALL_S, snap("STARTED", line_no=11)))
    assert c.live()["state"] == "STOPPED"
    assert c.live()["stall"]["reason"] == "time"
    assert intervals(db) == [("RUNNING", "STARTED", 0, 100), ("STOPPED", "STARTED", 100, None)]
    assert c.live()["run"]["run_s"] == 100


def test_message_shortly_after_block_change_is_not_a_stall(db, make_collector):
    c = make_collector()
    feed(
        c,
        (0, snap("STARTED", line_no=10)),
        (STALL_MESSAGE_S - 10, snap("STARTED", line_no=10, errors=("Puffer-Batterie wechseln",))),
        (STALL_MESSAGE_S - 2, snap("STARTED", line_no=10, errors=("Puffer-Batterie wechseln",))),
        (STALL_MESSAGE_S, snap("STARTED", line_no=11, errors=("Puffer-Batterie wechseln",))),
    )
    assert c.live()["state"] == "RUNNING"
    # Die Meldung stand schon beim Satzwechsel an: kein neuer Hinweis auf einen Stillstand
    feed(c, *standing(STALL_MESSAGE_S + 2, 400, 11, errors=("Puffer-Batterie wechseln",)))
    assert c.live()["state"] == "RUNNING"
    assert [r[0] for r in intervals(db)] == ["RUNNING"]


def test_no_stall_in_manual_mode_or_without_block_number(db, make_collector):
    c = make_collector()
    feed(c, *standing(0, STALL_S + 10, 5, step=10, exec_mode="MDI", errors=()))
    feed(c, (STALL_S + 20, snap("STARTED", exec_mode="MDI", line_no=5, errors=(PRESSURE,))))
    assert c.live()["state"] == "RUNNING"
    feed(c, (STALL_S + 30, snap("STARTED")), (2 * STALL_S + 40, snap("STARTED", errors=(PRESSURE,))))
    assert c.live()["state"] == "RUNNING"


def test_tool_use_ends_at_last_block_change(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("STARTED", line_no=1, tool="T5")), (100, snap("STARTED", line_no=2, tool="T5")))
    feed(c, *standing(102, 300, 2, tool="T5"))
    feed(c, (302, snap("STARTED", line_no=2, tool="T5", errors=(PRESSURE,))))
    [use] = db._query("SELECT started_at, ended_at FROM tool_usage")
    assert (use["started_at"], use["ended_at"]) == (0, 100)
