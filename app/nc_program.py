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
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_END_PGM = re.compile(r"^\s*(\d+)\s+END\s+PGM\b", re.IGNORECASE | re.MULTILINE)
_BLOCK_NO = re.compile(r"^\s*(\d+)\s", re.MULTILINE)
_CALL = re.compile(r"\b(?:CALL\s+PGM|SEL\s+PGM|SEL\s+CYCLE|12\.1\s+PGM)\s+(?:\"([^\"]+)\"|([^\s\"]+))", re.IGNORECASE)
_TABLE_HEADER = re.compile(r"^\s*NR\s.*\bNAME\b", re.IGNORECASE)
_PROGRAM_FILE = re.compile(r"([^\s\\/:\"]+\.[HI])(?=[\s\"]|$)", re.IGNORECASE)


@dataclass(frozen=True)
class ProgramFile:
    path: str
    size: int | None = None
    mtime: float | None = None
    blocks: int | None = None  # Nummer des letzten Satzes (= Satzanzahl der Anzeige)
    error: str | None = None
    calls: tuple[str, ...] | None = None  # aufgerufene Programme (``call_name``), None = nicht gelesen


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


def _pallet_programs(text: str) -> tuple[str, ...]:
    """Einträge der Spalte NAME (Paletten, Spannmittel, Programme) und dazu jeder Dateiname auf
    .H/.I in einer Zeile – so wird ein Programm auch erkannt, wenn die Spalte anders heißt."""
    lines = text.splitlines()
    head = next((i for i, line in enumerate(lines) if _TABLE_HEADER.match(line)), None)
    column: tuple[int, int | None] | None = None
    if head is not None:
        titles = [(m.group().upper(), m.start()) for m in re.finditer(r"\S+", lines[head])]
        index = next(i for i, (title, _) in enumerate(titles) if title == "NAME")
        column = (titles[index][1], titles[index + 1][1] if index + 1 < len(titles) else None)
    names: dict[str, None] = {}
    for line in lines[head + 1 :] if head is not None else lines:
        if line.strip().upper().startswith("[END]"):
            break
        if column is not None and (value := line[column[0] : column[1]].strip().strip('"')):
            names[call_name(value)] = None
        for found in _PROGRAM_FILE.findall(line):
            names[call_name(found)] = None
    return tuple(names)


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
