"""NC-Programme auswerten: Gesamtzahl der Sätze eines Heidenhain-Programms.

- Klartext (.H): Sätze sind durchnummeriert, der letzte lautet ``<Nr> END PGM <Name> MM``.
- DIN/ISO (.I): gezählt werden die Programmzeilen (ohne Leerzeilen), beginnend bei 0.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_END_PGM = re.compile(r"^\s*(\d+)\s+END\s+PGM\b", re.IGNORECASE | re.MULTILINE)
_BLOCK_NO = re.compile(r"^\s*(\d+)\s", re.MULTILINE)


@dataclass(frozen=True)
class ProgramFile:
    path: str
    size: int | None = None
    mtime: float | None = None
    blocks: int | None = None  # Nummer des letzten Satzes (= Satzanzahl der Anzeige)
    error: str | None = None


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
