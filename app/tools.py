"""Werkzeuge: Einsatzzeit je Maschine und T-Nummer, Maximallaufzeit, Zurücksetzen.

- Ein Werkzeug wird automatisch angelegt, sobald es an einer Maschine in der Spindel auftaucht
  (oder von Hand im Tab „Werkzeugauswertung“, um das Limit schon vorher einzutragen).
- Die DNC-Abfrage „Werkzeug in der Spindel“ liefert Nummer, Index, Achse, Länge und Radius, aber
  keinen Namen. ``name`` kommt deshalb aus der Werkzeugtabelle TOOL.T der Steuerung (siehe
  tool_table.py, gelesen vom Collector); eigene Bezeichnungen stehen in ``note``.
- **Einsatzzeit** ist die Zeit, in der ein Programm läuft (Zustand „Läuft“) und das Werkzeug in der
  Spindel ist. Stopps, Störungen, Einrichten und angewählte, aber stehende Programme zählen nicht.
- T100 an Maschine 1 und T100 an Maschine 2 sind verschiedene Werkzeuge (eigene Magazine).
- **Zurücksetzen** (neues Werkzeug eingespannt) setzt die Einsatzzeit auf 0; der alte Stand bleibt
  als Standzeit in der Historie.
"""

from __future__ import annotations

import re
from statistics import mean
from typing import Any

from .db import Database

TOOL_MIN, TOOL_MAX = 1, 1000  # Nummernkreis im Betrieb (Anlegen von Hand)
WARN_RATIO = 0.9  # Vorwarnung ab 90 % der Maximallaufzeit
DEFAULT_LIMIT_H = 100  # Maximallaufzeit neuer Werkzeuge (automatisch und von Hand angelegt)
DEFAULT_LIMIT_S = DEFAULT_LIMIT_H * 3600

_TOOL = re.compile(r"^T(\d+)(?:\s+(.*))?$")


def parse_tool(text: str | None) -> tuple[int, str] | None:
    """``"T100 FRAESER_D10"`` → ``(100, "FRAESER_D10")``; T0 (kein Werkzeug) → None."""
    if not text:
        return None
    match = _TOOL.match(text.strip())
    if not match or int(match[1]) <= 0:
        return None
    return int(match[1]), (match[2] or "").strip()


def status(used_s: float, limit_s: float | None) -> str:
    """"none" (kein Limit), "ok", "warn" (ab 90 %) oder "over" (Limit erreicht)."""
    if not limit_s:
        return "none"
    ratio = used_s / limit_s
    return "over" if ratio >= 1 else "warn" if ratio >= WARN_RATIO else "ok"


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        **row,
        "ratio": row["used_s"] / row["limit_s"] if row["limit_s"] else None,
        "status": status(row["used_s"], row["limit_s"]),
    }


def list_tools(db: Database, machines: list[dict[str, Any]], spindle: dict[str, tuple[int | None, bool]]) -> dict[str, Any]:
    """Alle Werkzeuge der aktiven Maschinen (in deren Reihenfolge, dann nach T-Nummer).

    ``spindle``: je Maschine (T-Nummer in der Spindel, Programm läuft) aus dem Live-Status.
    """
    names = {m["id"]: m["name"] for m in machines}
    order = {m["id"]: i for i, m in enumerate(machines)}
    rows = []
    for row in db.tools():
        if row["machine_id"] not in names:
            continue  # entfernte Maschine
        number, running = spindle.get(row["machine_id"], (None, False))
        in_spindle = number == row["number"]
        rows.append(
            {**_public(row), "machine": names[row["machine_id"]], "in_spindle": in_spindle, "running": in_spindle and running}
        )
    rows.sort(key=lambda r: (order[r["machine_id"]], r["number"]))
    return {"tools": rows, "alerts": sum(r["status"] == "over" for r in rows)}


def tool_detail(db: Database, machine_id: str, number: int) -> dict[str, Any] | None:
    row = db.tool(machine_id, number)
    if row is None:
        return None
    resets = db.tool_resets(machine_id, number)
    lives = [r["used_s"] for r in resets if r["used_s"] > 0]
    return {**_public(row), "resets": resets, "avg_life_s": mean(lives) if lives else None}


def tool_info(db: Database, machine_id: str, tool: str | None) -> dict[str, Any] | None:
    """Kurzinfo zum Werkzeug in der Spindel für die Live-Karte."""
    parsed = parse_tool(tool)
    row = db.tool(machine_id, parsed[0]) if parsed else None
    if row is None:
        return None
    public = _public(row)
    return {key: public[key] for key in ("number", "name", "used_s", "limit_s", "ratio", "status")}


def alert_count(db: Database, machine_ids: set[str]) -> int:
    return sum(
        status(row["used_s"], row["limit_s"]) == "over" for row in db.tools() if row["machine_id"] in machine_ids
    )
