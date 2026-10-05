"""NC-Programme auswerten: Gesamtzahl der Sätze eines Heidenhain-Programms und die Programme,
die es aufruft.

- Klartext (.H): Sätze sind durchnummeriert, der letzte lautet ``<Nr> END PGM <Name> MM``.
- DIN/ISO (.I): gezählt werden die Programmzeilen (ohne Leerzeilen), beginnend bei 0.
- Aufrufe (Klartext): ``CALL PGM``, ``SEL PGM`` und Zyklus 12 (``CYCL DEF 12.1 PGM``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_END_PGM = re.compile(r"^\s*(\d+)\s+END\s+PGM\b", re.IGNORECASE | re.MULTILINE)
_BLOCK_NO = re.compile(r"^\s*(\d+)\s", re.MULTILINE)
_CALL = re.compile(r"\b(?:CALL|SEL|12\.1)\s+PGM\s+\"?([^\s\"]+)", re.IGNORECASE)


@dataclass(frozen=True)
class ProgramFile:
    path: str
    size: int | None = None
    mtime: float | None = None
    blocks: int | None = None  # Nummer des letzten Satzes (= Satzanzahl der Anzeige)
    error: str | None = None
    calls: tuple[str, ...] | None = None  # aufgerufene Programme (``call_name``), None = nicht gelesen


def call_name(path: str) -> str:
    """Vergleichbarer Name eines Programms: ``TNC:\\PALETTE\\Reinigung.h`` und ``CALL PGM REINIGUNG``
    → ``REINIGUNG`` (ohne Pfad und Endung, Großbuchstaben)."""
    name = re.split(r"[\\/:]", path)[-1]
    return (name.rsplit(".", 1)[0] if "." in name else name).upper()


def program_calls(text: str) -> tuple[str, ...]:
    """Programme, die ein Klartext-Programm aufruft, in der Reihenfolge des ersten Aufrufs.
    Kommentare (ab ``;``) zählen nicht."""
    names: dict[str, None] = {}
    for line in text.splitlines():
        for target in _CALL.findall(line.split(";", 1)[0]):
            names[call_name(target)] = None
    return tuple(names)


def count_blocks(path: str, text: str) -> int | None:
    extension = path.rsplit(".", 1)[-1].upper() if "." in path else ""
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
