"""REST-API: Live-Status, Zeitleiste, Auswertung und CSV-Export.

Zeitangaben in Antworten sind Unix-Sekunden (UTC). ``from``/``to`` akzeptieren Unix-Sekunden
oder ISO-Zeitstempel (ohne Zeitzone = lokale Zeitzone aus der Konfiguration).
"""

from __future__ import annotations

import asyncio
import csv
import io
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, Response

from . import BUILD, __version__, changelog, diagnostics, orders, stats, tools
from .state import EXEC_MODE_LABELS, PGM_STATE_LABELS, RUN_RESULT_LABELS, STATE_LABELS

if TYPE_CHECKING:
    from .main import AppContext

router = APIRouter(prefix="/api")

FromQuery = Query(None, alias="from", description="Beginn (Unix-Sekunden oder ISO)")
ToQuery = Query(None, description="Ende (Unix-Sekunden oder ISO), Standard: jetzt")


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _parse_time(value: str | None, tz: ZoneInfo) -> float | None:
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(400, f"Ungültiger Zeitpunkt: {value!r}") from exc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=tz)
    return dt.timestamp()


def _range(ctx: AppContext, from_: str | None, to: str | None) -> tuple[float, float]:
    now = time.time()
    today = datetime.fromtimestamp(now, ctx.tz).replace(hour=0, minute=0, second=0, microsecond=0)
    t0 = _parse_time(from_, ctx.tz)
    t1 = _parse_time(to, ctx.tz)
    t0 = today.timestamp() if t0 is None else t0
    t1 = now if t1 is None else min(t1, now)
    if t1 <= t0:
        raise HTTPException(400, "Der Zeitraum ist leer (Ende liegt vor dem Beginn)")
    return t0, t1


def _check_machine(ctx: AppContext, machine_id: str | None) -> None:
    if machine_id is not None and machine_id not in ctx.collectors:
        raise HTTPException(404, f"Unbekannte Maschine: {machine_id}")


@router.get("/meta")
def meta(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    return {
        "machines": [m.public() for m in ctx.manager.machines()],
        "timezone": ctx.settings.timezone,
        "simulate": ctx.settings.simulate,
        "version": __version__,
        "build": BUILD,
        "tool_alerts": tools.alert_count(ctx.db, set(ctx.collectors)),
        "poll_interval_s": ctx.settings.poll_interval_s,
        "labels": {
            "state": {**{s.value: label for s, label in STATE_LABELS.items()}, stats.NO_DATA: "Keine Daten"},
            "pgm_state": PGM_STATE_LABELS,
            "exec_mode": EXEC_MODE_LABELS,
            "run_result": RUN_RESULT_LABELS,
        },
    }


@router.get("/version")
def version() -> dict[str, Any]:
    return {"version": __version__, "build": BUILD, "changelog": changelog.load()}


@router.get("/diagnose.zip")
async def diagnose(
    request: Request,
    days: int = Query(7, ge=1, le=365, description="Zeitraum der Auswertungsdaten in Tagen"),
    db: bool = Query(False, description="Datenbank beifügen"),
) -> Response:
    """Diagnose-Datei für Fehlermeldungen (siehe diagnostics.py)."""
    ctx = _ctx(request)
    captured = diagnostics.capture(ctx)  # in der Ereignisschleife, wie die Erfassung
    data = await asyncio.to_thread(diagnostics.build_zip, ctx, captured, days, db)
    stamp = datetime.now(ctx.tz).strftime("%Y-%m-%d_%H%M")
    return Response(
        content=data,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="ld-diagnose_{stamp}.zip"'},
    )


@router.get("/machines")
def machines(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    machines = []
    for collector in ctx.manager.ordered_collectors():
        live = collector.live()
        live["tool_info"] = tools.tool_info(ctx.db, live["id"], live["tool"])
        if live["order"] and (order := ctx.db.order(live["order"]["key"])):
            # Bild des Bauteils und der laufenden Aufspannung (die Live-Karte zeigt dieses bevorzugt), so
            # groß wie das Maschinenbild. Das große Bild lädt der Browser nur bei hoher Pixeldichte (srcset).
            code = live["order"]
            public = ctx.order_images.public(order)
            code["thumb_url"], code["image_url"] = public["thumb_url"], public["image_url"]
            image = ctx.db.setup_image(code["key"], code["version"], code["setup"])
            urls = ctx.order_images.setup_urls(code["key"], code["version"], code["setup"], image)
            code["setup_thumb_url"], code["setup_image_url"] = urls["thumb_url"], urls["image_url"]
        if live["order"] and live["order"]["kind"] == "rim":
            live["order"]["rim"] = orders.rim_info(live["order"]["key"], ctx.db.rim_design_names())
        machines.append(live)
    return {"now": time.time(), "machines": machines}


@router.get("/machines/{machine_id}/image")
def machine_image(request: Request, machine_id: str) -> FileResponse:
    path = _ctx(request).manager.image_path(machine_id)
    if path is None:
        raise HTTPException(404, "Kein Bild hinterlegt")
    # Die URL enthält den Dateinamen als Version, daher darf der Browser lange cachen
    return FileResponse(path, headers={"Cache-Control": "public, max-age=31536000, immutable"})


@router.get("/machines/{machine_id}/timeline")
def timeline(request: Request, machine_id: str, from_: str | None = FromQuery, to: str | None = ToQuery) -> dict[str, Any]:
    ctx = _ctx(request)
    _check_machine(ctx, machine_id)
    t0, t1 = _range(ctx, from_, to)
    intervals = stats.clip(ctx.db.intervals(t0, t1, machine_id), t0, t1)
    keys = ("state", "pgm_state", "exec_mode", "program", "run_id", "start", "end")
    return {"from": t0, "to": t1, "intervals": [{k: iv[k] for k in keys} for iv in intervals]}


@router.get("/stats")
def get_stats(request: Request, from_: str | None = FromQuery, to: str | None = ToQuery) -> dict[str, Any]:
    ctx = _ctx(request)
    t0, t1 = _range(ctx, from_, to)
    return stats.summarize(ctx.db, list(ctx.collectors), t0, t1, ctx.tz, time.time())


@router.get("/runs")
def runs(
    request: Request, machine: str | None = None, from_: str | None = FromQuery, to: str | None = ToQuery
) -> list[dict[str, Any]]:
    ctx = _ctx(request)
    _check_machine(ctx, machine)
    t0, t1 = _range(ctx, from_, to)
    return ctx.db.runs(t0, t1, machine)


@router.get("/events")
def events(
    request: Request,
    machine: str | None = None,
    from_: str | None = FromQuery,
    to: str | None = ToQuery,
    limit: int = Query(200, ge=1, le=5000),
) -> list[dict[str, Any]]:
    ctx = _ctx(request)
    _check_machine(ctx, machine)
    t0, t1 = _range(ctx, from_, to)
    return ctx.db.events(t0, t1 + 1, machine, limit)


def _local(t: float | None, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(t, tz).strftime("%d.%m.%Y %H:%M:%S") if t is not None else ""


def _minutes(seconds: float | None) -> str:
    # Komma als Dezimaltrenner, damit ein deutsches Excel die Zahlen direkt erkennt
    return f"{seconds / 60:.2f}".replace(".", ",") if seconds is not None else ""


@router.get("/export.csv")
def export_csv(
    request: Request,
    kind: Literal["intervals", "runs"] = "intervals",
    machine: str | None = None,
    from_: str | None = FromQuery,
    to: str | None = ToQuery,
) -> Response:
    ctx = _ctx(request)
    _check_machine(ctx, machine)
    t0, t1 = _range(ctx, from_, to)
    names = {m.id: m.name for m in ctx.manager.machines()}
    state_labels = {s.value: label for s, label in STATE_LABELS.items()}
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";", lineterminator="\r\n")

    if kind == "intervals":
        writer.writerow(
            ["Maschine", "Zustand", "Programmstatus", "Betriebsart", "Programm", "Lauf-Nr.", "Beginn", "Ende", "Dauer (min)"]
        )
        for iv in stats.clip(ctx.db.intervals(t0, t1, machine), t0, t1):
            if iv["machine_id"] not in names:  # entfernte Maschinen nicht exportieren
                continue
            writer.writerow(
                [
                    names.get(iv["machine_id"], iv["machine_id"]),
                    state_labels.get(iv["state"], iv["state"]),
                    PGM_STATE_LABELS.get(iv["pgm_state"] or "", ""),
                    EXEC_MODE_LABELS.get(iv["exec_mode"] or "", ""),
                    iv["program"] or "",
                    iv["run_id"] or "",
                    _local(iv["start"], ctx.tz),
                    _local(iv["end"], ctx.tz),
                    _minutes(iv["end"] - iv["start"]),
                ]
            )
    else:
        writer.writerow(
            ["Lauf-Nr.", "Maschine", "Programm", "Beginn", "Ende", "Ergebnis",
             "Laufzeit (min)", "Stoppzeit (min)", "Gesamt (min)", "Start beobachtet"]
        )
        for run in ctx.db.runs(t0, t1, machine):
            if run["machine_id"] not in names:
                continue
            end = run["ended_at"]
            writer.writerow(
                [
                    run["id"],
                    names.get(run["machine_id"], run["machine_id"]),
                    run["program"] or "",
                    _local(run["started_at"], ctx.tz),
                    _local(end, ctx.tz),
                    RUN_RESULT_LABELS.get(run["result"] or "", "läuft"),
                    _minutes(run["run_s"]),
                    _minutes(run["stop_s"]),
                    _minutes((end or run["last_active"]) - run["started_at"]),
                    "ja" if run["start_observed"] else "nein",
                ]
            )

    day = datetime.fromtimestamp(t0, ctx.tz).strftime("%Y-%m-%d")
    filename = f"laufzeit_{'intervalle' if kind == 'intervals' else 'laeufe'}_{day}.csv"
    return Response(
        content="\ufeff" + buf.getvalue(),  # BOM: Excel erkennt UTF-8 (Umlaute)
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
