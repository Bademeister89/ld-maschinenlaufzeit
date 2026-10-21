"""NC-Programme auswerten: Gesamtzahl der Sätze eines Heidenhain-Programms und die Programme,
die es aufruft.

- Klartext (.H): Sätze sind durchnummeriert, der letzte lautet ``<Nr> END PGM <Name> MM``.
- DIN/ISO (.I): gezählt werden die Programmzeilen (ohne Leerzeilen), beginnend bei 0.
- Aufrufe (Klartext): ``CALL PGM``, ``SEL PGM``, ``SEL CYCLE`` und Zyklus 12 (``CYCL DEF 12.1 PGM``).
- Palettentabelle (.P): Tabelle mit festen Spaltenbreiten; die Programme stehen in der Spalte
  NAME, als Name oder mit vollem Pfad (der Leerzeichen enthalten kann)::

      BEGIN PAL1SP .P MM
      NR  TYPE NAME                                          DATUM  X  Y  Z …
      0   PAL  PAL1
      1   PGM  TNC:\\Programme\\21 Motor\\…\\26-21053-01-01.h
      2   PGM  DREH.H
      [END]

  Die Steuerung arbeitet die Zeilen von oben nach unten ab: Zeilen PAL (Palette) und FIX
  (Spannmittel) gelten für die Programmzeilen PGM darunter. Ein „*“ in der Spalte LOCK sperrt eine
  Zeile (bei PAL/FIX alles darunter bis zur nächsten Palette bzw. zum nächsten Spannmittel),
  ebenso der Bearbeitungsstatus EMPTY (Leerplatz) oder SKIP in der Spalte W-STATE.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_END_PGM = re.compile(r"^\s*(\d+)\s+END\s+PGM\b", re.IGNORECASE | re.MULTILINE)
_BLOCK_NO = re.compile(r"^\s*(\d+)\s", re.MULTILINE)
_CALL = re.compile(r"\b(?:CALL\s+PGM|SEL\s+PGM|SEL\s+CYCLE|12\.1\s+PGM)\s+(?:\"([^\"]+)\"|([^\s\"]+))", re.IGNORECASE)
_TABLE_HEADER = re.compile(r"^\s*NR\s.*\bNAME\b", re.IGNORECASE)
_PROGRAM_FILE = re.compile(r"([^\s\\/:\"]+\.[HI])(?=[\s\"]|$)", re.IGNORECASE)
_SKIP_STATES = frozenset({"EMPTY", "SKIP"})  # W-STATE: Leerplatz bzw. Bearbeitung überspringen


@dataclass(frozen=True)
class ProgramFile:
    path: str
    size: int | None = None
    mtime: float | None = None
    blocks: int | None = None  # Nummer des letzten Satzes (= Satzanzahl der Anzeige)
    error: str | None = None
    calls: tuple[str, ...] | None = None  # aufgerufene Programme (``call_name``), None = nicht gelesen
    content: str | None = None  # Text der Datei – nur bei Palettentabellen (für die Ablaufliste)


@dataclass(frozen=True)
class PalletEntry:
    """Programmzeile (PGM) einer Palettentabelle."""

    program: str  # wie in der Tabelle: Name oder Pfad
    pallet: str | None = None  # Name der Palette (Zeile PAL darüber)
    skipped: bool = False  # gesperrt (LOCK) oder laut W-STATE nicht zu bearbeiten


def _extension(path: str) -> str:
    return path.rsplit(".", 1)[-1].upper() if "." in path else ""


def call_name(path: str) -> str:
    """Vergleichbarer Name eines Programms: ``TNC:\\PALETTE\\Reinigung.h`` und ``CALL PGM REINIGUNG``
    → ``REINIGUNG`` (ohne Pfad und Endung, Großbuchstaben)."""
    name = re.split(r"[\\/:]", path)[-1]
    return (name.rsplit(".", 1)[0] if "." in name else name).upper()


def program_calls(path: str, text: str) -> tuple[str, ...]:
    """Programme, die ein Programm aufruft (Klartext) bzw. die eine Palettentabelle abarbeitet,
    in der Reihenfolge des ersten Auftretens."""
    if _extension(path) == "P":
        return _pallet_programs(text)
    names: dict[str, None] = {}
    for line in text.splitlines():
        for quoted, plain in _CALL.findall(line.split(";", 1)[0]):  # Kommentare ab ";" zählen nicht
            names[call_name(quoted or plain)] = None
    return tuple(names)


Span = tuple[int, int | None]


def _table(text: str) -> tuple[dict[str, Span], list[str]]:
    """Spalten der Kopfzeile ``NR … NAME …`` (Titel → Anfang/Ende) und die Zeilen danach bis
    ``[END]``. Ohne Kopfzeile: keine Spalten und alle Zeilen."""
    lines = text.splitlines()
    head = next((i for i, line in enumerate(lines) if _TABLE_HEADER.match(line)), None)
    columns: dict[str, Span] = {}
    if head is not None:
        titles = [(m.group().upper(), m.start()) for m in re.finditer(r"\S+", lines[head])]
        for i, (title, start) in enumerate(titles):
            columns.setdefault(title, (start, titles[i + 1][1] if i + 1 < len(titles) else None))
    rows = []
    for line in lines[head + 1 :] if head is not None else lines:
        if line.strip().upper().startswith("[END]"):
            break
        rows.append(line)
    return columns, rows


def _cell(line: str, span: Span | None) -> str:
    return line[span[0] : span[1]].strip().strip('"') if span else ""


def _pallet_programs(text: str) -> tuple[str, ...]:
    """Einträge der Spalte NAME (Paletten, Spannmittel, Programme) und dazu jeder Dateiname auf
    .H/.I in einer Zeile – so wird ein Programm auch erkannt, wenn die Spalte anders heißt."""
    columns, rows = _table(text)
    names: dict[str, None] = {}
    for line in rows:
        if value := _cell(line, columns.get("NAME")):
            names[call_name(value)] = None
        for found in _PROGRAM_FILE.findall(line):
            names[call_name(found)] = None
    return tuple(names)


def pallet_entries(text: str) -> tuple[PalletEntry, ...]:
    """Programmzeilen einer Palettentabelle in der Reihenfolge, in der die Steuerung sie abarbeitet –
    jede Zeile einzeln, auch wenn ein Programm mehrmals vorkommt. Leer, wenn die Tabelle keine
    Kopfzeile mit Spalte NAME hat."""
    columns, rows = _table(text)
    name = columns.get("NAME")
    if name is None:
        return ()
    # Zeilentyp PAL/FIX/PGM: Spalte "TYPE" bzw. "PAL/PGM"
    kind = next((span for title, span in columns.items() if title == "TYPE" or "PGM" in title), None)
    lock = columns.get("LOCK")
    state = next((span for title, span in columns.items() if title.startswith("W-STAT")), None)
    entries: list[PalletEntry] = []
    pallet: str | None = None
    pallet_locked = fixture_locked = False
    for line in rows:
        value = _cell(line, name)
        row_type = _cell(line, kind).upper() if kind else ("PGM" if _PROGRAM_FILE.search(value) else "")
        locked = "*" in _cell(line, lock)
        if row_type == "PAL":
            pallet, pallet_locked, fixture_locked = value or None, locked, False
        elif row_type == "FIX":
            fixture_locked = locked
        elif row_type == "PGM" and value:
            skipped = pallet_locked or fixture_locked or locked or _cell(line, state).upper() in _SKIP_STATES
            entries.append(PalletEntry(value, pallet, skipped))
    return tuple(entries)


def count_blocks(path: str, text: str) -> int | None:
    extension = _extension(path)
    if extension == "H":
        matches = _END_PGM.findall(text)
        if matches:
            return int(matches[-1])
        numbers = _BLOCK_NO.findall(text)
        return int(numbers[-1]) if numbers else None
    if extension == "I":
        lines = [line for line in text.splitlines() if line.strip()]
        return len(lines) - 1 if lines else None
    return None
