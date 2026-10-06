"""Ablaufliste des Palettenprogramms (.P) auf der Live-Seite: Reihenfolge, Stand und erwartete Zeiten."""

import asyncio
import sqlite3

from app.collector import MachineCollector
from app.db import Database
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
    return [(e["status"], e["expected_s"], e["measured"]) for e in info["entries"]], info


def test_pallet_list_follows_the_table_and_learns_times(db, make_collector):
    c = make_collector()
    history(db, c)

    feed(c, (1000, pal("IDLE", None)))  # Palettenprogramm angewählt, noch nicht gestartet
    entries, info = view(c)
    assert entries == [
        ("pending", 840, False), ("pending", 45, False),  # Drehen: gleicher Name in anderem Verzeichnis
        ("pending", 840, False), ("pending", 45, False),
        ("pending", 840, False), ("pending", 45, False),
        ("pending", None, False),  # P-Ende lief noch nie
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
    assert entries[:4] == [("done", 840, True), ("done", 120, True), ("current", 840, True), ("pending", 120, True)]
    assert (info["remaining_s"], info["remaining_unknown"]) == (840 + 120 + 840 + 120, 1)

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
    assert entries[0] == ("done", 840, True)  # Median aus 840, 760, 840
    assert entries[6] == ("current", None, False)
    assert info["remaining_unknown"] == 1

    feed(c, (3990, pal("IDLE", None, 0)))  # Palettenprogramm zu Ende
    entries, info = view(c)
    assert [e[0] for e in entries] == ["done"] * 7
    assert info["remaining_s"] is None

    # Neustart: wieder von vorn, gemessene Zeiten bleiben
    feed(c, (4100, pal("STARTED", MACRO)), (4170, pal("STARTED", ORDER, 15)))
    entries, info = view(c)
    assert entries[0] == ("current", 840, True) and info["position"] == 0


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
    assert entries[1] == ("done", 70, True)  # Drehen gemessen bis zur nächsten freien Zeile (Palette 3)
    assert info["total_s"] == 2 * (90 + 70) and info["unknown"] == 0  # gesperrte Palette zählt nicht


def test_start_of_the_collector_mid_table_does_not_measure(db, make_collector):
    """Nach einem Neustart der App ist der Beginn der laufenden Zeile nicht gesehen."""
    c = make_collector()
    text = table(FIELD_ROWS)
    db.save_program_file("m1", TABLE, len(text), 1.0, None, None, 0, program_calls(TABLE, text), text)
    feed(c, (0, pal("STARTED", ORDER, 20000)), (300, pal("STARTED", DREH, 1)))
    entries, info = view(c)
    assert entries[0] == ("done", None, False) and entries[1][0] == "current"


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
    assert db.get_meta("schema_version") == "11"
    assert db.program_file("m1", TABLE)["size"] is None  # wird neu gelesen
    assert db.program_file("m1", ORDER)["size"] == 900  # Programme bleiben
    db.close()
