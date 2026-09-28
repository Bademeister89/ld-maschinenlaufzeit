"""API für den Tab „Aufträge“: Liste, Detail, Bezeichnung/Status ändern, CSV-Export."""

from __future__ import annotations

import csv
import io
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import Response

from . import orders
from .state import RUN_RESULT_LABELS

if TYPE_CHECKING:
    from .main import AppContext

router = APIRouter(prefix="/api/orders")

STATUSES = ("open", "closed")


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _active(ctx: AppContext) -> dict[str, list[dict[str, Any]]]:
    """Welche Aufträge laufen gerade auf welcher Maschine (live)?"""
    active: dict[str, list[dict[str, Any]]] = {}
    for collector in ctx.manager.ordered_collectors():
        live = collector.live()
        order = live.get("order")
        if order and live.get("run") and live["state"] in ("RUNNING", "STOPPED", "ERROR"):
            active.setdefault(order["key"], []).append(
                {
                    "machine_id": live["id"],
                    "machine": live["name"],
                    "state": live["state"],
                    "program": order["name"],
                    "forecast": live.get("forecast"),
                }
            )
    return active


@router.get("")
def list_orders(request: Request, status: str = Query("all", pattern="^(all|open|closed)$")) -> dict[str, Any]:
    ctx = _ctx(request)
    active = _active(ctx)
    rows = orders.list_orders(ctx.db, status)
    for row in rows:
        row["active"] = active.get(row["key"], [])
    return {"now": time.time(), "orders": rows}


@router.get("/{key}")
def order_detail(request: Request, key: str) -> dict[str, Any]:
    ctx = _ctx(request)
    detail = orders.order_detail(ctx.db, key, ctx.tz)
    if detail is None:
        raise HTTPException(404, f"Unbekannter Auftrag: {key}")
    detail["active"] = _active(ctx).get(key, [])
    detail["now"] = time.time()
    return detail


@router.put("/{key}")
def update_order(request: Request, key: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    db = _ctx(request).db
    order = db.order(key)
    if order is None:
        raise HTTPException(404, f"Unbekannter Auftrag: {key}")
    title = str(payload.get("title", order["title"]) or "").strip()
    status = str(payload.get("status", order["status"]))
    if len(title) > 120:
        raise HTTPException(400, "Die Bezeichnung darf höchstens 120 Zeichen lang sein.")
    if status not in STATUSES:
        raise HTTPException(400, "Status muss „open“ oder „closed“ sein.")
    db.update_order(key, title, status, time.time())
    return db.order(key)


@router.get("/{key}/export.csv")
def export_order(request: Request, key: str) -> Response:
    ctx = _ctx(request)
    if ctx.db.order(key) is None:
        raise HTTPException(404, f"Unbekannter Auftrag: {key}")
    names = {m.id: m.name for m in ctx.manager.machines()}

    def local(t: float | None) -> str:
        return datetime.fromtimestamp(t, ctx.tz).strftime("%d.%m.%Y %H:%M:%S") if t is not None else ""

    def minutes(seconds: float | None) -> str:
        return f"{seconds / 60:.2f}".replace(".", ",") if seconds is not None else ""

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")
    writer.writerow(
        ["Auftrag", "Aufspannung", "Programm", "Lauf-Nr.", "Maschine", "Beginn", "Ende", "Ergebnis",
         "Laufzeit (min)", "Stoppzeit (min)", "Start beobachtet"]
    )
    for run in ctx.db.order_runs(key):
        code = orders.parse_program(run["program"])
        writer.writerow(
            [
                key,
                code.setup if code else "",
                code.name if code else run["program"],
                run["id"],
                names.get(run["machine_id"], run["machine_id"]),
                local(run["started_at"]),
                local(run["ended_at"]),
                RUN_RESULT_LABELS.get(run["result"] or "", "läuft"),
                minutes(run["run_s"]),
                minutes(run["stop_s"]),
                "ja" if run["start_observed"] else "nein",
            ]
        )
    return Response(
        content="﻿" + buf.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="auftrag_{key}.csv"'},
    )
