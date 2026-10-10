"""Befunde aus der Diagnose vom 10.10.: kurze Verbindungsabbrüche, sehr große Programme, Satzvorlauf
aus dem Ordner der Palettentabelle, seltener fragen im Leerlauf."""

from .conftest import feed, snap

A = "TNC:\\Programme\\21048\\26-21048-02-01.h"
COPY = "TNC:\\Programme\\21051\\26-21048-02-01.h"  # dieselbe Datei im Ordner der Palettentabelle
SMALL = "TNC:\\Programme\\21048\\26-21048-02-02.h"


def runs(db):
    return db.runs(0, 1e9)


def test_short_connection_gap_keeps_the_start_observed(db, make_collector):
    """DMU 70, 9.10. 16:19: angewählt, Zeitüberschreitung, nach 14 s wieder verbunden – da lief das
    Programm schon (Satz 15). Bisher galt der Start als nicht beobachtet: Der 6,5-h-Lauf fehlte in der Ø-Zeit."""
    c = make_collector()
    feed(c, (0, snap("IDLE", A)), (2, snap("IDLE", A)), (6, None), (16, snap("STARTED", A, line_no=15)))
    feed(c, (5000, snap("IDLE", A, line_no=0)))
    [run] = runs(db)
    assert run["start_observed"] == 1


def test_long_gap_start_is_not_observed(db, make_collector):
    """Maschine war aus (oder lange nicht erreichbar): Wann das Programm begann, ist unbekannt."""
    c = make_collector()
    feed(c, (0, snap("IDLE", A)), (6, None), (600, snap("STARTED", A, line_no=15)), (900, snap("IDLE", A, line_no=0)))
    [run] = runs(db)
    assert run["start_observed"] == 0


def test_false_start_of_a_program_too_big_to_count_is_not_finished(db, make_collector):
    """DMU 70, 8.10. 16:21: 26-21048-02-01 (25,9 MB, Satzanzahl unbekannt) nach 18 s bis Satz 33 –
    bisher „fertig“ mit 18 s und in der Ø-Zeit."""
    c = make_collector()
    db.save_program_file("m1", A, 25_900_000, 1.0, None, "Programm zu groß zum Einlesen (25.9 MB)", 0)
    feed(c, (0, snap("IDLE", A)), (2, snap("STARTED", A, line_no=18)), (20, snap("STARTED", A, line_no=33)))
    feed(c, (22, snap("IDLE", A, line_no=0)))
    [run] = runs(db)
    assert run["result"] == "aborted"


def test_program_with_unknown_size_still_counts_as_finished(db, make_collector):
    """Ohne Satzanzahl und ohne bekannte Größe (z. B. noch nicht gelesen) bleibt es wie bisher."""
    c = make_collector()
    feed(c, (0, snap("IDLE", SMALL)), (2, snap("STARTED", SMALL, line_no=5)), (40, snap("IDLE", SMALL, line_no=0)))
    [run] = runs(db)
    assert run["result"] == "finished"


def test_block_scan_from_the_copy_in_the_pallet_folder_resumes_the_run(db, make_collector):
    """DMU 70, 8.10.: Lauf aus dem Auftragsordner bis Satz 50.364, nach 8 min per Satzvorlauf ab Satz
    50.367 weiter – angewählt über die Palettentabelle, also die Kopie im Ordner 21051."""
    c = make_collector()
    db.save_program_file("m1", A, 25_900_000, 1.0, None, "Programm zu groß zum Einlesen (25.9 MB)", 0)
    feed(
        c,
        (0, snap("IDLE", A)),
        (2, snap("STARTED", A, line_no=32)),
        (1650, snap("STARTED", A, line_no=50364)),
        (1652, snap("IDLE", A, line_no=0)),
        (2100, snap("IDLE", COPY)),
        (2130, snap("STARTED", COPY, line_no=50367)),
        (24000, snap("IDLE", COPY, line_no=0)),
    )
    [run] = runs(db)
    assert run["start_observed"] == 1 and run["result"] == "finished"
    assert run["run_s"] > 1600 + 21000
    assert [e["type"] for e in db.events(0, 1e9) if e["type"] == "run_resumed"] == ["run_resumed"]


def test_idle_machine_is_polled_less_often(db, make_collector):
    c = make_collector()
    feed(c, (0, snap("IDLE", A)))
    assert c.next_poll_s(100) == 2
    assert c.next_poll_s(300) == 5  # 5 min bereit ohne Änderung
    feed(c, (310, snap("IDLE", SMALL)))  # anderes Programm angewählt: jemand arbeitet an der Maschine
    assert c.next_poll_s(320) == 2
    feed(c, (700, snap("STARTED", SMALL, line_no=5)))
    assert c.next_poll_s(2000) == 2  # Programm läuft: immer im normalen Takt
    feed(c, (2000, snap("IDLE", SMALL, line_no=0)))
    assert (c.next_poll_s(2100), c.next_poll_s(2300)) == (2, 5)
