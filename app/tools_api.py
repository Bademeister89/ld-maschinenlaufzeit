"""API für den Tab „Werkzeugauswertung“: Liste, Anlegen, Werkzeugdaten/Standzeit, Zurücksetzen, CSV."""

from __future__ import annotations

import csv
import io
import math
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, HTTPException, Request
from fastapi.responses import Response

from . import tools

if TYPE_CHECKING:
    from .main import AppContext

router = APIRouter(prefix="/api/tools")

LIMIT_MAX_H = 100_000
NOTE_MAX = 120
TEXT_MAX = 80  # Hersteller, Artikelnummer
MM_MAX = 2000  # Durchmesser / Radius
STATUS_LABELS = {"none": "kein Limit", "ok": "ok", "warn": "Vorwarnung", "over": "über Limit"}


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _machines(ctx: AppContext) -> list[dict[str, Any]]:
    return [
        {"id": c.machine.id, "name": c.machine.name, "tool_slots": c.machine.tool_slots}
        for c in ctx.manager.ordered_collectors()
    ]


def _spindle(ctx: AppContext) -> dict[str, tuple[int | None, bool]]:
    """Je Maschine: T-Nummer in der Spindel und ob gerade ein Programm läuft."""
    result = {}
    for collector in ctx.manager.ordered_collectors():
        live = collector.live()
        parsed = tools.parse_tool(live.get("tool"))
        result[live["id"]] = (parsed[0] if parsed else None, live.get("state") == "RUNNING")
    return result


def _check_machine(ctx: AppContext, machine_id: str) -> None:
    if machine_id not in ctx.collectors:
        raise HTTPException(404, f"Unbekannte Maschine: {machine_id}")


def _existing(ctx: AppContext, machine_id: str, number: int) -> dict[str, Any]:
    _check_machine(ctx, machine_id)
    row = ctx.db.tool(machine_id, number)
    if row is None:
        raise HTTPException(404, f"Werkzeug T{number} ist an dieser Maschine nicht angelegt")
    return row


def _decimal(value: Any, message: str) -> float | None:
    """Zahl mit Dezimalkomma oder -punkt; leer = None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        number = float(str(value).replace(",", "."))
    except ValueError:
        raise HTTPException(400, message) from None
    if not math.isfinite(number):
        raise HTTPException(400, message)
    return number


def _hours(payload: dict[str, Any], key: str, current_s: float | None, label: str) -> float | None:
    """Stunden (leer = keine Angabe) → Sekunden; fehlt der Schlüssel, bleibt der bisherige Wert."""
    if key not in payload:
        return current_s
    hours = _decimal(payload[key], f"{label} muss eine Zahl in Stunden sein, z. B. 100 oder 2,5.")
    if hours is not None and not 0 < hours <= LIMIT_MAX_H:
        raise HTTPException(400, f"{label} muss größer als 0 und höchstens 100.000 Stunden sein.")
    return None if hours is None else hours * 3600


def _mm(payload: dict[str, Any], key: str, current: float | None, label: str, allow_zero: bool = False) -> float | None:
    if key not in payload:
        return current
    value = _decimal(payload[key], f"{label} muss eine Zahl in mm sein, z. B. 10 oder 0,5.")
    if value is None:
        return None
    if not ((value >= 0 if allow_zero else value > 0) and value <= MM_MAX):
        lower = "mindestens 0" if allow_zero else "größer als 0"
        raise HTTPException(400, f"{label} muss {lower} und höchstens {MM_MAX} mm sein.")
    return value


def _text(payload: dict[str, Any], key: str, current: str, label: str, limit: int) -> str:
    text = str(payload.get(key, current) or "").strip()
    if len(text) > limit:
        raise HTTPException(400, f"{label} darf höchstens {limit} Zeichen lang sein.")
    return text


NEW_TOOL = {
    "note": "",
    "manufacturer": "",
    "article_no": "",
    "diameter": None,
    "radius": None,
    "limit_s": tools.DEFAULT_LIMIT_S,
    "warn_s": tools.DEFAULT_WARN_S,
}


def _fields(payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Von Hand pflegbare Werkzeugfelder aus der Anfrage; fehlende Angaben bleiben wie ``current``."""
    fields = {
        "note": _text(payload, "note", current["note"], "Die Notiz", NOTE_MAX),
        "manufacturer": _text(payload, "manufacturer", current["manufacturer"], "Der Hersteller", TEXT_MAX),
        "article_no": _text(payload, "article_no", current["article_no"], "Die Artikelnummer", TEXT_MAX),
        "diameter": _mm(payload, "diameter", current["diameter"], "Der Durchmesser"),
        "radius": _mm(payload, "radius", current["radius"], "Der Radius", allow_zero=True),
        "limit_s": _hours(payload, "limit_h", current["limit_s"], "Die Maximallaufzeit"),
        "warn_s": _hours(payload, "warn_h", current["warn_s"], "Die Vorwarnzeit"),
    }
    if fields["limit_s"] and fields["warn_s"] and fields["warn_s"] >= fields["limit_s"]:
        if "warn_h" in payload:
            raise HTTPException(400, "Die Vorwarnzeit muss kleiner als die Maximallaufzeit sein, z. B. 80 h bei 100 h.")
        fields["warn_s"] = None  # nur das Limit gesenkt: Vorwarnung wieder automatisch bei 90 %
    return fields


@router.get("")
def list_tools(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    machines = _machines(ctx)
    return {
        "now": time.time(),
        "machines": machines,
        "range": [tools.TOOL_MIN, tools.TOOL_MAX],
        "warn_ratio": tools.WARN_RATIO,
        "default_limit_h": tools.DEFAULT_LIMIT_H,
        "default_warn_h": tools.DEFAULT_WARN_H,
        "manufacturers": [m["name"] for m in ctx.db.manufacturers()],
        **tools.list_tools(ctx.db, machines, _spindle(ctx)),
    }


@router.post("", status_code=201)
def add_tool(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    ctx = _ctx(request)
    machine_id = str(payload.get("machine_id") or "")
    _check_machine(ctx, machine_id)
    try:
        number = int(str(payload.get("number", "")).strip().upper().removeprefix("T"))
    except ValueError:
        raise HTTPException(400, "Bitte eine Werkzeugnummer eintragen, z. B. 100.") from None
    if not tools.TOOL_MIN <= number <= tools.TOOL_MAX:
        raise HTTPException(400, f"Werkzeugnummern gehen von {tools.TOOL_MIN} bis {tools.TOOL_MAX}.")
    if not ctx.db.insert_tool(machine_id, number, time.time(), **_fields(payload, NEW_TOOL)):
        raise HTTPException(409, f"T{number} ist an dieser Maschine schon angelegt.")
    return tools.tool_detail(ctx.db, machine_id, number)


@router.get("/export.csv")
def export_tools(request: Request) -> Response:
    ctx = _ctx(request)
    rows = tools.list_tools(ctx.db, _machines(ctx), {})["tools"]

    def local(t: float | None) -> str:
        return datetime.fromtimestamp(t, ctx.tz).strftime("%d.%m.%Y %H:%M") if t is not None else ""

    def hours(seconds: float | None) -> str:
        return f"{seconds / 3600:.2f}".replace(".", ",") if seconds is not None else ""

    def mm(value: float | None) -> str:
        return f"{value:g}".replace(".", ",") if value is not None else ""

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        ["Maschine", "Werkzeug", "Name", "Hersteller", "Artikelnummer", "Durchmesser (mm)", "Radius (mm)",
         "Notiz", "Einsatzzeit (h)", "Maximallaufzeit (h)", "Vorwarnung ab (h)", "Auslastung (%)", "Status",
         "Zurückgesetzt am", "Zuletzt im Einsatz", "Aufrufe", "Laufzeit gesamt (h)"]
    )
    for row in rows:
        writer.writerow(
            [
                row["machine"],
                f"T{row['number']}",
                row["name"],
                row["manufacturer"],
                row["article_no"],
                mm(row["diameter"]),
                mm(row["radius"]),
                row["note"],
                hours(row["used_s"]),
                hours(row["limit_s"]),
                hours(row["warn_at_s"]),
                f"{row['ratio'] * 100:.0f}" if row["ratio"] is not None else "",
                STATUS_LABELS[row["status"]],
                local(row["reset_at"]),
                local(row["last_used_at"]),
                row["calls"],
                hours(row["total_s"]),
            ]
        )
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="werkzeuge.csv"'},
    )


@router.get("/{machine_id}/{number}")
def tool_detail(request: Request, machine_id: str, number: int) -> dict[str, Any]:
    ctx = _ctx(request)
    _existing(ctx, machine_id, number)
    return tools.tool_detail(ctx.db, machine_id, number)


@router.put("/{machine_id}/{number}")
def update_tool(request: Request, machine_id: str, number: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    ctx = _ctx(request)
    row = _existing(ctx, machine_id, number)
    ctx.db.update_tool(machine_id, number, **_fields(payload, row))
    return tools.tool_detail(ctx.db, machine_id, number)


@router.post("/{machine_id}/{number}/reset")
def reset_tool(request: Request, machine_id: str, number: int) -> dict[str, Any]:
    ctx = _ctx(request)
    _existing(ctx, machine_id, number)
    used = ctx.db.reset_tool(machine_id, number, time.time())
    ctx.db.add_event(machine_id, time.time(), "tool_reset", {"tool": number, "used_s": round(used, 1)})
    return tools.tool_detail(ctx.db, machine_id, number)


@router.delete("/{machine_id}/{number}", status_code=204)
def delete_tool(request: Request, machine_id: str, number: int) -> None:
    ctx = _ctx(request)
    _existing(ctx, machine_id, number)
    ctx.db.delete_tool(machine_id, number)
    ctx.collectors[machine_id].forget_tool(number)
