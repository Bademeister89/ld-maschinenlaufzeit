"""Aufträge aus Programmnamen: ``JJ-AUFTRAG-AUFSPANNUNG-PROGRAMM``, z. B. ``26-21055-01-01``.

- ``26``    Jahr (2026)
- ``21055`` Auftragsnummer, 4- oder 5-stellig
- ``01``    Aufspannung (1 = Spannung 1, 2 = Spannung 2 …)
- ``01``    Programmnummer, fortlaufend

Ein Auftrag ist eindeutig über Jahr und Nummer (Schlüssel ``26-21055``). Er wird automatisch
angelegt, sobald ein passendes Programm an einer Maschine auftaucht. Gezählt wird die Zeit
der Programmdurchläufe (Laufzeit sowie Stopps/Fehler innerhalb der Läufe) – nicht die Zeit,
in der ein Programm nur angewählt ist, sonst würde ein übers Wochenende angewähltes Programm
dem Auftrag Tage gutschreiben.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from .db import Database
from .state import MachineState

_CODE = re.compile(r"^(?P<year>\d{2})[-_](?P<order>\d{4,5})[-_](?P<setup>\d{1,2})[-_](?P<program>\d{1,3})(?:$|\D)")
RUN_TIME_STATES = (MachineState.RUNNING.value, MachineState.STOPPED.value, MachineState.ERROR.value)


@dataclass(frozen=True)
class ProgramCode:
    key: str  # "26-21055"
    year: int  # 2026
    order: str  # "21055"
    setup: int  # 1
    program: int  # 1
    name: str  # "26-21055-01-01"

    def public(self) -> dict[str, Any]:
        return asdict(self)


def program_name(path: str) -> str:
    """``TNC:\\AUFTRAG\\26-21055-01-01.H`` → ``26-21055-01-01``"""
    name = re.split(r"[\\/:]", path)[-1]
    return name.rsplit(".", 1)[0] if "." in name else name


def parse_program(path: str | None) -> ProgramCode | None:
    if not path:
        return None
    name = program_name(path)
    match = _CODE.match(name)
    if not match:
        return None
    yy, order = match["year"], match["order"]
    return ProgramCode(
        key=f"{yy}-{order}",
        year=2000 + int(yy),
        order=order,
        setup=int(match["setup"]),
        program=int(match["program"]),
        name=name,
    )


def backfill(db: Database) -> int:
    """Auftragsnummern für vorhandene Daten nachtragen (z. B. nach einem Update). Idempotent."""
    assigned = 0
    for program in db.programs_without_order():
        code = parse_program(program)
        if code is None:
            continue
        first_seen = db.assign_order(program, code.key)
        db.ensure_order(code.key, code.year, code.order, first_seen)
        assigned += 1
    return assigned


# --- Auswertung ------------------------------------------------------------------------


def _empty_totals() -> dict[str, float]:
    return {"running_s": 0.0, "stopped_s": 0.0}


def _add(totals: dict[str, float], state: str, seconds: float) -> None:
    key = "running_s" if state == MachineState.RUNNING.value else "stopped_s"
    totals[key] += seconds


def list_orders(db: Database, status: str = "all") -> list[dict[str, Any]]:
    orders = {o["key"]: o for o in db.orders(status)}
    rows: dict[str, dict[str, Any]] = {}
    for key, order in orders.items():
        rows[key] = {
            **order,
            **_empty_totals(),
            "runs": 0,
            "finished": 0,
            "first_activity": None,
            "last_activity": None,
            "setups": set(),
            "programs": set(),
            "machines": set(),
        }
    for r in db.order_state_totals(RUN_TIME_STATES):
        row = rows.get(r["order_key"])
        if row is None:
            continue
        _add(row, r["state"], r["seconds"])
        row["first_activity"] = min(v for v in (row["first_activity"], r["first"]) if v is not None)
        row["last_activity"] = max(v for v in (row["last_activity"], r["last"]) if v is not None)
    for r in db.order_run_counts():
        if r["order_key"] in rows:
            rows[r["order_key"]]["runs"] = r["runs"]
            rows[r["order_key"]]["finished"] = r["finished"]
    for r in db.order_programs():
        row = rows.get(r["order_key"])
        code = parse_program(r["program"])
        if row is None or code is None:
            continue
        row["setups"].add(code.setup)
        row["programs"].add(code.name)
        row["machines"].add(r["machine_id"])
    result = []
    for row in rows.values():
        row["setups"] = sorted(row["setups"])
        row["programs"] = len(row["programs"])
        row["machines"] = sorted(row["machines"])
        result.append(row)
    # zuletzt aktive Aufträge zuerst, neue ohne Laufzeit nach Anlagedatum
    return sorted(result, key=lambda r: -(r["last_activity"] or r["created_at"]))


def order_detail(db: Database, key: str, tz: ZoneInfo) -> dict[str, Any] | None:
    order = db.order(key)
    if order is None:
        return None

    runs = db.order_runs(key)
    programs: dict[str, dict[str, Any]] = {}

    def program_row(path: str) -> dict[str, Any]:
        code = parse_program(path)
        return programs.setdefault(
            path,
            {
                "program": path,
                "name": code.name if code else path,
                "setup": code.setup if code else 0,
                "number": code.program if code else 0,
                **_empty_totals(),
                "runs": 0,
                "finished": 0,
                "avg_run_s": None,
                "median_run_s": None,
                "last_run": None,
                "machines": set(),
            },
        )

    for r in db.order_program_totals(key, RUN_TIME_STATES):
        row = program_row(r["program"])
        _add(row, r["state"], r["seconds"])
        row["machines"].add(r["machine_id"])

    cycles: dict[str, list[float]] = defaultdict(list)
    for run in runs:
        row = program_row(run["program"])
        row["runs"] += 1
        row["last_run"] = max(row["last_run"] or 0, run["started_at"])
        row["machines"].add(run["machine_id"])
        if run["result"] == "finished":
            row["finished"] += 1
            if run["start_observed"] and run["run_s"] > 0:
                cycles[run["program"]].append(run["run_s"])
    for path, values in cycles.items():
        programs[path]["avg_run_s"] = sum(values) / len(values)
        programs[path]["median_run_s"] = median(values)

    setups: dict[int, dict[str, Any]] = {}
    for row in sorted(programs.values(), key=lambda r: (r["setup"], r["number"], r["name"])):
        row["machines"] = sorted(row["machines"])
        setup = setups.setdefault(
            row["setup"],
            {"setup": row["setup"], **_empty_totals(), "runs": 0, "finished": 0, "part_run_s": 0.0,
             "part_complete": True, "programs": []},
        )
        setup["programs"].append(row)
        setup["running_s"] += row["running_s"]
        setup["stopped_s"] += row["stopped_s"]
        setup["runs"] += row["runs"]
        setup["finished"] += row["finished"]
        # Ø Bearbeitungszeit je Teil in dieser Aufspannung: Summe der Ø-Laufzeiten ihrer Programme
        if row["avg_run_s"] is None:
            setup["part_complete"] = False
        else:
            setup["part_run_s"] += row["avg_run_s"]

    totals = _empty_totals()
    for setup in setups.values():
        totals["running_s"] += setup["running_s"]
        totals["stopped_s"] += setup["stopped_s"]
    part_complete = bool(setups) and all(s["part_complete"] for s in setups.values())

    intervals = db.order_intervals(key, RUN_TIME_STATES)
    first = min((iv["start"] for iv in intervals), default=None)
    last = max((iv["end"] for iv in intervals), default=None)

    return {
        "order": order,
        "totals": {
            **totals,
            "runs": len(runs),
            "finished": sum(1 for r in runs if r["result"] == "finished"),
            "part_run_s": sum(s["part_run_s"] for s in setups.values()) if setups else None,
            "part_complete": part_complete,
            "first_activity": first,
            "last_activity": last,
            "machines": sorted({iv["machine_id"] for iv in intervals} | {r["machine_id"] for r in runs}),
        },
        "setups": [setups[k] for k in sorted(setups)],
        "days": _days(intervals, tz),
        "runs": runs,
    }


def _days(intervals: list[dict[str, Any]], tz: ZoneInfo) -> list[dict[str, Any]]:
    """Lauf- und Stoppzeit des Auftrags je Kalendertag (nur Tage mit Aktivität)."""
    days: dict[str, dict[str, Any]] = {}
    for iv in intervals:
        start, end = iv["start"], iv["end"]
        while end > start:
            day = datetime.fromtimestamp(start, tz).date()
            day_end = datetime.combine(day + timedelta(days=1), time(0), tz).timestamp()
            chunk_end = min(end, day_end)
            row = days.setdefault(day.isoformat(), {"date": day.isoformat(), **_empty_totals(), "machines": set()})
            _add(row, iv["state"], chunk_end - start)
            row["machines"].add(iv["machine_id"])
            start = chunk_end
    for row in days.values():
        row["machines"] = sorted(row["machines"])
    return [days[k] for k in sorted(days)]
