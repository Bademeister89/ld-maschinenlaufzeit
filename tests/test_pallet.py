"""Ablaufliste des Palettenprogramms (.P) auf der Live-Seite: Reihenfolge, Stand und erwartete Zeiten."""

import asyncio
import sqlite3

import pytest

from app.collector import MachineCollector
from app.db import SCHEMA_VERSION, Database
from app.nc_program import PalletEntry, ProgramFile, pallet_entries, program_calls

from .conftest import MACHINE, NullAdapter, feed, snap

FOLDER = "TNC:\\Programme\\21 Motor\\21053 abdeckung rechts 1 cvo"
TABLE = f"{FOLDER}\\pal1sp.p"
ORDER = f"{FOLDER}\\26-21053-01-01.h"
DREH = f"{FOLDER}\\Dreh.h"
DREH_ELSEWHERE = "TNC:\\Programme\\maschine\\Dreh.h"
P_ENDE = f"{FOLDER}\\P-Ende.h"
MACRO = "PLC:\\PLC\\Palett.H"  # Palettenwechsel


def table(rows, type_column="TYPE"):
    """Palettentabelle mit festen Spaltenbreiten wie auf der iTNC 530, mit den Spalten LOCK und W-STATE.
    Zeile: (Typ, Name[, LOCK[, W-STATE]])."""
    lines = ["BEGIN PAL1SP .P MM", f"{'NR':<5}{type_column:<9}{'NAME':<72}{'DATUM':<10}{'LOCK':<6}{'W-STATE':<12}{'X':<10}"]
    for nr, (kind, name, *rest) in enumerate(rows):
        lock, state = (*rest, "", "")[:2]
        lines.append(f"{nr:<5}{kind:<9}{name:<72}{'':<10}{lock:<6}{state:<12}{'+0':<10}")
    return "\n".join([*lines, "[END]"]) + "\n"


# Wie im Feldtest an der DMU 70: je Palette Auftragsprogramm und Drehen, am Ende P-Ende
FIELD_ROWS = [
    ("PAL", "1"), ("PGM", ORDER), ("PGM", "DREH.H"),
    ("PAL", "2"), ("PGM", ORDER), ("PGM", "DREH.H"),
    ("PAL", "3"), ("PGM", ORDER), ("PGM", "DREH.H"),
    ("PAL", "0"), ("PGM", "P-ENDE.H"),
]


def test_pallet_entries_in_order_with_pallets_and_locks():
    text = table([
        ("PAL", "1"), ("PGM", ORDER), ("PGM", "DREH.H"),
        ("PAL", "2", "*"), ("PGM", ORDER), ("PGM", "DREH.H"),  # ganze Palette gesperrt
        ("PAL", "3"), ("FIX", "SPANNER1", "*"), ("PGM", ORDER),  # Spannmittel gesperrt
        ("FIX", "SPANNER2"), ("PGM", ORDER, "", "EMPTY"), ("PGM", "DREH.H", "", "ENDED"),
        ("PAL", "0"), ("PGM", "P-ENDE.H", "*"),
    ])
    assert pallet_entries(text) == (
        PalletEntry(ORDER, "1"), PalletEntry("DREH.H", "1"),
        PalletEntry(ORDER, "2", True), PalletEntry("DREH.H", "2", True),
        PalletEntry(ORDER, "3", True), PalletEntry(ORDER, "3", True), PalletEntry("DREH.H", "3"),
        PalletEntry("P-ENDE.H", "0", True),
    )
    # Die Aufrufliste (Oberprogramm-Erkennung) bleibt wie bisher: jeder Name einmal
    assert program_calls(TABLE, text) == ("1", "26-21053-01-01", "DREH", "2", "3", "SPANNER1", "SPANNER2", "0", "P-ENDE")


def test_pallet_entries_other_type_column_and_unknown_format():
    assert pallet_entries(table([("PAL", "7"), ("PGM", "A.H"), ("PGM", "A.H")], "PAL/PGM")) == (
        PalletEntry("A.H", "7"), PalletEntry("A.H", "7"),  # dasselbe Programm zweimal: zwei Zeilen
    )
    assert pallet_entries(f"BEGIN PAL1SP .P MM\n1 PGM {ORDER} +0\n[END]\n") == ()  # ohne Kopfzeile


def pal(state, current, line=None):
    return snap(state, TABLE, current_program=current, line_no=line)


def history(db, c):
    """Frühere Läufe: Auftragsprogramm 840 s, Drehen (anderes Verzeichnis) 45 s; Tabelle gelesen."""
    text = table(FIELD_ROWS)
    db.save_program_file("m1", TABLE, len(text), 1.0, None, None, 0, program_calls(TABLE, text), text)
    feed(
        c,
        (0, snap("IDLE", ORDER)),
        (10, snap("STARTED", ORDER, current_program=ORDER)),
        (850, snap("FINISHED", ORDER)),
        (860, snap("IDLE", DREH_ELSEWHERE)),
        (870, snap("STARTED", DREH_ELSEWHERE, current_program=DREH_ELSEWHERE)),
        (915, snap("FINISHED", DREH_ELSEWHERE)),
    )


def view(c):
    info = c.live()["pallet"]
    return [(e["status"], e["expected_s"], e["source"]) for e in info["entries"]], info


def test_pallet_list_follows_the_table_and_learns_times(db, make_collector):
    c = make_collector()
    history(db, c)

    feed(c, (1000, pal("IDLE", None)))  # Palettenprogramm angewählt, noch nicht gestartet
    entries, info = view(c)
    assert entries == [
        ("pending", 840, "history"), ("pending", 45, "history"),  # Drehen: gleicher Name, anderes Verzeichnis
        ("pending", 840, "history"), ("pending", 45, "history"),
        ("pending", 840, "history"), ("pending", 45, "history"),
        ("pending", None, None),  # P-Ende lief noch nie
    ]
    assert (info["table"], info["position"], info["total_s"], info["unknown"]) == (TABLE, None, 3 * 885, 1)
    assert info["remaining_s"] is None and info["eta"] is None
    assert [e["pallet"] for e in info["entries"]] == ["1", "1", "2", "2", "3", "3", "0"]

    feed(
        c,
        (1010, pal("STARTED", MACRO)),  # Palette 1 einwechseln
        (1080, pal("STARTED", ORDER, 15)),
    )
    entries, info = view(c)
    assert [e[0] for e in entries] == ["current"] + ["pending"] * 6
    # Rest: Prognose des laufenden Programms (840) + 2 × 840 + 3 × 45, P-Ende unbekannt
    assert (info["remaining_s"], info["remaining_unknown"], info["eta"]) == (3 * 885, 1, 1080 + 3 * 885)

    feed(
        c,
        (1500, pal("STARTED", ORDER, 9000)),
        (1920, pal("STARTED", DREH, 1)),  # Palette 1 drehen
        (1970, pal("STARTED", MACRO)),  # Palette 2 einwechseln
        (2040, pal("STARTED", ORDER, 15)),
    )
    entries, info = view(c)
    # Gemessen je Zeile bis zur nächsten (Drehen mit Palettenwechsel: 50 + 70 s)
    assert entries[:4] == [("done", 840, "measured"), ("done", 120, "measured"), ("current", 840, "measured"), ("pending", 120, "measured")]
    assert [e["started_at"] for e in info["entries"]] == [1080, 1920, 2040, None, None, None, None]
    assert (info["remaining_s"], info["remaining_unknown"]) == (840 + 120 + 840 + 120, 1)
    # Bis wann: fertige Zeilen bis zum Beginn der nächsten, offene aufsummiert ab jetzt; nach dem
    # unbekannten P-Ende nur „frühestens“
    assert [e["until"] for e in info["entries"]] == [1920, 2040, 2880, 3000, 3840, 3960, 3960]
    assert [e["until_min"] for e in info["entries"]] == [False] * 6 + [True]

    # Störung: Palettenprogramm abgebrochen und per Satzvorlauf an derselben Palette fortgesetzt
    feed(
        c,
        (2400, pal("STOPPED", ORDER, 12000)),
        (2450, pal("IDLE", None, 0)),
        (2500, pal("STARTED", ORDER, 12010)),
    )
    assert view(c)[1]["position"] == 2
    feed(
        c,
        (2900, pal("STARTED", DREH, 1)),  # Palette 2: 360 + 400 s gelaufen (Stillstand zählt nicht)
        (2950, pal("STARTED", MACRO)),
        (3020, pal("STARTED", ORDER, 15)),
        (3860, pal("STARTED", DREH, 1)),
        (3910, pal("STARTED", P_ENDE, 1)),
        (3920, pal("STARTED", MACRO)),
    )
    entries, info = view(c)
    assert entries[0] == ("done", 840, "measured")  # Median aus 840, 760, 840
    assert entries[6] == ("current", None, None)
    assert info["remaining_unknown"] == 1

    feed(c, (3990, pal("IDLE", None, 0)))  # Palettenprogramm zu Ende
    entries, info = view(c)
    assert [e[0] for e in entries] == ["done"] * 7
    assert info["remaining_s"] is None

    # Neustart: wieder von vorn, gemessene Zeiten bleiben
    feed(c, (4100, pal("STARTED", MACRO)), (4170, pal("STARTED", ORDER, 15)))
    entries, info = view(c)
    assert entries[0] == ("current", 840, "measured") and info["position"] == 0
    assert [e["started_at"] for e in info["entries"]] == [4170] + [None] * 6  # neuer Durchgang


def test_locked_pallet_is_skipped(db, make_collector):
    c = make_collector()
    rows = [("PAL", "1"), ("PGM", ORDER), ("PGM", "DREH.H"), ("PAL", "2", "*"), ("PGM", ORDER), ("PGM", "DREH.H"),
            ("PAL", "3"), ("PGM", ORDER), ("PGM", "DREH.H")]
    text = table(rows)
    db.save_program_file("m1", TABLE, len(text), 1.0, None, None, 0, program_calls(TABLE, text), text)
    feed(
        c,
        (0, pal("IDLE", None)),
        (10, pal("STARTED", ORDER)),
        (100, pal("STARTED", DREH)),
        (130, pal("STARTED", MACRO)),
        (170, pal("STARTED", ORDER)),
    )
    entries, info = view(c)
    assert [e[0] for e in entries] == ["done", "done", "skipped", "skipped", "current", "pending"]
    assert entries[1] == ("done", 70, "measured")  # Drehen gemessen bis zur nächsten freien Zeile (Palette 3)
    assert info["total_s"] == 2 * (90 + 70) and info["unknown"] == 0  # gesperrte Palette zählt nicht


def test_start_of_the_collector_mid_table_does_not_measure(db, make_collector):
    """Nach einem Neustart der App ist der Beginn der laufenden Zeile nicht gesehen."""
    c = make_collector()
    text = table(FIELD_ROWS)
    db.save_program_file("m1", TABLE, len(text), 1.0, None, None, 0, program_calls(TABLE, text), text)
    feed(c, (0, pal("STARTED", ORDER, 20000)), (300, pal("STARTED", DREH, 1)))
    entries, info = view(c)
    assert entries[0] == ("done", None, None) and entries[1][0] == "current"
    assert (info["entries"][0]["started_at"], info["entries"][0]["started_observed"]) == (0, False)


def test_restart_of_the_app_continues_at_the_running_pallet(db, make_collector):
    """Update am 06.10. um 17:05 mitten in pal2sp.p: Die Liste begann bei der ersten Zeile – und weil
    die Tabelle in dem Moment noch nicht neu gelesen war, wurde gar keine Zeile markiert."""
    first = make_collector()
    history(db, first)
    feed(
        first,
        (1000, pal("IDLE", None)),
        (1010, pal("STARTED", MACRO)),
        (1080, pal("STARTED", ORDER, 15)),
        (1920, pal("STARTED", DREH, 1)),
        (1970, pal("STARTED", MACRO)),
        (2040, pal("STARTED", ORDER, 15)),  # Palette 2
        (2100, pal("STARTED", ORDER, 900)),
    )
    second = make_collector()  # Neustart der App
    feed(second, (2400, pal("STARTED", ORDER, 9000)))
    entries, info = view(second)
    assert [e[0] for e in entries] == ["done", "done", "current"] + ["pending"] * 4
    started = [(e["started_at"], e["started_observed"]) for e in info["entries"][:3]]
    assert started == [(1080, True), (None, None), (2040, True)]  # Palette 2 läuft seit 2040
    feed(second, (2900, pal("STARTED", DREH, 1)))
    assert view(second)[1]["position"] == 3


def test_table_read_after_the_start_still_marks_the_running_line(db, make_collector):
    c = make_collector()
    feed(c, (0, pal("STARTED", MACRO)), (60, pal("STARTED", ORDER, 15)))
    assert c.live()["pallet"] is None  # Tabelle noch nicht gelesen
    text = table(FIELD_ROWS)
    db.save_program_file("m1", TABLE, len(text), 1.0, None, None, 0, program_calls(TABLE, text), text)
    c._programs.clear()  # wie nach dem Einlesen
    feed(c, (62, pal("STARTED", ORDER, 40)))
    entries, info = view(c)
    assert entries[0][0] == "current" and info["entries"][0]["started_at"] == 60  # Beginn des Laufs


def test_program_without_history_uses_the_forecast_of_the_running_run(db, make_collector):
    """26-21051-02-01 lief noch nie fertig: Statt „mind. 47 s“ je Palette die Prognose des laufenden
    Laufs (bisherige Laufzeit + Rest) auch für die weiteren Paletten."""
    c = make_collector()
    text = table(FIELD_ROWS)
    db.save_program_file("m1", TABLE, len(text), 1.0, None, None, 0, program_calls(TABLE, text), text)
    db.save_program_file("m1", ORDER, 9000, 1.0, 1000, None, 0, ())
    feed(c, (0, pal("IDLE", None)), (10, pal("STARTED", ORDER, 10)), (110, pal("STARTED", ORDER, 100)))
    entries, info = view(c)
    # Grobe Schätzung aus der Satznummer: 10 % in 100 s → Rest 900 s, gesamt 1000 s
    assert entries[0] == ("current", 1000, "forecast") and entries[2] == ("pending", 1000, "forecast")
    assert (info["remaining_s"], info["remaining_unknown"]) == (900 + 2 * 1000, 4)


def test_no_list_without_readable_table(db, make_collector):
    c = make_collector()
    feed(c, (0, pal("STARTED", ORDER)))
    assert c.live()["pallet"] is None  # Tabelle noch nicht gelesen
    db.save_program_file("m1", TABLE, 10, 1.0, None, None, 0, (), "kein Tabellenformat")
    c._programs.clear()
    assert c.live()["pallet"] is None
    feed(c, (10, snap("STARTED", ORDER, current_program=ORDER)))  # kein Palettenprogramm angewählt
    assert c.live()["pallet"] is None


class TableAdapter(NullAdapter):
    def fetch_program(self, path, known, max_bytes):
        if path == TABLE:
            text = table(FIELD_ROWS)
            return ProgramFile(path, len(text), 1.0, None, None, program_calls(path, text), text)
        return ProgramFile(path, 500, 1.0, 9, calls=())


def test_fetched_pallet_table_keeps_its_text(db):
    c = MachineCollector(MACHINE, TableAdapter(), db)

    async def scenario():
        s = pal("IDLE", None)
        c.process(s, 0)
        c._maybe_fetch_programs(s)
        for _ in range(20):
            await asyncio.sleep(0.01)
            if c._fetch_task.done():
                break

    asyncio.run(scenario())
    assert db.program_file("m1", TABLE)["content"] == table(FIELD_ROWS)
    assert len(c.live()["pallet"]["entries"]) == 7


def test_update_rereads_pallet_tables_for_their_text(tmp_path):
    """Datenbank wie in Version 1.13.0 (Schema 10): Palettentabellen ohne Text."""
    path = tmp_path / "v10.db"
    db = Database(path)
    db.ensure_machine("m1", "DMG 1", "10.0.0.1", 19000)
    db.save_program_file("m1", TABLE, 800, 1.0, None, None, 0, ("26-21053-01-01", "DREH"))
    db.save_program_file("m1", ORDER, 900, 1.0, 26395, None, 0, ())
    db.close()
    con = sqlite3.connect(path)
    con.executescript("ALTER TABLE program_files DROP COLUMN content; UPDATE meta SET value = '10' WHERE key = 'schema_version';")
    con.close()
    db = Database(path)
    assert db.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert db.program_file("m1", TABLE)["size"] is None  # wird neu gelesen
    assert db.program_file("m1", ORDER)["size"] == 900  # Programme bleiben
    db.close()


FIXTURE = f"{FOLDER}\\Vorrichtung\\26-21053-02-v-01.h"


def test_night_of_06_10_restart_after_interruption(db, make_collector):
    """DMU 70, 6./7.10.: Palette 8 unterbrochen (Satz 66865), dazwischen die Vorrichtung und ein Fehlstart
    (bis Satz 34), dann Neueinstieg per Satzvorlauf bei Satz 66851. Bisher: Fehlstart „fertig“, der
    Neueinstieg ein Teillauf ohne beobachteten Start – für Palette 9 gab es nur die grobe Schätzung."""
    c = make_collector()
    text = table(FIELD_ROWS)
    db.save_program_file("m1", TABLE, len(text), 1.0, None, None, 0, program_calls(TABLE, text), text)
    db.save_program_file("m1", ORDER, 30_000_000, 1.0, 514_998, None, 0, ())
    feed(
        c,
        (0, pal("IDLE", None)),
        (10, pal("STARTED", MACRO, 6)),
        (70, pal("STARTED", ORDER, 15)),  # Palette 1
        (2000, pal("STARTED", ORDER, 65662)),
        (2070, pal("STOPPED", ORDER, 66865)),
        (2073, pal("IDLE", None, 0)),  # abgebrochen
        (2200, snap("IDLE", FIXTURE)),
        (2210, snap("STARTED", FIXTURE, current_program=FIXTURE, line_no=10)),
        (2320, snap("IDLE", FIXTURE, line_no=0)),
        (2400, pal("IDLE", None)),
        (2402, pal("STARTED", MACRO, 6)),
        (2413, pal("STARTED", ORDER, 15)),  # Fehlstart …
        (2425, pal("STARTED", ORDER, 34)),
        (2427, pal("IDLE", None, 0)),
        (2432, pal("STARTED", ORDER, 66851)),  # … und Neueinstieg per Satzvorlauf
    )
    entries, info = view(c)
    assert entries[0][0] == "current"
    assert info["entries"][0]["started_at"] == 70  # Palette 1 läuft seit dem ersten Start
    feed(
        c,
        (8000, pal("STARTED", ORDER, 300_000)),
        (15000, pal("STARTED", ORDER, 514_995)),
        (15002, pal("STARTED", DREH, 1)),
        (15050, pal("STARTED", MACRO, 6)),
        (15120, pal("STARTED", ORDER, 15)),  # Palette 2
        (15200, pal("STARTED", ORDER, 3000)),
    )
    first, fixture, false_start, second = db.runs(0, 100_000)
    assert (first["started_at"], first["ended_at"], first["result"], first["start_observed"]) == (70, 15002, "finished", 1)
    assert first["run_s"] == pytest.approx(2000 + 12570)  # beide Teile, ohne die Unterbrechung
    assert (false_start["program"], false_start["result"]) == (ORDER, "aborted")
    assert fixture["program"] == FIXTURE
    assert second["start_observed"] == 1
    assert c.live()["forecast"]["method"] != "blocks"  # Prognose aus dem vollständigen ersten Lauf
    entries, info = view(c)
    # Palette 1 nicht gemessen (Beginn vor dem Neueinstieg), dafür die Laufzeit des fertigen Laufs
    assert entries[4] == ("pending", pytest.approx(14570), "history")
    assert [e["order"] for e in info["entries"]] == [True, False, True, False, True, False, False]


def test_abort_right_after_the_start_is_not_finished(db, make_collector):
    """Die iTNC 530 geht dabei direkt auf „inaktiv“ – wie am Programmende."""
    c = make_collector()
    db.save_program_file("m1", ORDER, 30_000_000, 1.0, 514_998, None, 0, ())
    db.save_program_file("m1", DREH, 570, 1.0, 18, None, 0, ())
    feed(
        c,
        (0, snap("IDLE", ORDER)),
        (10, snap("STARTED", ORDER, current_program=ORDER, line_no=15)),
        (22, snap("STARTED", ORDER, current_program=ORDER, line_no=34)),
        (24, snap("IDLE", ORDER, line_no=0)),
        (30, snap("IDLE", DREH)),
        (32, snap("STARTED", DREH, current_program=DREH, line_no=1)),
        (60, snap("STARTED", DREH, current_program=DREH, line_no=17)),
        (62, snap("IDLE", DREH, line_no=0)),  # kurzes Programm, echtes Ende
    )
    assert [(r["program"], r["result"]) for r in db.runs(0, 1000)] == [(ORDER, "aborted"), (DREH, "finished")]


# Echte Palettentabelle der DMU 70 (pal2sp.p aus der Diagnose vom 7.10.); die Spaltenbeschreibung
# zwischen #STRUCTBEGIN und #STRUCTEND ist gekürzt (nur Deutsch, ohne DATUM/X/Y/Z)
REAL_TABLE = "\n".join([
    "BEGIN pal2sp .p ",
    "#STRUCTBEGIN",
    "   NAME = PAL/PGM",
    "     TYPE = C",
    "     WIDTH = 3",
    "     DEC = 0",
    "     DIA-GERMAN = Palette=PAL / Programm=PGM",
    "   NAME = NAME",
    "     TYPE = C",
    "     WIDTH = 30",
    "     DEC = 0",
    "     DIA-GERMAN = Palette / NC-Programm?",
    "#STRUCTEND",
    "NR      PAL/PGM NAME                           DATUM                          X          Y          Z",
    *(
        f"{nr:<8}{kind:<8}{name:<31}{'':<31}{'':<11}{'':<11}"
        for nr, (kind, name) in enumerate([
            ("PAL", "8"), ("PGM", "26-21051-02-01.h"), ("PGM", "Dreh.h"),
            ("PAL", "9"), ("PGM", "26-21051-02-01.h"), ("PGM", "Dreh.h"),
            ("PAL", "10"), ("PGM", "26-21051-02-01.h"), ("PGM", "Dreh.h"),
            ("PAL", "0"), ("PGM", "Dreh.h"), ("PGM", "P-Ende.h"),
        ])
    ),
    "[END]",
    "",
])


def test_real_pallet_table_of_the_dmu_70():
    entries = pallet_entries(REAL_TABLE)
    assert [(e.pallet, e.program, e.skipped) for e in entries] == [
        ("8", "26-21051-02-01.h", False), ("8", "Dreh.h", False),
        ("9", "26-21051-02-01.h", False), ("9", "Dreh.h", False),
        ("10", "26-21051-02-01.h", False), ("10", "Dreh.h", False),
        ("0", "Dreh.h", False), ("0", "P-Ende.h", False),
    ]
    assert program_calls("TNC:\\pal2sp.p", REAL_TABLE) == ("8", "26-21051-02-01", "DREH", "9", "10", "0", "P-ENDE")


# --- Palette von Hand setzen (Live-Karte) -------------------------------------------------------
# Die Steuerung meldet nicht, bei welcher Zeile der Tabelle eingestiegen wurde: Der Satz beim
# Palettenwechsel ist der des Makros Palett.H (Diagnose vom 21.10., pallet_line aus 1.36.1).


def pallet_sets(db):
    return [e["payload"] for e in db.events(0, 1e12, "m1") if e["type"] == "pallet_set"]


def test_pallet_set_by_hand_while_its_program_runs(db, make_collector):
    """DMU 70, 15.10.: Einstieg bei Palette 7 von pal1sp.p, die Live-Karte zeigte Palette 1."""
    c = make_collector()
    history(db, c)
    feed(
        c,
        (1000, pal("IDLE", None, 0)),
        (1010, pal("STARTED", MACRO, 6)),  # eingestiegen bei Palette 3 – die App sieht Palette 1
        (1080, pal("STARTED", ORDER, 15)),
        (1500, pal("STARTED", ORDER, 9000)),
    )
    assert view(c)[1]["position"] == 0
    info = c.set_pallet(4)  # erste Programmzeile von Palette 3
    assert (info["position"], info["set_at"]) == (4, 1500)
    assert [e["status"] for e in info["entries"]] == ["done"] * 4 + ["current", "pending", "pending"]
    assert [e["started_at"] for e in info["entries"]] == [None] * 4 + [1080, None, None]  # seit Beginn des Programms
    assert [e["until"] for e in info["entries"][:4]] == [None] * 4  # wann die Paletten davor fertig wurden: unbekannt
    assert info["remaining_s"] == 840 - 420 + 45  # Rest der Palette 3 (Prognose) + Drehen, P-Ende unbekannt
    assert pallet_sets(db) == [{
        "table": TABLE, "index": 4, "running": True, "pallet": "3", "program": ORDER, "from": 0, "from_pallet": "1",
    }]
    feed(
        c,
        (1920, pal("STARTED", DREH, 1)),
        (1970, pal("STARTED", MACRO, 6)),
        (1990, pal("STARTED", P_ENDE, 1)),
    )
    entries, info = view(c)
    assert info["position"] == 6
    assert entries[4] == ("done", 840, "history")  # von Hand gesetzt: nicht gemessen
    feed(c, (2000, pal("IDLE", None, 0)))
    assert [e[0] for e in view(c)[0]] == ["done"] * 7
    # Neuer Durchgang: wieder von vorn, ohne den Hinweis
    feed(c, (2100, pal("STARTED", MACRO, 6)), (2170, pal("STARTED", ORDER, 15)))
    assert (view(c)[1]["position"], view(c)[1]["set_at"]) == (0, None)


def test_pallet_set_by_hand_during_the_pallet_change(db, make_collector):
    """Während des Wechsels (oder vor dem Start) läuft noch kein Programm der Palette: Sie beginnt mit
    dem nächsten Start ihres Programms – an der gesetzten Zeile, nicht an der folgenden."""
    c = make_collector()
    history(db, c)
    feed(
        c,
        (1000, pal("IDLE", None, 0)),
        (1010, pal("STARTED", MACRO, 6)),
        (1080, pal("STARTED", ORDER, 15)),
        (1920, pal("STARTED", DREH, 1)),
        (1970, pal("STARTED", MACRO, 6)),  # die App erwartet Palette 2, eingewechselt wird Palette 3
    )
    info = c.set_pallet(4)
    assert (info["position"], info["entries"][4]["status"], info["entries"][4]["started_at"]) == (4, "current", None)
    assert pallet_sets(db)[0]["running"] is False
    feed(c, (2040, pal("STARTED", ORDER, 15)))
    entries, info = view(c)
    assert info["position"] == 4 and info["entries"][4]["started_at"] == 2040
    assert entries[1] == ("done", 45, "history")  # Drehen der Palette 1: nicht gemessen
    feed(c, (2880, pal("STARTED", DREH, 1)), (2930, pal("STARTED", MACRO, 6)), (2940, pal("STARTED", P_ENDE, 1)))
    entries, info = view(c)
    assert info["position"] == 6
    assert entries[4] == ("done", 840, "measured")  # ab dem Start der Palette gemessen


def test_pallet_set_by_hand_before_the_restart(db, make_collector):
    """Palettenprogramm angehalten, Palette gesetzt, dann neu gestartet (sonst: dieselbe Zeile)."""
    c = make_collector()
    history(db, c)
    feed(c, (1000, pal("IDLE", None, 0)), (1010, pal("STARTED", MACRO, 6)), (1080, pal("STARTED", ORDER, 15)))
    feed(c, (1200, pal("STOPPED", ORDER, 500)), (1210, pal("IDLE", None, 0)))
    info = c.set_pallet(2)
    assert info["position"] == 2 and [e["status"] for e in info["entries"][:3]] == ["done", "done", "current"]
    feed(c, (1300, pal("STARTED", MACRO, 6)), (1370, pal("STARTED", ORDER, 15)))
    info = view(c)[1]
    assert info["position"] == 2 and info["entries"][2]["started_at"] == 1370


def test_pallet_set_by_hand_survives_a_restart_of_the_app(db, make_collector):
    first = make_collector()
    history(db, first)
    feed(
        first,
        (1000, pal("IDLE", None, 0)),
        (1010, pal("STARTED", MACRO, 6)),
        (1080, pal("STARTED", ORDER, 15)),
        (1500, pal("STARTED", ORDER, 9000)),
    )
    first.set_pallet(4)
    second = make_collector()  # Update mitten in Palette 3
    feed(second, (2400, pal("STARTED", ORDER, 12000)))
    info = view(second)[1]
    assert (info["position"], info["set_at"]) == (4, 1500)
    assert info["entries"][4]["started_at"] == 1080

    # Gesetzt während des Wechsels, dann Neustart der App, bevor die Palette begann
    feed(second, (2900, pal("STARTED", DREH, 1)), (2950, pal("STARTED", MACRO, 6)))
    second.set_pallet(2)
    third = make_collector()
    feed(third, (3020, pal("STARTED", ORDER, 15)))
    info = view(third)[1]
    assert info["position"] == 2 and info["entries"][2]["started_at"] == 3020


def test_pallet_set_by_hand_refused(db, make_collector):
    c = make_collector()
    with pytest.raises(ValueError, match="Kein Palettenprogramm"):
        c.set_pallet(0)
    rows = [("PAL", "1"), ("PGM", ORDER), ("PAL", "2", "*"), ("PGM", ORDER), ("PAL", "3"), ("PGM", ORDER)]
    text = table(rows)
    db.save_program_file("m1", TABLE, len(text), 1.0, None, None, 0, program_calls(TABLE, text), text)
    feed(c, (0, pal("IDLE", None, 0)))
    with pytest.raises(ValueError, match="gesperrt"):
        c.set_pallet(1)
    with pytest.raises(ValueError, match="keine Programmzeile 4"):
        c.set_pallet(3)
    assert c.set_pallet(2)["position"] == 2


def test_pallet_api(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings(machines=(MACHINE,), db_path=tmp_path / "api.db", simulate=True)
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as client:
        ctx = app.state.ctx
        assert client.post("/api/machines/m1/pallet", json={"index": 0}).status_code == 409  # kein Palettenprogramm
        text = table(FIELD_ROWS)
        ctx.db.save_program_file("m1", TABLE, len(text), 1.0, None, None, 0, program_calls(TABLE, text), text)
        col = ctx.collectors["m1"]
        feed(col, (0, pal("IDLE", None, 0)), (10, pal("STARTED", MACRO, 6)), (80, pal("STARTED", ORDER, 15)))
        res = client.post("/api/machines/m1/pallet", json={"index": 2})
        assert res.status_code == 200 and res.json()["pallet"]["position"] == 2
        assert client.get("/api/machines").json()["machines"][0]["pallet"]["position"] == 2
        assert client.post("/api/machines/m1/pallet", json={"index": "2"}).status_code == 400
        assert client.post("/api/machines/xx/pallet", json={"index": 2}).status_code == 404
