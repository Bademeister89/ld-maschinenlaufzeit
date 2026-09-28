"""Werkzeugtabelle TOOL.T der Heidenhain-Steuerung: Werkzeugnummer → Name.

Die DNC-Abfrage „Werkzeug in der Spindel“ liefert nur die Nummer; den Namen hat nur die
Werkzeugtabelle. Sie ist eine Textdatei mit festen Spaltenbreiten, die Kopfzeile gibt die
Spaltenanfänge vor (iTNC 530 / TNC 640)::

    BEGIN TOOL     .T     MM
    T    NAME             L           R           R2   …   DOC
    0    NULLWERKZEUG     +0          +0          +0
    1    NC-ANBOHRER      +80.5       +5          +0
    [END]
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

TOOL_TABLE = "TNC:\\TOOL.T"
TOOL_TABLE_MAX_BYTES = 5_000_000

_HEADER = re.compile(r"^\s*T\s+NAME\b")
_NUMBER = re.compile(r"(\d+)(?:\.(\d+))?")


@dataclass(frozen=True)
class ToolTableFile:
    size: int | None = None
    mtime: float | None = None
    names: dict[int, str] = field(default_factory=dict)
    error: str | None = None


def parse_tool_table(text: str) -> dict[int, str]:
    """Namen aller Werkzeuge mit Nummer > 0; Werkzeuge ohne Namen fehlen im Ergebnis.

    Indizierte Werkzeuge (T5.1, T5.2 …) teilen sich die Nummer mit dem Hauptwerkzeug; es zählt
    dessen Name, weil die Einsatzzeit je Nummer erfasst wird.
    """
    lines = text.splitlines()
    head = next((i for i, line in enumerate(lines) if _HEADER.match(line)), None)
    if head is None:
        return {}
    columns = [(m.group(), m.start()) for m in re.finditer(r"\S+", lines[head])]
    names = [i for i, (title, _) in enumerate(columns) if title == "NAME"]
    start = columns[names[0]][1]
    end = columns[names[0] + 1][1] if names[0] + 1 < len(columns) else None
    result: dict[int, str] = {}
    for line in lines[head + 1 :]:
        if line.strip().upper().startswith("[END]"):
            break
        parts = line.split(maxsplit=1)
        match = _NUMBER.fullmatch(parts[0]) if parts else None
        if not match or int(match[1]) <= 0:
            continue
        number, index = int(match[1]), int(match[2] or 0)
        name = line[start:end].strip()
        if name and (index == 0 or number not in result):
            result[number] = name
    return result
