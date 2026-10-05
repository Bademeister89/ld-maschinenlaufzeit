"""Diagnose-Datei für Fehlermeldungen: eine ZIP-Datei mit allem, was zur Fehlersuche nötig ist.

Inhalt (Zeiten als Ortszeit der Konfiguration):

- ``LIESMICH.txt``           kurze Beschreibung der Dateien
- ``info.json``              Version, Build, Laufzeit, Einstellungen, Maschinen, Verbindungsstatus
- ``live.json``              aktueller Live-Status aller Maschinen
- ``mitschnitt_<id>.csv``    jede Änderung dessen, was die Steuerung meldet, seit dem Start der App
- ``laeufe.csv``             Läufe des Zeitraums mit Programmstatus am Laufende und danach
- ``zustaende.csv``          Zustandsabschnitte des Zeitraums
- ``ereignisse.csv``         Ereignisse des Zeitraums
- ``programmdateien.csv``    gelesene Programmdateien (Satzanzahl, Aufrufe, Fehler)
- ``logs/``                  Log-Dateien der App
- ``data.db``                auf Wunsch die ganze Datenbank

Passwörter kennt die App nicht; enthalten sind aber Maschinenadressen und Programmnamen.
"""

from __future__ import annotations

import csv
import io
import json
import platform
import sys
import tempfile
import time
import zipfile
from bisect import bisect_left
from collections import defaultdict
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any

from . import BUILD, __version__

if TYPE_CHECKING:
    from .main import AppContext

README = """LD-Machine-Viewer – Diagnose-Datei

info.json            Version, Build, Laufzeit, Einstellungen, Maschinen und Verbindungsstatus
live.json            Live-Status aller Maschinen beim Erstellen der Datei
mitschnitt_*.csv     Je Maschine jede Änderung der Steuerungsdaten seit dem Start der App
                     (Programmstatus, Betriebsart, Haupt- und aktuelles Programm, Satz davor/danach)
                     und was die Erfassung daraus gemacht hat (gezähltes Programm, Lauf)
laeufe.csv           Programmläufe mit Ergebnis und dem Programmstatus am Ende bzw. danach
zustaende.csv        Zustandsabschnitte (Läuft, Gestoppt, Bereit …) mit Programmstatus
ereignisse.csv       Ereignisse (Verbindung, Werkzeugwechsel, NC-Fehler, Aufträge …)
programmdateien.csv  Gelesene Programmdateien mit Satzanzahl, aufgerufenen Programmen und Fehlern
logs/                Log-Dateien der App
data.db              Datenbank (nur wenn beim Herunterladen ausgewählt)
"""


def _local(t: float | None, ctx: AppContext) -> str:
    return "" if t is None else datetime.fromtimestamp(t, ctx.tz).strftime("%Y-%m-%d %H:%M:%S")


def _csv(rows: list[dict[str, Any]], columns: list[str]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(columns)
    for row in rows:
        writer.writerow(["" if row.get(c) is None else row.get(c) for c in columns])
    return ("﻿" + buf.getvalue()).encode("utf-8")  # BOM: Excel erkennt UTF-8


def _version(package: str) -> str | None:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return None


def capture(ctx: AppContext) -> dict[str, Any]:
    """Live-Status und Mitschnitt abgreifen – in der Ereignisschleife aufrufen, in der auch die
    Erfassung läuft, damit sich nichts ändert, während kopiert wird."""
    return {
        "live": {mid: c.live() for mid, c in ctx.collectors.items()},
        "raw": {mid: c.raw_log() for mid, c in ctx.collectors.items()},
        "since": {mid: c.started_at for mid, c in ctx.collectors.items()},
    }


def _info(ctx: AppContext, captured: dict[str, Any], days: int, now: float) -> dict[str, Any]:
    s = ctx.settings
    db_path = Path(ctx.db.path)
    machines = []
    for m in ctx.manager.machines():
        live = captured["live"].get(m.id, {})
        since = captured["since"].get(m.id)
        machines.append({
            **m.public(),
            "erfasst": m.id in captured["live"],
            "erfasst_seit": _local(since, ctx),
            "verbunden": live.get("connected"),
            "steuerung": live.get("control"),
            "letzter_fehler": live.get("connection_error"),
            "letzte_abfrage": _local(live.get("last_update"), ctx),
        })
    return {
        "erstellt": _local(now, ctx),
        "zeitraum_tage": days,
        "version": __version__,
        "build": BUILD,
        "app_gestartet": _local(ctx.started_at, ctx),
        "python": sys.version,
        "plattform": platform.platform(),
        "pylsv2": _version("pyLSV2"),
        "fastapi": _version("fastapi"),
        "zeitzone": s.timezone,
        "simulation": s.simulate,
        "datenordner": str(s.data_dir),
        "datenbank": str(db_path),
        "datenbank_mb": round(db_path.stat().st_size / 1e6, 2) if db_path.exists() else None,
        "schema": ctx.db.get_meta("schema_version"),
        "einstellungen": {
            "poll_interval_s": s.poll_interval_s,
            "timeout_s": s.timeout_s,
            "fetch_programs": s.fetch_programs,
            "program_max_mb": s.program_max_mb,
            "listen_host": s.listen_host,
            "port": s.port,
        },
        "maschinen": machines,
    }


def _runs_with_end_state(ctx: AppContext, t0: float, now: float) -> list[dict[str, Any]]:
    """Läufe mit dem Programmstatus ihres letzten Abschnitts und des ersten Abschnitts danach –
    daran sieht man, was die Steuerung beim Programmende gemeldet hat."""
    intervals: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for iv in ctx.db.intervals(t0 - 86_400, now):
        intervals[iv["machine_id"]].append(iv)
    starts = {mid: [iv["start"] for iv in ivs] for mid, ivs in intervals.items()}
    rows = []
    for run in ctx.db.runs(t0, now):
        ivs = intervals.get(run["machine_id"], [])
        own = [iv for iv in ivs if iv["run_id"] == run["id"]]
        after = None
        if run["ended_at"] is not None:
            i = bisect_left(starts.get(run["machine_id"], []), run["ended_at"] - 0.001)
            after = next((iv for iv in ivs[i:] if iv["run_id"] != run["id"]), None)
        rows.append({
            **run,
            "start": _local(run["started_at"], ctx),
            "ende": _local(run["ended_at"], ctx),
            "status_am_ende": f"{own[-1]['state']}/{own[-1]['pgm_state']}" if own else None,
            "status_danach": f"{after['state']}/{after['pgm_state']}" if after else None,
            "programm_danach": after["program"] if after else None,
        })
    return rows


def build_zip(ctx: AppContext, captured: dict[str, Any], days: int = 7, include_db: bool = False) -> bytes:
    """ZIP-Datei bauen (``captured`` aus ``capture``); darf in einem eigenen Thread laufen."""
    now = time.time()
    t0 = now - days * 86_400
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("LIESMICH.txt", README)
        z.writestr("info.json", json.dumps(_info(ctx, captured, days, now), ensure_ascii=False, indent=2, default=str))
        z.writestr("live.json", json.dumps(captured["live"], ensure_ascii=False, indent=2, default=str))

        for mid, raw in captured["raw"].items():
            rows = [{**r, "zeit": _local(r["t"], ctx)} for r in raw]
            z.writestr(f"mitschnitt_{mid}.csv", _csv(rows, [
                "zeit", "t", "state", "pgm_state", "exec_mode", "program", "current_program", "line_before",
                "line_no", "tool", "errors", "counted_program", "caller", "run_id", "run_program", "reason",
            ]))

        z.writestr("laeufe.csv", _csv(_runs_with_end_state(ctx, t0, now), [
            "id", "machine_id", "program", "start", "ende", "result", "had_error", "start_observed", "run_s",
            "stop_s", "status_am_ende", "status_danach", "programm_danach", "started_at", "ended_at",
        ]))
        intervals = [
            {**iv, "von": _local(iv["start"], ctx), "bis": _local(iv["end"], ctx), "dauer_s": round(iv["end"] - iv["start"], 1)}
            for iv in ctx.db.intervals(t0, now)
        ]
        z.writestr("zustaende.csv", _csv(intervals, [
            "machine_id", "von", "bis", "dauer_s", "state", "pgm_state", "exec_mode", "program", "run_id", "open",
            "start", "end",
        ]))
        events = [
            {**e, "zeit": _local(e["ts"], ctx), "daten": json.dumps(e["payload"], ensure_ascii=False)}
            for e in reversed(ctx.db.events(t0, now, limit=200_000))
        ]
        z.writestr("ereignisse.csv", _csv(events, ["machine_id", "zeit", "type", "daten", "ts"]))
        files = [
            {**f, "gelesen": _local(f["checked_at"], ctx), "geaendert": _local(f["mtime"], ctx),
             "calls": None if f["calls"] is None else f["calls"].replace("\n", ", ")}
            for f in ctx.db.program_files()
        ]
        z.writestr("programmdateien.csv", _csv(files, [
            "machine_id", "path", "size", "blocks", "error", "calls", "gelesen", "geaendert",
        ]))

        log_dir = ctx.settings.data_dir / "logs"
        for path in sorted(log_dir.glob("*.log*")) if log_dir.is_dir() else []:
            z.write(path, f"logs/{path.name}")

        if include_db:
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / "data.db"
                ctx.db.backup_to(copy)
                z.write(copy, "data.db")
    return buf.getvalue()
