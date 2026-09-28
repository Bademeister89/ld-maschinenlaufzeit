"""API für den Tab „Werkzeugauswertung“: Liste, Anlegen, Limit/Notiz, Zurücksetzen, CSV."""

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
STATUS_LABELS = {"none": "kein Limit", "ok": "ok", "warn": "Vorwarnung", "over": "über Limit"}


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _machines(ctx: AppContext) -> list[dict[str, Any]]:
    return [{"id": c.machine.id, "name": c.machine.name} for c in ctx.manager.ordered_collectors()]


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


def _limit_s(payload: dict[str, Any], current: float | None = None) -> float | None:
    """Maximallaufzeit in Stunden (leer = kein Limit) → Sekunden."""
    if "limit_h" not in payload:
        return current
    value = payload["limit_h"]
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        hours = float(str(value).replace(",", "."))
    except ValueError:
        raise HTTPException(400, "Die Maximallaufzeit muss eine Zahl in Stunden sein, z. B. 100 oder 2,5.") from None
    if not math.isfinite(hours) or hours <= 0 or hours > LIMIT_MAX_H:
        raise HTTPException(400, "Die Maximallaufzeit muss größer als 0 und höchstens 100.000 Stunden sein.")
    return hours * 3600


def _note(payload: dict[str, Any], current: str = "") -> str:
    note = str(payload.get("note", current) or "").strip()
    if len(note) > NOTE_MAX:
        raise HTTPException(400, f"Die Notiz darf höchstens {NOTE_MAX} Zeichen lang sein.")
    return note


@router.get("")
def list_tools(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    machines = _machines(ctx)
    return {
        "now": time.time(),
        "machines": machines,
        "range": [tools.TOOL_MIN, tools.TOOL_MAX],
        "warn_ratio": tools.WARN_RATIO,
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
    if not ctx.db.insert_tool(machine_id, number, _note(payload), _limit_s(payload), time.time()):
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

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        ["Maschine", "Werkzeug", "Name (Steuerung)", "Notiz", "Einsatzzeit (h)", "Maximallaufzeit (h)",
         "Auslastung (%)", "Status", "Zurückgesetzt am", "Zuletzt im Einsatz"]
    )
    for row in rows:
        writer.writerow(
            [
                row["machine"],
                f"T{row['number']}",
                row["name"],
                row["note"],
                hours(row["used_s"]),
                hours(row["limit_s"]),
                f"{row['ratio'] * 100:.0f}" if row["ratio"] is not None else "",
                STATUS_LABELS[row["status"]],
                local(row["reset_at"]),
                local(row["last_used_at"]),
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
    ctx.db.update_tool(machine_id, number, _note(payload, row["note"]), _limit_s(payload, row["limit_s"]))
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
