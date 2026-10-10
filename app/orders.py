"""Aufträge aus Programmnamen: ``JJ-AUFTRAG-AUFSPANNUNG-PROGRAMM``, z. B. ``26-21055-01-01``.

- ``26``    Jahr (2026) – zählt für den Auftrag nicht
- ``21055`` Auftragsnummer, 4- oder 5-stellig (die ersten Ziffern: Teilegruppe, z. B. 21 Motor),
            optional mit Version: ``21055V1``, ``21055V2``
- ``01``    Aufspannung (1 = Spannung 1, 2 = Spannung 2 …); ``08`` und ``09`` sind Vorrichtungsbau
- ``01``    Programmnummer, fortlaufend

Ein Auftrag ist eindeutig über seine Nummer (Schlüssel ``21055``), gleich mit welchem Jahr das Programm
beginnt: ``21-21055-01-01`` und ``26-21055-01-01`` gehören zum selben Auftrag. Versionen (``26-21055V1``,
``26-21055V2``; ein kleines ``v`` zählt wie ``V``) sind andere Ausführungen des Teils und gehören zum
selben Auftrag: Seine Laufzeit ist die Summe aller Versionen, die Ø-Zeit je Teil gilt je Version. Ein
Auftrag wird automatisch angelegt, sobald ein passendes Programm an einer Maschine auftaucht. Gezählt wird die Zeit
der Programmdurchläufe (Laufzeit sowie Stopps/Fehler innerhalb der Läufe) – nicht die Zeit,
in der ein Programm nur angewählt ist, sonst würde ein übers Wochenende angewähltes Programm
dem Auftrag Tage gutschreiben.

Läuft ``close_days`` Tage lang (Konfiguration → Artikel, Standard 7) kein Programm des Auftrags, wird
er automatisch abgeschlossen (``close_idle``). Läuft er wieder an, öffnet er sich wieder.

Fertige Teile: die fertigen Läufe des letzten Programms der letzten Aufspannung (ohne Vorrichtung) –
erst dort ist ein Teil fertig. Bei Versionen je Version, der Auftrag ist die Summe.

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
from .nc_program import call_name
from .state import MachineState

_CODE = re.compile(
    r"^(?P<year>\d{2})[-_](?P<order>\d{4,5})(?P<version>[Vv]\d{1,2})?[-_](?P<setup>\d{1,2})[-_](?P<program>\d{1,3})(?:$|\D)"
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
ORDER_IDLE_DAYS = 7  # Standard: so lange ohne Programmlauf, dann wird ein Auftrag automatisch abgeschlossen
CLOSE_DAYS_MAX = 365
CLOSE_DAYS_KEY = "order_close_days"  # in der Tabelle meta; 0 = nie automatisch

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProgramCode:
    key: str  # "21055" (jedes Jahr, auch die Versionen 21055V1 …); Felge: "10101018"
    year: int  # Jahr aus dem Programmnamen, 2026 (zählt für den Auftrag nicht); Felge: 0
    order: str  # "21055"; Felge: "10101018"
    setup: int  # 1
    program: int  # 1; Felge: 0 (die Programme einer Spannung unterscheidet der Zusatz)
    name: str  # "26-21055-01-01" bzw. "10101018-01 tasche"
    kind: str = "order"  # "order" oder "rim"
    variant: str = ""  # Felge: Zusatz hinter der Spannung ("tasche", "einarm" …)
    version: str = ""  # Auftrag: Version "V1", "V2" … ("" = Grundversion)

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
        yy, order = match["year"], match["order"]
        return ProgramCode(
            key=order,
            year=2000 + int(yy),
            order=order,
            setup=int(match["setup"]),
            program=int(match["program"]),
            name=name,
            version=(match["version"] or "").upper(),  # v1 und V1 sind dieselbe Version
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


def close_days(db: Database) -> int:
    """Nach wie vielen Tagen ohne Programmlauf ein Auftrag abgeschlossen wird (0 = nie)."""
    try:
        return int(db.get_meta(CLOSE_DAYS_KEY) or ORDER_IDLE_DAYS)
    except ValueError:
        return ORDER_IDLE_DAYS


def set_close_days(db: Database, days: int) -> None:
    if not 0 <= days <= CLOSE_DAYS_MAX:
        raise ValueError(f"Bitte 0 bis {CLOSE_DAYS_MAX} Tage eintragen (0 = nie automatisch abschließen).")
    db.set_meta(CLOSE_DAYS_KEY, str(days))


def close_idle(db: Database, now: float) -> list[str]:
    """Aufträge abschließen, in denen seit ``close_days`` Tagen kein Programm lief (und die auch nicht
    in dieser Zeit von Hand wieder geöffnet wurden). Ein laufender oder gestoppter Lauf hält ihn offen."""
    days = close_days(db)
    if days <= 0:
        return []
    keys = db.idle_orders(now - days * 86_400)
    if keys:
        db.close_orders_auto(keys, now)
        log.info("Aufträge automatisch abgeschlossen (%d Tage ohne Programmlauf): %s", days, ", ".join(keys))
    return keys


def pre_stage(setups: dict[str, set[int]]) -> bool:
    """Ist die Grundversion nur die gemeinsame Vorstufe der Versionen? ``setups``: Aufspannungen je
    Version (ohne Vorrichtung). Ja, wenn es Versionen gibt und jede Aufspannung der Grundversion vor der
    ersten eigenen Aufspannung jeder Version liegt – z. B. 21054: Spannung 1 gemeinsam, Spannung 2 je
    Version (V1, V2, V3). Dann ist kein Teil der Grundversion fertig; jede Version übernimmt ihre
    Spannung 1."""
    base = setups.get("")
    others = [s for version, s in setups.items() if version and s]
    return bool(base) and bool(others) and max(base) < min(min(s) for s in others)


def finished_parts(programs: Any, kind: str) -> dict[str, int]:
    """Fertige Teile je Version aus ``(Programm, fertige Läufe)``: die fertigen Läufe des letzten
    Programms der letzten Aufspannung – erst dort ist ein Teil fertig. Vorrichtungsbau (08/09) zählt
    nicht. Reihenfolge wie im Auftragsdetail (Aufspannung, Programmnummer, Name). Ist die Grundversion
    nur Vorstufe der Versionen (``pre_stage``), hat sie keine fertigen Teile."""
    counts: dict[str, int] = defaultdict(int)
    last: dict[str, tuple[tuple[int, int, str], str]] = {}
    setups: dict[str, set[int]] = defaultdict(set)
    for path, finished in programs:
        code = parse_program(path)
        if code is None or (kind == "order" and code.setup in FIXTURE_SETUPS):
            continue
        name = call_name(path)
        counts[name] += finished or 0
        setups[code.version].add(code.setup)
        rank = (code.setup, code.program, code.name)
        if code.version not in last or rank > last[code.version][0]:
            last[code.version] = (rank, name)
    result = {version: counts[name] for version, (_, name) in last.items()}
    if pre_stage(setups):
        result[""] = 0
    return result


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
            "versions": set(),
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
    # Fertige Teile: auch Programme mit Planzeit, die noch nie liefen (dann ist noch kein Teil fertig)
    per_order: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for r in db.order_program_finished():
        per_order[r["order_key"]].append((r["program"], r["finished"]))
    for plan in db.plans():
        per_order[plan["order_key"]].append((plan["program"], 0))
    for key, row in rows.items():
        row["parts"] = sum(finished_parts(per_order.get(key, ()), row["kind"]).values())
    for r in db.order_programs():
        row = rows.get(r["order_key"])
        code = parse_program(r["program"])
        if row is None or code is None:
            continue
        row["setups"].add(code.setup)
        row["programs"].add(code.name)
        row["versions"].add(code.version)
        row["machines"].add(r["machine_id"])
    designs = db.rim_design_names()
    result = []
    for row in rows.values():
        row["rim"] = rim_info(row["key"], designs) if row["kind"] == "rim" else None
        row["setups"] = sorted(row["setups"])
        row["versions"] = [v for v in sorted(row["versions"], key=version_order) if v]  # nur V1, V2 …
        row["programs"] = len(row["programs"])
        row["machines"] = sorted(row["machines"])
        result.append(row)
    # zuletzt aktive Aufträge zuerst, neue ohne Laufzeit nach Anlagedatum
    return sorted(result, key=lambda r: -(r["last_activity"] or r["created_at"]))


def setup_of(path: str) -> tuple[str, int]:
    """(Version, Aufspannung) eines Programms, so wie das Auftragsdetail gruppiert."""
    code = parse_program(path)
    return (code.version, code.setup) if code else ("", 0)


def order_detail(db: Database, key: str, tz: ZoneInfo) -> dict[str, Any] | None:
    order = db.order(key)
    if order is None:
        return None

    runs = db.order_runs(key)
    # Ein Programm ist sein Name: Kopien in anderen Ordnern (z. B. im Ordner eines anderen Auftrags)
    # zählen zur selben Zeile; ``paths`` nennt alle Speicherorte
    programs: dict[str, dict[str, Any]] = {}

    def program_row(path: str, located: bool = True) -> dict[str, Any]:
        """Zeile je Programmname; ``located=False`` für Planzeiten (Name ohne Speicherort)."""
        code = parse_program(path)
        row = programs.setdefault(
            call_name(path),
            {
                "program": path,
                "call_name": call_name(path),  # Schlüssel der Zeile (zum Löschen)
                "paths": set(),
                "name": code.name if code else path,
                "setup": code.setup if code else 0,
                "number": code.program if code else 0,
                "version": code.version if code else "",
                **_empty_totals(),
                "runs": 0,
                "finished": 0,
                "avg_run_s": None,
                "median_run_s": None,
                "last_run": None,
                "machines": set(),
                "plan_s": None,  # CAM-Planzeit (Tebis-Doku oder von Hand)
                "plan_source": None,
                "open": False,  # ein Lauf ist noch nicht beendet
            },
        )
        if located:
            row["paths"].add(path)
        return row

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
        row["open"] = row["open"] or run["ended_at"] is None
        if run["result"] == "finished":
            row["finished"] += 1
            if run["start_observed"] and run["run_s"] > 0:
                cycles[call_name(run["program"])].append(run["run_s"])
    for name, values in cycles.items():
        programs[name]["avg_run_s"] = sum(values) / len(values)
        programs[name]["median_run_s"] = median(values)
    # Planzeiten – auch für Programme, die noch nie gelaufen sind (eigene Zeile)
    for plan in db.plans_of_order(key):
        row = program_row(plan["program"], located=False)
        row["plan_s"], row["plan_source"] = plan["planned_s"], plan["source"]

    # Aufspannungen je Version (andere Ausführung des Teils): (Version, Aufspannung) → Aufspannung
    setups: dict[tuple[str, int], dict[str, Any]] = {}
    for row in sorted(programs.values(), key=lambda r: (r["setup"], r["number"], r["name"])):
        row["machines"] = sorted(row["machines"])
        row["paths"] = sorted(row["paths"])
        setup = setups.setdefault(
            (row["version"], row["setup"]),
            # Vorrichtungsbau (Spannung 08/09) gibt es nur bei Aufträgen, nicht bei Felgen
            {"setup": row["setup"], "version": row["version"],
             "fixture": order["kind"] == "order" and row["setup"] in FIXTURE_SETUPS,
             **_empty_totals(), "runs": 0,
             "finished": 0, "part_run_s": 0.0, "part_complete": True, "plan_s": 0.0, "plan_complete": True,
             "open": False, "programs": []},
        )
        setup["programs"].append(row)
        setup["open"] = setup["open"] or row["open"]
        setup["running_s"] += row["running_s"]
        setup["stopped_s"] += row["stopped_s"]
        setup["runs"] += row["runs"]
        setup["finished"] += row["finished"]
        # Ø Bearbeitungszeit je Teil in dieser Aufspannung: Summe der Ø-Laufzeiten ihrer Programme
        if row["avg_run_s"] is None:
            setup["part_complete"] = False
        else:
            setup["part_run_s"] += row["avg_run_s"]
        # Planzeit je Teil: Summe der CAM-Planzeiten, nur wenn jedes Programm eine hat
        if row["plan_s"] is None:
            setup["plan_complete"] = False
        else:
            setup["plan_s"] += row["plan_s"]

    totals = _empty_totals()
    for setup in setups.values():
        totals["running_s"] += setup["running_s"]
        totals["stopped_s"] += setup["stopped_s"]
    versions = _versions(setups.values())
    _inherit(versions)
    parts = finished_parts(((row["program"], row["finished"]) for row in programs.values()), order["kind"])
    for block in versions:
        block["parts"] = parts.get(block["version"], 0)
    # Ø Bearbeitungszeit je Teil: bei einer einzigen Ausführung wie bisher, bei mehreren je Version
    single = versions[0] if len(versions) == 1 else None

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
            "parts": sum(parts.values()),
            "part_run_s": single["part_run_s"] if single else None,
            "part_complete": single["part_complete"] if single else False,
            "plan_part_s": single["plan_part_s"] if single else None,
            "fixture_s": sum(s["running_s"] for s in setups.values() if s["fixture"]),
            "first_activity": first,
            "last_activity": last,
            "machines": sorted({iv["machine_id"] for iv in intervals} | {r["machine_id"] for r in runs}),
        },
        "versions": versions,
        "setups": [s for v in versions for s in v["setups"]],
        "days": _days(intervals, tz),
        "runs": runs,
    }


def version_order(version: str) -> tuple[int, int]:
    """Grundversion zuerst, dann V1, V2 … V10 der Zahl nach."""
    return (1, int(version[1:])) if version else (0, 0)


def _inherit(versions: list[dict[str, Any]]) -> None:
    """Versionen übernehmen die Aufspannungen der Grundversion, die vor ihrer ersten eigenen liegen
    (z. B. Spannung 1 gemeinsam, Spannung 2 je Version): Ø-Zeit und Planzeit je Teil zählen sie mit.
    ``inherited``: übernommene Aufspannungen; ``pre_stage``: Grundversion ist nur Vorstufe."""
    base = next((v for v in versions if not v["version"]), None)
    own = {v["version"]: {s["setup"] for s in v["setups"] if not s["fixture"]} for v in versions}
    is_pre = pre_stage(own)
    for v in versions:
        v["pre_stage"] = not v["version"] and is_pre
        v["inherited"] = []
        if not v["version"] or base is None or not own[v["version"]]:
            continue
        first = min(own[v["version"]])
        inherited = [s for s in base["setups"] if not s["fixture"] and s["setup"] < first]
        if not inherited:
            continue
        v["inherited"] = [s["setup"] for s in inherited]
        v["part_complete"] = v["part_complete"] and all(s["part_complete"] for s in inherited)
        v["part_run_s"] = (v["part_run_s"] or 0.0) + sum(s["part_run_s"] for s in inherited)
        if v["plan_part_s"] is not None:
            complete = all(s["plan_complete"] for s in inherited)
            v["plan_part_s"] = v["plan_part_s"] + sum(s["plan_s"] for s in inherited) if complete else None


def _versions(setups: Any) -> list[dict[str, Any]]:
    """Je Version (andere Ausführung des Teils) ihre Aufspannungen mit Summen und Ø-Zeit je Teil.
    Die Ø-Zeit je Teil rechnet nur Aufspannungen des Teils, nicht den Vorrichtungsbau."""
    blocks: dict[str, dict[str, Any]] = {}
    for setup in sorted(setups, key=lambda s: (s["fixture"], s["setup"])):  # Vorrichtung zuletzt
        block = blocks.setdefault(
            setup["version"], {"version": setup["version"], **_empty_totals(), "runs": 0, "finished": 0, "setups": []}
        )
        block["setups"].append(setup)
        for field in ("running_s", "stopped_s", "runs", "finished"):
            block[field] += setup[field]
    result = []
    for version in sorted(blocks, key=version_order):
        block = blocks[version]
        parts = [s for s in block["setups"] if not s["fixture"]]
        block["part_complete"] = bool(parts) and all(s["part_complete"] for s in parts)
        block["part_run_s"] = sum(s["part_run_s"] for s in parts) if parts else None
        block["plan_part_s"] = sum(s["plan_s"] for s in parts) if parts and all(s["plan_complete"] for s in parts) else None
        block["fixture_s"] = sum(s["running_s"] for s in block["setups"] if s["fixture"])
        result.append(block)
    return result


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
