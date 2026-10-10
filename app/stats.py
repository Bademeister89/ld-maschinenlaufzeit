"""Auswertung: Zeiten je Zustand, Tag und Programm für einen Zeitraum.

Zeit ohne erfasstes Intervall (Tool lief nicht) wird als ``NO_DATA`` ausgewiesen, damit
die Summe aller Zustände immer genau dem Zeitraum entspricht.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .db import Database
from .nc_program import call_name
from .state import STORED_STATES, MachineState

NO_DATA = "NO_DATA"
STATE_KEYS = [s.value for s in STORED_STATES] + [NO_DATA]


def clip(intervals: list[dict[str, Any]], t0: float, t1: float) -> list[dict[str, Any]]:
    out = []
    for iv in intervals:
        start, end = max(iv["start"], t0), min(iv["end"], t1)
        if end > start:
            out.append({**iv, "start": start, "end": end})
    return out


def local_days(t0: float, t1: float, tz: ZoneInfo) -> list[tuple[str, float, float]]:
    """Kalendertage (lokale Zeit, sommerzeitfest) im Bereich [t0, t1) als (Datum, Beginn, Ende)."""
    days = []
    day = datetime.fromtimestamp(t0, tz).date()
    while True:
        start = datetime.combine(day, time(0), tz).timestamp()
        if start >= t1:
            break
        end = datetime.combine(day + timedelta(days=1), time(0), tz).timestamp()
        days.append((day.isoformat(), max(start, t0), min(end, t1)))
        day += timedelta(days=1)
    return days


def _state_totals(intervals: list[dict[str, Any]], period_s: float) -> dict[str, float]:
    totals = dict.fromkeys(STATE_KEYS, 0.0)
    for iv in intervals:
        totals[iv["state"]] = totals.get(iv["state"], 0.0) + iv["end"] - iv["start"]
    totals[NO_DATA] = max(0.0, period_s - sum(v for k, v in totals.items() if k != NO_DATA))
    return totals


def _kpis(totals: dict[str, float], period_s: float) -> dict[str, float | None]:
    running = totals[MachineState.RUNNING.value]
    powered_on = period_s - totals[NO_DATA] - totals[MachineState.OFFLINE.value]
    return {
        "period_s": period_s,
        "powered_on_s": powered_on,
        # Auslastung bezogen auf die Zeit, in der die Steuerung erreichbar war
        "utilization": running / powered_on if powered_on > 0 else None,
        # Auslastung bezogen auf die Kalenderzeit des Zeitraums
        "utilization_calendar": running / period_s if period_s > 0 else None,
    }


def summarize(
    db: Database, machine_ids: list[str], t0: float, t1: float, tz: ZoneInfo, now: float
) -> dict[str, Any]:
    t1 = min(t1, now)
    period_s = max(0.0, t1 - t0)
    intervals = clip(db.intervals(t0, t1), t0, t1) if period_s else []
    by_machine: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for iv in intervals:
        by_machine[iv["machine_id"]].append(iv)

    days = local_days(t0, t1, tz) if period_s else []
    machines: dict[str, Any] = {}
    for mid in machine_ids:
        ivs = by_machine.get(mid, [])
        totals = _state_totals(ivs, period_s)
        machines[mid] = {
            "totals": totals,
            **_kpis(totals, period_s),
            "days": [
                {
                    "date": date,
                    "period_s": d1 - d0,
                    "states": (day_totals := _state_totals(clip(ivs, d0, d1), d1 - d0)),
                    **_kpis(day_totals, d1 - d0),
                }
                for date, d0, d1 in days
            ],
        }

    return {
        "from": t0,
        "to": t1,
        "period_s": period_s,
        "machines": machines,
        "programs": programs(db, intervals, t0, t1) if period_s else [],
    }


def programs(db: Database, intervals: list[dict[str, Any]], t0: float, t1: float) -> list[dict[str, Any]]:
    """Kennzahlen je (Maschine, Programm): Lauf-/Stoppzeit im Zeitraum, Anzahl Läufe, Ø-Zeiten.
    Ein Programm ist sein Name: Kopien in anderen Ordnern zählen zur selben Zeile (``paths``)."""
    rows: dict[tuple[str, str], dict[str, Any]] = {}

    def row(mid: str, program: str) -> dict[str, Any]:
        r = rows.setdefault(
            (mid, call_name(program)),
            {
                "machine_id": mid,
                "program": program,
                "paths": set(),
                "running_s": 0.0,
                "stopped_s": 0.0,
                "runs": 0,
                "finished": 0,
                "avg_run_s": None,
                "avg_total_s": None,
                "last_run": None,
            },
        )
        r["paths"].add(program)
        return r

    for iv in intervals:
        if not iv["program"] or iv["run_id"] is None:
            continue
        r = row(iv["machine_id"], iv["program"])
        key = "running_s" if iv["state"] == MachineState.RUNNING.value else "stopped_s"
        r[key] += iv["end"] - iv["start"]

    cycles: dict[tuple[str, str], list[tuple[float, float]]] = defaultdict(list)
    resets = db.program_resets()
    for run in db.runs(t0, t1):
        if not run["program"] or not (t0 <= run["started_at"] < t1):
            continue
        r = row(run["machine_id"], run["program"])
        k = (run["machine_id"], call_name(run["program"]))
        r["runs"] += 1
        r["last_run"] = max(r["last_run"] or 0, run["started_at"])
        if run["result"] == "finished":
            r["finished"] += 1
            # Stückzeiten nur aus vollständig beobachteten Läufen (und nach einem Zurücksetzen der Ø-Zeit)
            reset = resets.get(call_name(run["program"]))
            if run["start_observed"] and (reset is None or run["started_at"] >= reset):
                cycles[k].append((run["run_s"], run["ended_at"] - run["started_at"]))

    for k, values in cycles.items():
        rows[k]["avg_run_s"] = sum(v[0] for v in values) / len(values)
        rows[k]["avg_total_s"] = sum(v[1] for v in values) / len(values)

    blocks = db.program_blocks()
    by_name = {(mid, call_name(path)): count for (mid, path), count in blocks.items()}
    for r in rows.values():
        r["paths"] = sorted(r["paths"])
        # Satzanzahl der Datei; eine Kopie, die noch nie gelesen wurde, hat die des gleichnamigen Programms
        r["blocks"] = blocks.get((r["machine_id"], r["program"])) or by_name.get((r["machine_id"], call_name(r["program"])))

    return sorted(rows.values(), key=lambda r: (-r["running_s"], r["machine_id"], r["program"]))
