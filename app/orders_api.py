"""API für den Tab „Aufträge“: Liste, Detail, Bezeichnung/Status ändern, Bild, CSV-Export, Löschen
(Lauf, Programm, Aufspannung, ganzer Auftrag), CAM-Planzeiten (Import der Tebis-Doku als PDF, von Hand)."""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from . import cam_import, orders
from .nc_program import call_name
from .order_images import MAX_UPLOAD_BYTES, ImageError, split_upload, valid_key
from .state import RUN_RESULT_LABELS

if TYPE_CHECKING:
    from .main import AppContext

log = logging.getLogger(__name__)

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
    files = ctx.order_images.files()
    rows = [ctx.order_images.public(row, files) for row in orders.list_orders(ctx.db, status)]
    for row in rows:
        row["active"] = active.get(row["key"], [])
    return {"now": time.time(), "close_days": orders.close_days(ctx.db), "orders": rows}


@router.get("/{key}")
def order_detail(request: Request, key: str) -> dict[str, Any]:
    ctx = _ctx(request)
    detail = orders.order_detail(ctx.db, key, ctx.tz)
    if detail is None:
        raise HTTPException(404, f"Unbekannter Auftrag: {key}")
    detail["order"] = ctx.order_images.public(detail["order"])
    images, files = ctx.db.setup_images(key), ctx.order_images.files()
    for setup in detail["setups"]:  # dieselben Einträge wie in detail["versions"]
        image = images.get((setup["version"], setup["setup"]))
        setup.update(ctx.order_images.setup_urls(key, setup["version"], setup["setup"], image, files))
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
    return _ctx(request).order_images.public(db.order(key))


# --- Lauf löschen ------------------------------------------------------------------------------


@router.delete("/{key}/runs/{run_id}", status_code=204)
def delete_run(request: Request, key: str, run_id: int) -> Response:
    """Lauf aus dem Auftrag löschen, z. B. einen Fehllauf oder ein Nachprogramm.

    Wie beim Verwerfen von MDI-Läufen bleibt seine Zeit als Maschinenzeit erhalten (Auswertung je
    Maschine und Tag), zählt aber zu keinem Lauf mehr: nicht zum Auftrag, nicht zu den Ø-Stückzeiten
    und nicht zur Prognose. Was gelöscht wurde, steht als Ereignis ``run_deleted`` in der Datenbank.
    """
    ctx = _ctx(request)
    if ctx.db.order(key) is None:
        raise HTTPException(404, f"Unbekannter Auftrag: {key}")
    run = ctx.db.run(run_id)
    if run is None or run["order_key"] != key:
        raise HTTPException(404, f"Lauf {run_id} gehört nicht zu Auftrag {key}.")
    if run["ended_at"] is None:
        raise HTTPException(409, "Der Lauf läuft noch. Löschen geht erst, wenn er beendet ist.")
    with ctx.db.transaction():
        _discard_run(ctx, run, key, time.time())
    _forget_run(ctx, run)
    log.info("Auftrag %s: Lauf %d (%s) gelöscht", key, run_id, run["program"])
    return Response(status_code=204)


def _discard_run(ctx: AppContext, run: dict[str, Any], key: str, now: float, reason: str | None = None) -> None:
    """Lauf löschen und als Ereignis ``run_deleted`` festhalten (in einer Transaktion aufrufen)."""
    payload = {
        "run": run["id"],
        "order": key,
        "program": run["program"],
        "started_at": run["started_at"],
        "ended_at": run["ended_at"],
        "result": run["result"],
        "run_s": round(run["run_s"], 1),
        "stop_s": round(run["stop_s"], 1),
    }
    if reason:
        payload["reason"] = reason
    ctx.db.discard_run(run["id"])
    ctx.db.add_event(run["machine_id"], now, "run_deleted", payload)


def _forget_run(ctx: AppContext, run: dict[str, Any]) -> None:
    collector = ctx.collectors.get(run["machine_id"])
    if collector is not None:
        collector.forget_run(run["id"], run["program"])


# --- Programm, Aufspannung oder ganzen Auftrag löschen ----------------------------------------


def _delete_programs(
    ctx: AppContext, key: str, match: Callable[[str], bool], reason: str, finish: Callable[[], None] | None = None
) -> dict[str, int]:
    """Läufe und Planzeiten der Programme des Auftrags löschen, für die ``match(Programm)`` gilt.

    Wie beim einzelnen Lauf bleibt die Zeit als Maschinenzeit erhalten. Ist ein Lauf noch nicht
    beendet, wird nichts gelöscht (409). ``finish`` läuft in derselben Transaktion."""
    runs = [r for r in ctx.db.order_runs(key) if match(r["program"])]
    plans = [p for p in ctx.db.plans_of_order(key) if match(p["program"])]
    running = sorted({call_name(r["program"]) for r in runs if r["ended_at"] is None})
    if running:
        raise HTTPException(
            409, f"{', '.join(running)} läuft noch. Löschen geht erst, wenn der Lauf beendet ist."
        )
    now = time.time()
    with ctx.db.transaction():
        for run in runs:
            _discard_run(ctx, run, key, now, reason)
        for plan in plans:
            ctx.db.delete_plan(plan["name"])
        if finish is not None:
            finish()
    for run in runs:
        _forget_run(ctx, run)
    if plans:
        _forget_plans(ctx)
    return {"runs": len(runs), "plans": len(plans)}


@router.delete("/{key}/programs/{name}")
def delete_program(request: Request, key: str, name: str) -> dict[str, int]:
    """Ein Programm aus dem Auftrag löschen: alle seine Läufe (gleich in welchem Ordner) und seine
    Planzeit. ``name`` ist ``call_name`` der Programmzeile, z. B. ``26-21053-02-01``."""
    ctx = _ctx(request)
    if ctx.db.order(key) is None:
        raise HTTPException(404, f"Unbekannter Auftrag: {key}")
    name = name.strip().upper()
    if not any(call_name(p) == name for p in _order_programs(ctx, key)):
        raise HTTPException(404, f"Programm {name} gehört nicht zu Auftrag {key}.")
    result = _delete_programs(ctx, key, lambda p: call_name(p) == name, f"Programm {name} gelöscht")
    log.info("Auftrag %s: Programm %s gelöscht (%d Läufe, %d Planzeiten)", key, name, result["runs"], result["plans"])
    return result


@router.delete("/{key}/setups/{setup}")
def delete_setup(
    request: Request, key: str, setup: int, version: str = Query("", pattern=r"^([vV]\d{1,2})?$")
) -> dict[str, int]:
    """Eine Aufspannung (bei Felgen: Spannung) löschen: alle ihre Programme mit Läufen und
    Planzeiten. ``version`` (z. B. ``V2``) wählt die Aufspannung einer Version, leer = Grundversion."""
    ctx = _ctx(request)
    if ctx.db.order(key) is None:
        raise HTTPException(404, f"Unbekannter Auftrag: {key}")
    wanted = (version.upper(), setup)
    what = f"Aufspannung {setup}" + (f" {wanted[0]}" if wanted[0] else "")
    if not any(orders.setup_of(p) == wanted for p in _order_programs(ctx, key)):
        raise HTTPException(404, f"{what} gehört nicht zu Auftrag {key}.")
    result = _delete_programs(ctx, key, lambda p: orders.setup_of(p) == wanted, f"{what} gelöscht")
    ctx.order_images.delete_setup(key, *wanted)
    log.info("Auftrag %s: %s gelöscht (%d Läufe, %d Planzeiten)", key, what, result["runs"], result["plans"])
    return result


@router.delete("/{key}")
def delete_order(request: Request, key: str) -> dict[str, int]:
    """Auftrag bzw. Felge komplett löschen: Läufe, Planzeiten, Bezeichnung und Bild.

    Ist ein Programm noch angewählt oder läuft es wieder, legt der Collector den Auftrag neu an,
    ohne die gelöschten Daten."""
    ctx = _ctx(request)
    order = ctx.db.order(key)
    if order is None:
        raise HTTPException(404, f"Unbekannter Auftrag: {key}")
    setup_images = ctx.db.setup_images(key)
    result = _delete_programs(
        ctx, key, lambda p: True, f"Auftrag {key} gelöscht", finish=lambda: ctx.db.delete_order(key)
    )
    for image in (order["image"], *setup_images.values()):
        ctx.order_images.remove_files(image)
    log.info("Auftrag %s gelöscht (%d Läufe, %d Planzeiten)", key, result["runs"], result["plans"])
    return result


def _order_programs(ctx: AppContext, key: str) -> set[str]:
    """Programme des Auftrags: aus Läufen und Planzeiten."""
    return {r["program"] for r in ctx.db.order_runs(key)} | {p["program"] for p in ctx.db.plans_of_order(key)}


# --- CAM-Planzeiten (Tebis-Doku oder von Hand) ------------------------------------------------


def _forget_plans(ctx: AppContext) -> None:
    """Prognosen und Palettenlisten mit den neuen Planzeiten rechnen."""
    for collector in ctx.collectors.values():
        collector.forget_plans()


@router.post("/import-cam")
async def import_cam(request: Request, x_file_name: str | None = Header(None)) -> dict[str, Any]:
    """Tebis-Doku einer Aufspannung (PDF als Rohdaten): Aufträge bzw. Felgen der Programme anlegen,
    Planzeiten speichern, eine leere Bezeichnung aus dem CAD-Namen füllen. Kopfzeile ``X-File-Name``
    (URL-kodiert) nur für die Meldung und als Herkunft der Planzeit."""
    ctx = _ctx(request)
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > cam_import.MAX_PDF_BYTES:
            raise HTTPException(413, "Die PDF ist größer als 10 MB.")
    file = unquote(x_file_name or "").strip()[:200] or None
    try:
        doc = await asyncio.to_thread(cam_import.read_cam_pdf, bytes(data))
    except cam_import.CamImportError as exc:
        raise HTTPException(400, f"{file or 'PDF'}: {exc}") from exc
    result = cam_import.apply_cam_doc(ctx.db, doc, file, time.time())
    _forget_plans(ctx)
    for order in result["orders"]:
        log.info(
            "CAM-Doku %s: %s %s, %d Planzeit(en)", file or "?", "angelegt" if order["created"] else "Auftrag",
            order["key"], len(order["programs"]),
        )
    return {"file": file, **result}


@router.put("/{key}/plans")
def set_plan(request: Request, key: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Planzeit eines Programms von Hand: ``{"program": "26-21053-02-01", "time": "4,5" | "4:30"}``.
    Leere Zeit entfernt die Planzeit."""
    ctx = _ctx(request)
    if ctx.db.order(key) is None:
        raise HTTPException(404, f"Unbekannter Auftrag: {key}")
    program = str(payload.get("program") or "").strip()
    code = orders.parse_program(program)
    if code is None or code.key != key:
        raise HTTPException(400, f"„{program}“ ist kein Programm dieses Auftrags (z. B. 26-{key}-01-01).")
    name = call_name(program)
    text = str(payload.get("time") or "").strip()
    if not text:
        ctx.db.delete_plan(name)
        _forget_plans(ctx)
        return {"name": name, "planned_s": None}
    try:
        planned_s = cam_import.parse_plan_time(text)
    except ValueError:
        raise HTTPException(400, "Planzeit bitte in Stunden (z. B. 4,5) oder als h:mm (z. B. 4:30) eintragen.") from None
    ctx.db.set_plan(name, program, key, planned_s, "manual", time.time())
    _forget_plans(ctx)
    return {"name": name, "planned_s": planned_s}


# --- Bild (fertiges Bauteil) ----------------------------------------------------------------


def _check_key(ctx: AppContext, key: str) -> None:
    # Der Schlüssel landet im Dateinamen: nur das Schema der Programmnamen zulassen
    if not valid_key(key):
        raise HTTPException(400, f"Ungültiger Auftragsschlüssel: {key}")
    if ctx.db.order(key) is None:
        raise HTTPException(404, f"Unbekannter Auftrag: {key}")


@router.put("/{key}/image")
async def upload_image(request: Request, key: str, x_image_length: str | None = Header(None)) -> dict[str, Any]:
    """Großes Bild und Vorschaubild als Rohdaten in *einem* Rumpf (JPEG, im Browser verkleinert).

    Kopfzeile ``X-Image-Length``: Länge des großen Bildes; der Rest des Rumpfs ist das
    Vorschaubild. Eine Anfrage für beide, damit nie ein großes Bild ohne Vorschau entsteht.
    """
    ctx = _ctx(request)
    _check_key(ctx, key)
    image, thumb = await _read_upload(request, x_image_length)
    try:
        ctx.order_images.save(key, image, thumb)
    except ImageError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except LookupError as exc:  # Auftrag inzwischen nicht mehr vorhanden
        raise HTTPException(404, f"Unbekannter Auftrag: {key}") from exc
    return ctx.order_images.public(ctx.db.order(key))


async def _read_upload(request: Request, x_image_length: str | None) -> tuple[bytes, bytes]:
    """Rumpf lesen (höchstens MAX_UPLOAD_BYTES) und in großes Bild und Vorschaubild teilen."""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "Das Bild ist größer als 1 MB.")
    try:
        image_length = int(x_image_length or 0)
    except ValueError:
        image_length = 0
    try:
        return split_upload(bytes(data), image_length)
    except ImageError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.delete("/{key}/image")
def delete_image(request: Request, key: str) -> dict[str, Any]:
    ctx = _ctx(request)
    _check_key(ctx, key)
    ctx.order_images.delete(key)
    return ctx.order_images.public(ctx.db.order(key))


@router.get("/{key}/image")
def order_image(request: Request, key: str, size: str = Query("full", pattern="^(full|thumb)$")) -> FileResponse:
    ctx = _ctx(request)
    _check_key(ctx, key)
    path = ctx.order_images.path(ctx.db.order(key)["image"], size)
    if path is None:
        raise HTTPException(404, "Kein Bild hinterlegt")
    # Die Adresse enthält den Dateinamen als Version (?v=), daher darf der Browser dauerhaft cachen
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


# --- Bild je Aufspannung (Spannsituation; die Live-Karte zeigt es, solange die Aufspannung läuft) ---

VersionQuery = Query("", pattern=r"^([vV]\d{1,2})?$", description="Version, z. B. V1 (leer = Grundversion)")


def _check_setup(ctx: AppContext, key: str, setup: int, version: str) -> str:
    """Aufspannung muss zum Auftrag gehören (über ein Programm mit Lauf oder Planzeit)."""
    _check_key(ctx, key)
    version = version.upper()
    if not any(orders.setup_of(p) == (version, setup) for p in _order_programs(ctx, key)):
        raise HTTPException(404, f"Aufspannung {setup}{f' {version}' if version else ''} gehört nicht zu Auftrag {key}.")
    return version


@router.put("/{key}/setups/{setup}/image")
async def upload_setup_image(
    request: Request, key: str, setup: int, version: str = VersionQuery, x_image_length: str | None = Header(None)
) -> dict[str, Any]:
    """Bild der Aufspannung setzen oder ersetzen – Format wie beim Auftragsbild."""
    ctx = _ctx(request)
    version = _check_setup(ctx, key, setup, version)
    image, thumb = await _read_upload(request, x_image_length)
    try:
        ctx.order_images.save_setup(key, version, setup, image, thumb)
    except ImageError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, f"Unbekannter Auftrag: {key}") from exc
    return ctx.order_images.setup_urls(key, version, setup, ctx.db.setup_image(key, version, setup))


@router.delete("/{key}/setups/{setup}/image")
def delete_setup_image(request: Request, key: str, setup: int, version: str = VersionQuery) -> dict[str, Any]:
    ctx = _ctx(request)
    _check_key(ctx, key)
    ctx.order_images.delete_setup(key, version.upper(), setup)
    return ctx.order_images.setup_urls(key, version.upper(), setup, None)


@router.get("/{key}/setups/{setup}/image")
def setup_image(
    request: Request, key: str, setup: int, version: str = VersionQuery,
    size: str = Query("full", pattern="^(full|thumb)$"),
) -> FileResponse:
    ctx = _ctx(request)
    _check_key(ctx, key)
    path = ctx.order_images.path(ctx.db.setup_image(key, version.upper(), setup), size)
    if path is None:
        raise HTTPException(404, "Kein Bild hinterlegt")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})


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
        ["Auftrag", "Version", "Aufspannung", "Programm", "Lauf-Nr.", "Maschine", "Beginn", "Ende", "Ergebnis",
         "Laufzeit (min)", "Stoppzeit (min)", "Start beobachtet"]
    )
    for run in ctx.db.order_runs(key):
        code = orders.parse_program(run["program"])
        writer.writerow(
            [
                key,
                code.version if code else "",
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
