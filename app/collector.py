"""Erfassung: fragt eine Maschine zyklisch ab und schreibt Intervalle, Läufe und Ereignisse.

Regeln:
- Ein neues Zustandsintervall beginnt, sobald sich Zustand, Programmstatus, Betriebsart,
  Programm oder zugehöriger Lauf ändern; sonst wird nur ``last_seen`` fortgeschrieben.
- Ein Programmdurchlauf beginnt bei LÄUFT und bleibt über GESTOPPT/FEHLER offen. Er endet,
  sobald die Maschine BEREIT meldet oder ein anderes Programm aktiv ist.
- OFFLINE/UNBEKANNT beenden keinen Lauf (kurze Netzstörungen per VPN zerreißen ihn nicht).
- Ist eine Prüfadresse am Standort hinterlegt und antwortet auch sie nicht, ist nicht die
  Maschine aus, sondern die Verbindung zum Standort weg (z. B. VPN). Dann wird nichts
  gebucht; die Lücke erscheint in der Auswertung als "Keine Daten" statt als "Offline".
- Während ein Lauf LÄUFT, wird der Satzverlauf (reine Laufzeit → Satznummer) mitgeschrieben;
  daraus berechnet ``forecast.py`` die Restlaufzeit künftiger Läufe.
- Angewählte NC-Programme werden im Hintergrund gelesen, um die Satzanzahl zu kennen.
- Folgt der Programmname dem Schema JJ-AUFTRAG-AUFSPANNUNG-PROGRAMM, bekommen Abschnitte und
  Läufe die Auftragsnummer; ein neuer Auftrag wird dabei automatisch angelegt (orders.py).
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .adapters.base import MachineAdapter, Snapshot
from .config import MachineConfig
from .db import Database
from .forecast import Forecaster
from .orders import parse_program
from .state import RUN_ACTIVE_STATES, MachineState, classify, run_result

log = logging.getLogger(__name__)

BACKOFF_S = (5, 10, 20, 40, 60)
PROGRESS_SAMPLE_S = 10  # höchstens ein Satzverlauf-Punkt je 10 s reiner Laufzeit
PROGRESS_KEEP_RUNS = 10  # Satzverlauf der letzten 10 Referenzläufe je Programm aufheben


@dataclass
class _Interval:
    id: int
    key: tuple[Any, ...]
    last_seen: float


@dataclass
class _Run:
    id: int
    program: str | None
    started_at: float
    had_error: bool
    start_observed: bool
    last_active: float
    run_s: float = 0.0  # reine Laufzeit (Zustand LÄUFT) seit Start


class MachineCollector:
    def __init__(
        self,
        machine: MachineConfig,
        adapter: MachineAdapter,
        db: Database,
        poll_interval_s: float = 2.0,
        clock: Callable[[], float] = time.time,
        fetch_programs: bool = True,
        program_max_bytes: int = 20_000_000,
    ):
        self.machine = machine
        self._adapter = adapter
        self._db = db
        self.poll_interval_s = poll_interval_s
        self._clock = clock
        self._fetch_programs = fetch_programs
        self._program_max_bytes = program_max_bytes

        db.ensure_machine(machine.id, machine.name, machine.host, machine.port)
        db.close_stale_intervals(machine.id)
        self._interval: _Interval | None = None
        self._run = self._load_open_run()

        # None = noch kein durchgehender Kontakt (Programmstart / Neustart des Tools)
        self._prev_state: MachineState | None = None
        self._state_since: float | None = None
        self._snapshot: Snapshot | None = None
        self._last_tool: str | None = None
        self._last_update: float | None = None
        self._connected = False
        self._control: dict[str, str] = {}
        self._last_error: str | None = None

        self._forecaster = Forecaster(db, machine.id)
        self._forecast: dict[str, Any] | None = None
        self._last_sample: tuple[int, float, tuple[str | None, int]] | None = None
        self._programs: dict[str, dict[str, Any] | None] = {}
        self._program_checked: dict[str, int | None] = {}  # Pfad → Lauf, für den geprüft wurde
        self._fetch_task: asyncio.Task[None] | None = None

    def _load_open_run(self) -> _Run | None:
        row = self._db.open_run(self.machine.id)
        if row is None:
            return None
        return _Run(
            id=row["id"],
            program=row["program"],
            started_at=row["started_at"],
            had_error=bool(row["had_error"]),
            start_observed=bool(row["start_observed"]),
            last_active=row["last_active"],
            run_s=row["run_s"],
        )

    # --- Zustandslogik (synchron, direkt testbar) ------------------------------------

    def process(self, snap: Snapshot | None, now: float, reason: str | None = None, site_down: bool = False) -> None:
        """Eine Abfrage verbuchen. ``snap=None`` bedeutet: Steuerung nicht erreichbar;
        ``site_down``: auch die Prüfadresse am Standort antwortet nicht."""
        if snap is None:
            state = MachineState.NETWORK if site_down else MachineState.OFFLINE
        else:
            state = classify(snap.pgm_state)
        with self._db.transaction():
            if snap is not None:
                self._update_run(snap, state, now)
            self._update_interval(snap, state, now)
            self._record_events(snap, state, now, reason)
            self._update_progress(snap, state)
        self._prev_state = state
        self._snapshot = snap
        self._last_update = now

    def _update_run(self, snap: Snapshot, state: MachineState, now: float) -> None:
        run = self._run
        cur = self._interval
        if run is not None and cur is not None and cur.key[0] == MachineState.RUNNING.value and cur.key[-1] == run.id:
            # Seit der letzten Abfrage lief das Programm durchgehend
            run.run_s += now - cur.last_seen

        if run is not None and (
            state is MachineState.READY or (state in RUN_ACTIVE_STATES and snap.program != run.program)
        ):
            # Lief der Lauf bis eben durchgehend, endet er jetzt; nach einer Unterbrechung
            # (offline / Neustart) beim letzten Lebenszeichen.
            continuous = cur is not None and cur.key[-1] == run.id
            self._db.end_run(run.id, now if continuous else run.last_active, run_result(snap.pgm_state, run.had_error))
            self._db.prune_progress(self.machine.id, run.program, PROGRESS_KEEP_RUNS)
            self._forecaster.invalidate(run.program)
            self._run = run = None

        if run is None and state is MachineState.RUNNING:
            start_observed = self._prev_state not in (None, MachineState.OFFLINE)
            code = parse_program(snap.program)
            run_id = self._db.start_run(
                self.machine.id, snap.program, now, start_observed, code.key if code else None
            )
            self._run = run = _Run(run_id, snap.program, now, False, start_observed, now)

        if run is not None and state is MachineState.ERROR and not run.had_error:
            run.had_error = True
            self._db.mark_run_error(run.id)

    def _update_interval(self, snap: Snapshot | None, state: MachineState, now: float) -> None:
        run_id = self._run.id if self._run is not None and state in RUN_ACTIVE_STATES else None
        key = (
            state.value,
            snap.pgm_state if snap else None,
            snap.exec_mode if snap else None,
            snap.program if snap else None,
            run_id,
        )
        if run_id is not None:
            self._run.last_active = now

        cur = self._interval
        if state is MachineState.NETWORK:
            # Verbindung zum Standort weg: bisherigen Zustand beim letzten Lebenszeichen beenden
            # und bis zur Rückkehr nichts buchen (Lücke = "Keine Daten").
            if cur is not None:
                self._db.close_interval(cur.id, cur.last_seen)
                self._state_since = cur.last_seen
                self._interval = None
            elif self._prev_state is not MachineState.NETWORK:
                self._state_since = now
            return

        if cur is not None and cur.key == key:
            self._db.touch_interval(cur.id, now)
            cur.last_seen = now
            return

        start = now
        if cur is not None:
            # Beim Verbindungsverlust endet der alte Zustand beim letzten Lebenszeichen.
            start = cur.last_seen if state is MachineState.OFFLINE else now
            self._db.close_interval(cur.id, start)
        if cur is None or cur.key[0] != state.value:
            self._state_since = start
        code = parse_program(snap.program) if snap else None
        if code is not None:
            # Neuer Auftrag? Anlegen. Läuft ein abgeschlossener Auftrag wieder, wird er neu geöffnet.
            change = self._db.ensure_order(code.key, code.year, code.order, start, reopen=state is MachineState.RUNNING)
            if change:
                self._db.add_event(self.machine.id, now, f"order_{change}", {"order": code.key, "program": snap.program})
                log.info("Auftrag %s %s (%s)", code.key, "angelegt" if change == "created" else "wieder geöffnet", code.name)
        interval_id = self._db.open_interval(
            self.machine.id, *key[:4], run_id, start, now, order_key=code.key if code else None
        )
        self._interval = _Interval(interval_id, key, now)

    def _record_events(self, snap: Snapshot | None, state: MachineState, now: float, reason: str | None) -> None:
        mid = self.machine.id
        if snap is None:
            if state is MachineState.NETWORK and self._prev_state is not MachineState.NETWORK:
                self._db.add_event(mid, now, "site_unreachable", {"reason": reason, "check_host": self.machine.check_host})
            elif state is MachineState.OFFLINE and self._prev_state is not MachineState.OFFLINE:
                self._db.add_event(mid, now, "offline", {"reason": reason})
            return
        if snap.tool:
            # Gegen das letzte bekannte Werkzeug vergleichen: Abfragen ohne Werkzeuginfo
            # (oder ein Neuverbinden) erzeugen so keine Schein-Wechsel.
            if self._last_tool and snap.tool != self._last_tool:
                self._db.add_event(
                    mid, now, "tool_change", {"from": self._last_tool, "to": snap.tool, "program": snap.program}
                )
            self._last_tool = snap.tool
        prev = self._snapshot
        for text in sorted(set(snap.errors) - set(prev.errors if prev else ())):
            self._db.add_event(mid, now, "nc_error", {"text": text, "program": snap.program})

    def _update_progress(self, snap: Snapshot | None, state: MachineState) -> None:
        run = self._run
        if snap is None or run is None or state not in RUN_ACTIVE_STATES:
            self._forecast = None
            return
        position = (snap.current_program or snap.program, snap.line_no)
        if state is MachineState.RUNNING and snap.line_no is not None:
            last = self._last_sample
            if last is None or last[0] != run.id or (last[2] != position and run.run_s - last[1] >= PROGRESS_SAMPLE_S):
                self._db.add_progress(run.id, run.run_s, position[0], snap.line_no)
                self._last_sample = (run.id, run.run_s, position)
        self._forecast = self._forecaster.update(
            run.id, run.program, run.run_s, snap.current_program, snap.line_no, self._blocks(position[0]),
            run.start_observed,
        )

    # --- Programmdateien (Satzanzahl) -------------------------------------------------

    def _program_row(self, path: str) -> dict[str, Any] | None:
        if path not in self._programs:
            self._programs[path] = self._db.program_file(self.machine.id, path)
        return self._programs[path]

    def _blocks(self, path: str | None) -> int | None:
        row = self._program_row(path) if path else None
        return row["blocks"] if row else None

    def _maybe_fetch_programs(self, snap: Snapshot) -> None:
        """Angewählte Programme einmal je Lauf prüfen (bei Änderung neu einlesen)."""
        fetch = getattr(self._adapter, "fetch_program", None)
        if not self._fetch_programs or fetch is None or (self._fetch_task and not self._fetch_task.done()):
            return
        marker = self._run.id if self._run else None
        paths = [
            p for p in dict.fromkeys((snap.program, snap.current_program))
            if p and self._program_checked.get(p, -1) != marker
        ]
        if paths:
            self._fetch_task = asyncio.create_task(self._fetch(fetch, paths, marker))

    async def _fetch(self, fetch: Callable[..., Any], paths: list[str], marker: int | None) -> None:
        for path in paths:
            row = self._program_row(path)
            known = (row["size"], row["mtime"]) if row and row["size"] is not None else None
            try:
                result = await asyncio.to_thread(fetch, path, known, self._program_max_bytes)
            except Exception as exc:
                log.warning("%s: Programm %s nicht lesbar: %s", self.machine.name, path, exc)
                self._program_checked[path] = marker
                continue
            now = self._clock()
            if result is None:
                self._db.touch_program_file(self.machine.id, path, now)
            else:
                self._db.save_program_file(
                    self.machine.id, path, result.size, result.mtime, result.blocks, result.error, now
                )
                log.info("%s: %s eingelesen – %s", self.machine.name, path, result.error or f"{result.blocks} Sätze")
            self._programs.pop(path, None)
            self._program_checked[path] = marker

    # --- Live-Status ---------------------------------------------------------------

    def live(self) -> dict[str, Any]:
        snap = self._snapshot
        run = self._run
        forecast = None
        if self._forecast is not None and self._last_update is not None:
            forecast = {**self._forecast, "eta": self._last_update + self._forecast["remaining_s"]}
        blocks = None
        if snap is not None and snap.line_no is not None:
            path = snap.current_program or snap.program
            row = self._program_row(path) if path else None
            blocks = {
                "program": path,
                "line": snap.line_no,
                "total": row["blocks"] if row else None,
                "error": row["error"] if row else None,
            }
        return {
            **self.machine.public(),
            "state": self._prev_state.value if self._prev_state else None,
            "state_since": self._state_since,
            "last_update": self._last_update,
            "connected": self._connected,
            "connection_error": self._last_error,
            "control": self._control,
            "pgm_state": snap.pgm_state if snap else None,
            "exec_mode": snap.exec_mode if snap else None,
            "program": snap.program if snap else None,
            "current_program": snap.current_program if snap else None,
            "line_no": snap.line_no if snap else None,
            "blocks": blocks,
            "order": (code.public() if (code := parse_program(snap.program if snap else None)) else None),
            "tool": snap.tool if snap else None,
            "override": {
                "feed": snap.override_feed if snap else None,
                "spindle": snap.override_spindle if snap else None,
                "rapid": snap.override_rapid if snap else None,
            },
            "errors": list(snap.errors) if snap else [],
            "run": (
                {
                    "id": run.id,
                    "program": run.program,
                    "started_at": run.started_at,
                    "start_observed": run.start_observed,
                    "had_error": run.had_error,
                    "run_s": run.run_s,
                }
                if run
                else None
            ),
            "forecast": forecast,
        }

    # --- Abfrageschleife -----------------------------------------------------------

    async def run(self) -> None:
        failures = 0
        try:
            while True:
                try:
                    if not self._connected:
                        info = await asyncio.to_thread(self._adapter.connect)
                        self._connected = True
                        self._control = info
                        self._last_error = None
                        failures = 0
                        self._db.add_event(self.machine.id, self._clock(), "online", info)
                        log.info("%s verbunden: %s", self.machine.name, info)
                    snap = await asyncio.to_thread(self._adapter.read)
                except Exception as exc:  # Netzwerk, Protokoll, Steuerung aus …
                    if self._connected:
                        self._connected = False
                        await asyncio.to_thread(self._adapter.close)
                    await self._on_failure(str(exc))
                    await asyncio.sleep(BACKOFF_S[min(failures, len(BACKOFF_S) - 1)])
                    failures += 1
                    continue
                try:
                    self.process(snap, self._clock())
                    self._maybe_fetch_programs(snap)
                except Exception:
                    log.exception("%s: Verbuchen fehlgeschlagen", self.machine.name)
                await asyncio.sleep(self.poll_interval_s)
        finally:
            if self._fetch_task is not None:
                self._fetch_task.cancel()
            await asyncio.to_thread(self._adapter.close)

    async def _site_down(self) -> bool:
        """True, wenn eine Prüfadresse hinterlegt ist und sie ebenfalls nicht antwortet."""
        check = getattr(self._adapter, "site_reachable", None)
        if not self.machine.check_host or check is None:
            return False
        try:
            return not await asyncio.to_thread(check, self.machine.check_host)
        except Exception:
            log.exception("%s: Prüfadresse %s nicht prüfbar", self.machine.name, self.machine.check_host)
            return False

    async def _on_failure(self, reason: str) -> None:
        site_down = await self._site_down()
        if site_down:
            reason = f"{reason} – auch die Prüfadresse {self.machine.check_host} antwortet nicht"
        if reason != self._last_error:
            log.warning("%s %s: %s", self.machine.name, "Standort nicht erreichbar" if site_down else "offline", reason)
        self._last_error = reason
        try:
            self.process(None, self._clock(), reason, site_down=site_down)
        except Exception:
            log.exception("%s: Verbuchen fehlgeschlagen", self.machine.name)
