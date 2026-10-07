"""Aufträge aus Programmnamen: ``JJ-AUFTRAG-AUFSPANNUNG-PROGRAMM``, z. B. ``26-21055-01-01``.

- ``26``    Jahr (2026)
- ``21055`` Auftragsnummer, 4- oder 5-stellig, optional mit Version: ``21055V1``, ``21055V2``
- ``01``    Aufspannung (1 = Spannung 1, 2 = Spannung 2 …); ``08`` und ``09`` sind Vorrichtungsbau
- ``01``    Programmnummer, fortlaufend

Ein Auftrag ist eindeutig über Jahr und Nummer (Schlüssel ``26-21055``). Jede Version ist ein eigener
Auftrag (``26-21055V1``, ``26-21055V2``); ein kleines ``v`` zählt wie ``V``. Er wird automatisch
angelegt, sobald ein passendes Programm an einer Maschine auftaucht. Gezählt wird die Zeit
der Programmdurchläufe (Laufzeit sowie Stopps/Fehler innerhalb der Läufe) – nicht die Zeit,
in der ein Programm nur angewählt ist, sonst würde ein übers Wochenende angewähltes Programm
dem Auftrag Tage gutschreiben.

Läuft ``ORDER_IDLE_DAYS`` Tage lang kein Programm des Auftrags, wird er automatisch abgeschlossen
(``close_idle``). Läuft er wieder an, öffnet er sich wieder.

Felgen haben ein eigenes Schema ``BBDDBBZZ-SS[ Zusatz]``, z. B. ``10101018-01 tasche``:

- ``10``  Bauart: 10 = einteilig, 11 = dreiteilig
- ``10``  Design: 10 = 999, 20 = Z06 … (Namen pflegbar in der Konfiguration)
- ``10``  Breite in Zoll: unter 20 ganze Zoll (10 = 10″), sonst Zehntel (85 = 8,5″)
- ``18``  Durchmesser in Zoll (bis 30″)
- ``01``  Spannung
- Zusatz (``tasche``, ``einarm``, ``normal`` …): weiteres Programm derselben Spannung

Jede Felge (die achtstellige Nummer) ist ein Eintrag wie ein Auftrag (Art ``rim``, Schlüssel
``10101018``), ohne Jahr. Die Programme einer Spannung laufen nacheinander; die Ø-Zeit je Felge ist die
Summe aller.
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, time, timedelta
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from .db import Database
from .state import MachineState

_CODE = re.compile(
    r"^(?P<year>\d{2})[-_](?P<order>\d{4,5}(?:[Vv]\d{1,2})?)[-_](?P<setup>\d{1,2})[-_](?P<program>\d{1,3})(?:$|\D)"
)
_RIM = re.compile(
    r"^(?P<number>(?P<kind>1\d)(?P<design>\d{2})(?P<width>\d{2})(?P<diameter>\d{2}))[-_](?P<setup>\d{2})(?P<variant>\D.*)?$"
)
RIM_KINDS = {10: "einteilig", 11: "dreiteilig"}
# Designs beim ersten Start (danach in der Konfiguration pflegbar)
RIM_DESIGNS = {10: "999", 20: "Z06", 30: "EK 1", 40: "TrippleX", 50: "Hooligan", 60: "Stelth", 70: "458", 80: "Twister", 90: "Sonder"}
RUN_TIME_STATES = (MachineState.RUNNING.value, MachineState.STOPPED.value, MachineState.ERROR.value)
# Spannung 08 und 09 sind Vorrichtungsprogramme (Bau einer Vorrichtung): einmaliger Aufwand des
# Auftrags, keine Bearbeitung je Teil – zählt zur Laufzeit, aber nicht zur Ø-Zeit je Teil
FIXTURE_SETUPS = frozenset({8, 9})
ORDER_IDLE_DAYS = 7  # so lange ohne Programmlauf, dann wird ein Auftrag automatisch abgeschlossen

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProgramCode:
    key: str  # "26-21055" bzw. "26-21055V1"; Felge: "10101018"
    year: int  # 2026; Felge: 0
    order: str  # "21055" bzw. "21055V1"; Felge: "10101018"
    setup: int  # 1
    program: int  # 1; Felge: 0 (die Programme einer Spannung unterscheidet der Zusatz)
    name: str  # "26-21055-01-01" bzw. "10101018-01 tasche"
    kind: str = "order"  # "order" oder "rim"
    variant: str = ""  # Felge: Zusatz hinter der Spannung ("tasche", "einarm" …)

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
    if match:
        yy, order = match["year"], match["order"].upper()  # 21055v1 und 21055V1 sind derselbe Auftrag
        return ProgramCode(
            key=f"{yy}-{order}",
            year=2000 + int(yy),
            order=order,
            setup=int(match["setup"]),
            program=int(match["program"]),
            name=name,
        )
    match = _RIM.match(name)
    if match:
        number = match["number"]
        return ProgramCode(
            key=number, year=0, order=number, setup=int(match["setup"]), program=0, name=name, kind="rim",
            variant=(match["variant"] or "").strip(" -_"),
        )
    return None


RIM_TENTHS_FROM = 20  # Breitenangabe darunter in ganzen Zoll (10 = 10″), ab hier in Zehntel (85 = 8,5″)


def rim_width(code: int) -> float:
    """Breite in Zoll aus zwei Ziffern: 10 → 10, 85 → 8,5."""
    return float(code) if code < RIM_TENTHS_FROM else code / 10


def _inch(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def rim_info(key: str, designs: dict[int, str]) -> dict[str, Any] | None:
    """Bauart, Design und Größe einer Felge aus ihrer Nummer, mit Klartext für die Anzeige
    (``label`` z. B. "999 · einteilig · 10 × 18″")."""
    if not re.fullmatch(r"1\d{7}", key):
        return None
    kind, design, width, diameter = (int(key[i : i + 2]) for i in range(0, 8, 2))
    design_name = designs.get(design) or f"Design {design:02d}"
    kind_name = RIM_KINDS.get(kind, f"Bauart {kind}")
    size = f"{_inch(rim_width(width))} × {diameter}″"
    return {
        "kind": kind,
        "kind_name": kind_name,
        "design": design,
        "design_name": design_name,
        "width": rim_width(width),
        "diameter": diameter,
        "size": size,
        "label": f"{design_name} · {kind_name} · {size}",
    }


def backfill(db: Database) -> int:
    """Auftragsnummern für vorhandene Daten nachtragen (z. B. nach einem Update). Idempotent."""
    assigned = 0
    for program in db.programs_without_order():
        code = parse_program(program)
        if code is None:
            continue
        first_seen = db.assign_order(program, code.key)
        db.ensure_order(code.key, code.year, code.order, first_seen, kind=code.kind)
        assigned += 1
    return assigned


def close_idle(db: Database, now: float) -> list[str]:
    """Aufträge abschließen, in denen seit ORDER_IDLE_DAYS kein Programm lief (und die auch nicht in
    dieser Zeit von Hand wieder geöffnet wurden). Ein laufender oder gestoppter Lauf hält ihn offen."""
    keys = db.idle_orders(now - ORDER_IDLE_DAYS * 86_400)
    if keys:
        db.close_orders_auto(keys, now)
        log.info("Aufträge automatisch abgeschlossen (%d Tage ohne Programmlauf): %s", ORDER_IDLE_DAYS, ", ".join(keys))
    return keys


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
    designs = db.rim_design_names()
    result = []
    for row in rows.values():
        row["rim"] = rim_info(row["key"], designs) if row["kind"] == "rim" else None
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
            # Vorrichtungsbau (Spannung 08/09) gibt es nur bei Aufträgen, nicht bei Felgen
            {"setup": row["setup"], "fixture": order["kind"] == "order" and row["setup"] in FIXTURE_SETUPS,
             **_empty_totals(), "runs": 0,
             "finished": 0, "part_run_s": 0.0, "part_complete": True, "programs": []},
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
    # Ø Bearbeitungszeit je Teil: nur Aufspannungen des Teils, nicht der Vorrichtungsbau
    parts = [s for s in setups.values() if not s["fixture"]]
    part_complete = bool(parts) and all(s["part_complete"] for s in parts)

    intervals = db.order_intervals(key, RUN_TIME_STATES)
    first = min((iv["start"] for iv in intervals), default=None)
    last = max((iv["end"] for iv in intervals), default=None)

    order = {**order, "rim": rim_info(key, db.rim_design_names()) if order["kind"] == "rim" else None}
    return {
        "order": order,
        "totals": {
            **totals,
            "runs": len(runs),
            "finished": sum(1 for r in runs if r["result"] == "finished"),
            "part_run_s": sum(s["part_run_s"] for s in parts) if parts else None,
            "part_complete": part_complete,
            "fixture_s": sum(s["running_s"] for s in setups.values() if s["fixture"]),
            "first_activity": first,
            "last_activity": last,
            "machines": sorted({iv["machine_id"] for iv in intervals} | {r["machine_id"] for r in runs}),
        },
        "setups": sorted(setups.values(), key=lambda s: (s["fixture"], s["setup"])),  # Vorrichtung zuletzt
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
