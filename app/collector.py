"""Erfassung: fragt eine Maschine zyklisch ab und schreibt Intervalle, Läufe und Ereignisse.

Regeln:
- Ein neues Zustandsintervall beginnt, sobald sich Zustand, Programmstatus, Betriebsart,
  Programm oder zugehöriger Lauf ändern; sonst wird nur ``last_seen`` fortgeschrieben.
- Ein Programmdurchlauf beginnt bei LÄUFT im Programmlauf (nicht im Handbetrieb oder bei MDI) und
  bleibt über GESTOPPT/FEHLER offen. Er endet, sobald die Maschine BEREIT meldet oder ein anderes
  Programm aktiv ist. Fertig ist er bei "beendet" oder, wie bei der iTNC 530, wenn die Steuerung
  aus dem Programmlauf direkt auf "inaktiv" geht.
- Ein Satzvorlauf dort, wo der letzte Lauf desselben Programms endete (z. B. nach einer Störung),
  setzt diesen Lauf fort. Ein anderer Start mitten im Programm gilt als Teillauf (ohne beobachteten
  Start, zählt nicht in Ø-Stückzeiten und Prognose).
- OFFLINE/UNBEKANNT beenden keinen Lauf (kurze Netzstörungen per VPN zerreißen ihn nicht).
- Ist eine Prüfadresse am Standort hinterlegt und antwortet auch sie nicht, ist nicht die
  Maschine aus, sondern die Verbindung zum Standort weg (z. B. VPN). Dann wird nichts
  gebucht; die Lücke erscheint in der Auswertung als "Keine Daten" statt als "Offline".
- Während ein Lauf LÄUFT, wird der Satzverlauf (reine Laufzeit → Satznummer) mitgeschrieben;
  daraus berechnet ``forecast.py`` die Restlaufzeit künftiger Läufe.
- Angewählte NC-Programme werden im Hintergrund gelesen, um die Satzanzahl zu kennen.
- Folgt der Programmname dem Schema JJ-AUFTRAG-AUFSPANNUNG-PROGRAMM, bekommen Abschnitte und
  Läufe die Auftragsnummer; ein neuer Auftrag wird dabei automatisch angelegt (orders.py).
- Oberprogramme (z. B. ein Palettenprogramm auf der Automation) werden übersprungen: Ruft ein
  Hauptprogramm ohne Auftragsnummer ein Auftragsprogramm per CALL PGM auf, zählt alles für das
  aufgerufene Programm. Läuft das Oberprogramm selbst (Palettenwechsel zwischen den Aufrufen) oder
  ein Programm ohne Auftragsnummer, das es selbst aufruft (z. B. Reinigung), entsteht kein Lauf.
  Kehrt die Steuerung ins Oberprogramm zurück, ist das aufgerufene Programm fertig. Wer ein Programm
  aufruft, zeigen die CALL-PGM-Zeilen in den Dateien von Ober- und Auftragsprogramm (siehe
  ``_called_by_caller``).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from statistics import median
from typing import Any

from .adapters.base import MachineAdapter, Snapshot
from .config import MachineConfig
from .db import Database
from .forecast import Forecaster
from .nc_program import PalletEntry, call_name, pallet_entries
from .orders import parse_program
from .state import MANUAL_MODES, RUN_ACTIVE_STATES, MachineState, classify, run_result
from .tools import DEFAULT_LIMIT_S, DEFAULT_WARN_S, is_call, parse_tool, spindle_number

log = logging.getLogger(__name__)

BACKOFF_S = (5, 10, 20, 40, 60)
PROGRESS_SAMPLE_S = 10  # höchstens ein Satzverlauf-Punkt je 10 s reiner Laufzeit
PROGRESS_KEEP_RUNS = 10  # Satzverlauf der letzten 10 Referenzläufe je Programm aufheben
TOOL_TABLE_CHECK_S = 600  # Werkzeugtabelle (Namen) alle 10 min auf Änderungen prüfen …
TOOL_TABLE_RETRY_S = 60  # … und früher, wenn ein Werkzeug ohne bekannten Namen auftaucht
PROGRAM_RETRY_S = 60  # nicht lesbare Programmdatei jede Minute erneut versuchen
RAW_LOG_KEEP = 5000  # Mitschnitt: so viele Statusänderungen je Maschine im Speicher halten
# Start mitten im Programm (Satzvorlauf): erste Satznummer jenseits von 5 % der Sätze (mind. 100)
MID_START_SHARE = 0.05
MID_START_LINES = 100
RESUME_MAX_GAP_S = 12 * 3600  # so lange nach dem Ende kann ein Satzvorlauf den Lauf noch fortsetzen
# Palettenprogramm nach einem Neustart der App: so weit zurück nach Läufen des Durchgangs suchen; ein
# längeres Bereit trennt zwei Durchgänge (zwischen den Paletten meldet die Steuerung nur Sekunden)
PALLET_LOOKBACK_S = 7 * 86_400
PALLET_PAUSE_S = 300


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


@dataclass(frozen=True)
class _Ended:
    """Zuletzt beendeter Lauf – ein Satzvorlauf an dieser Stelle setzt ihn fort."""

    id: int
    program: str | None
    ended_at: float
    last_line: int | None


@dataclass
class _PalletRun:
    """Stand des angewählten Palettenprogramms (.P) – nur im Speicher."""

    table: str
    pos: int | None = None  # Index der Programmzeile, die gerade läuft (bzw. zuletzt lief)
    name: str | None = None  # zuletzt gesehenes Programm der Tabelle (``call_name``), None = dazwischen
    stopped: bool = False  # Palettenprogramm beendet/abgebrochen: weiter an derselben Zeile oder von vorn
    entry_run_s: float = 0.0  # reine Laufzeit seit Beginn der aktuellen Zeile
    entry_observed: bool = False  # Beginn der aktuellen Zeile gesehen, ohne Lücke (sonst nicht messen)
    # Lief das Palettenprogramm schon, als die App es zum ersten Mal sah (Neustart, Update)? Dann wird
    # die Stelle einmal aus den Läufen in der Datenbank bestimmt.
    joined_running: bool = False
    recovered: bool = False
    # Beginn je Zeile in diesem Durchgang: Zeile → (Zeitpunkt, Beginn beobachtet)
    started: dict[int, tuple[float, bool]] = field(default_factory=dict)
    # Gemessene reine Laufzeit je Programm (``call_name``) von Beginn einer Zeile bis zur nächsten,
    # also einschließlich Palettenwechsel
    durations: dict[str, list[float]] = field(default_factory=dict)


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
        self._ended: _Ended | None = None
        self._callers: set[str] = set()  # erkannte Oberprogramme (rufen Auftragsprogramme auf)
        self._pallet: _PalletRun | None = None  # angewähltes Palettenprogramm (Ablaufliste)
        # Programme mit fertigen Läufen nach Name (``call_name``) – für die Zeiten der Ablaufliste
        self._run_paths: dict[str, list[str]] | None = None

        # None = noch kein durchgehender Kontakt (Programmstart / Neustart des Tools)
        self._prev_state: MachineState | None = None
        self._state_since: float | None = None
        self._snapshot: Snapshot | None = None
        self._last_tool: str | None = None
        self._seen_tool: int | None = None  # zuletzt gesehene T-Nummer (Anlegen neuer Werkzeuge)
        self._spindle: int | None = None  # T-Nummer in der Spindel (0 = leer, None = unbekannt) – Aufrufe
        self._tool_use: tuple[int, int] | None = None  # offener Einsatzabschnitt: (id, T-Nummer)
        self._last_update: float | None = None
        self._connected = False
        self._control: dict[str, str] = {}
        self._last_error: str | None = None

        self._forecaster = Forecaster(db, machine.id)
        self._forecast: dict[str, Any] | None = None
        self._last_sample: tuple[int, float, tuple[str | None, int]] | None = None
        self._programs: dict[str, dict[str, Any] | None] = {}
        self._program_checked: dict[str, int | None] = {}  # Pfad → Lauf, für den geprüft wurde
        self._program_tried: dict[str, float] = {}  # Pfad → letzter Leseversuch
        self._program_failures: dict[str, str] = {}  # Pfad → Fehler beim letzten Leseversuch
        self._fetch_task: asyncio.Task[None] | None = None  # Programm- bzw. Werkzeugtabellen-Lesen
        self._tool_names: dict[int, str] = {}  # aus TOOL.T
        self._tool_table: tuple[int, float] | None = None  # (Größe, Änderungszeit) beim letzten Lesen
        self._tool_table_checked: float | None = None
        self._tool_name_missing = False
        self._tool_names_logged = False  # erstes Lesen der Werkzeugtabelle schon im Log
        # Mitschnitt der Rohdaten (nur im Speicher, seit dem Start) für die Diagnose-Datei
        self._raw: deque[dict[str, Any]] = deque(maxlen=RAW_LOG_KEEP)
        self._raw_key: tuple[Any, ...] | None = None
        self._raw_last: Snapshot | None = None
        self.started_at = clock()

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
        raw = snap
        if snap is None:
            state = MachineState.NETWORK if site_down else MachineState.OFFLINE
        else:
            state = classify(snap.pgm_state)
            snap = self._resolve_program(snap)
        with self._db.transaction():
            if snap is not None:
                self._update_run(snap, state, now)
            self._update_interval(snap, state, now)
            self._update_tool(snap, state, now)
            self._record_events(snap, state, now, reason)
            self._update_progress(snap, state)
        self._track_pallet(raw, snap, state, now)
        self._record_raw(raw, snap, state, now, reason)
        self._prev_state = state
        self._snapshot = snap
        self._last_update = now

    def _record_raw(
        self, raw: Snapshot | None, snap: Snapshot | None, state: MachineState, now: float, reason: str | None
    ) -> None:
        """Mitschnitt für die Diagnose-Datei: jede Änderung dessen, was die Steuerung meldet, mit
        dem Satz davor und danach und dem, was die Erfassung daraus gemacht hat."""
        key = (
            (state.value, reason)
            if raw is None
            else (raw.pgm_state, raw.exec_mode, raw.program, raw.current_program, raw.errors)
        )
        if key != self._raw_key:
            run = self._run
            self._raw.append({
                "t": now,
                "state": state.value,
                "pgm_state": raw.pgm_state if raw else None,
                "exec_mode": raw.exec_mode if raw else None,
                "program": raw.program if raw else None,
                "current_program": raw.current_program if raw else None,
                "line_before": self._raw_last.line_no if self._raw_last else None,
                "line_no": raw.line_no if raw else None,
                "tool": raw.tool if raw else None,
                "errors": " | ".join(raw.errors) if raw else None,
                "counted_program": snap.program if snap else None,
                "caller": snap.caller if snap else None,
                "run_id": run.id if run else None,
                "run_program": run.program if run else None,
                "reason": reason,
            })
            self._raw_key = key
        self._raw_last = raw

    def raw_log(self) -> list[dict[str, Any]]:
        return list(self._raw)

    def _resolve_program(self, snap: Snapshot) -> Snapshot:
        """Auftragsprogramm statt Oberprogramm: Die Steuerung meldet das angewählte Hauptprogramm
        (``program``) und das gerade abgearbeitete Programm (``current_program``). Ist das
        Hauptprogramm kein Auftragsprogramm, ruft aber eines auf, gilt das aufgerufene als
        ``program``; das Hauptprogramm steht dann als Oberprogramm in ``caller``."""
        main, current = snap.program, snap.current_program
        if not main or parse_program(main):
            return snap  # Auftragsprogramm direkt angewählt (oder gar keins)
        if main.upper().endswith(".P"):
            self._callers.add(main)  # Palettentabelle: immer ein Oberprogramm, nie ein eigener Lauf
        called = current if current and current != main else None
        if called and parse_program(called):
            self._callers.add(main)
            return replace(snap, program=called, caller=main)
        run = self._run
        if called and run is not None and run.program != main and parse_program(run.program):
            # Ein Auftragsprogramm lief unter diesem Hauptprogramm, es ist also ein Oberprogramm. Die
            # Steuerung meldet nur Haupt- und innerstes Programm: Ruft das Oberprogramm das Programm
            # selbst auf (z. B. eine Reinigung), ist das Auftragsprogramm fertig; sonst ist es ein
            # Unterprogramm des Auftragsprogramms, und der Lauf bleibt beim Auftragsprogramm.
            self._callers.add(main)
            if not self._called_by_caller(main, called, run.program):
                return replace(snap, program=run.program, caller=main)
        if main in self._callers:
            return replace(snap, caller=main)  # das Oberprogramm selbst, z. B. Palettenwechsel
        return snap

    def _calls_of(self, path: str) -> tuple[str, ...] | None:
        """Aufgerufene Programme laut eingelesener Datei; None = Datei (noch) nicht gelesen."""
        row = self._program_row(path)
        return row["calls"] if row else None

    def _called_by_caller(self, caller: str, called: str, order: str) -> bool:
        """Läuft ``called`` direkt aus dem Oberprogramm (dann ist das Auftragsprogramm ``order``
        fertig) oder als Unterprogramm des Auftragsprogramms?"""
        if called.upper().startswith("PLC:"):
            return False  # Makros des Maschinenherstellers laufen im Auftragsprogramm
        name = call_name(called)
        caller_calls = self._calls_of(caller)
        if caller_calls is not None and call_name(order) in caller_calls:
            # Die Datei des Oberprogramms zeigt seine Aufrufe vollständig (das Auftragsprogramm steht drin)
            return name in caller_calls
        # Oberprogramm unbekannt oder ruft über Parameter auf: Steht der Aufruf nicht im
        # Auftragsprogramm (oder ist auch das unbekannt), kommt er aus dem Oberprogramm.
        order_calls = self._calls_of(order)
        return order_calls is None or name not in order_calls

    def _reached_end(self) -> bool:
        """Die iTNC 530 meldet nach dem Programmende gleich "inaktiv" statt "beendet". Fertig ist das
        Programm, wenn es bis zuletzt im Programmlauf lief: Ein Abbruch geht über NC-Stopp ("gestoppt"),
        und ein MDI-Satz oder Handbetrieb ist kein Programmende."""
        prev = self._snapshot
        return self._prev_state is MachineState.RUNNING and prev is not None and prev.exec_mode not in MANUAL_MODES

    @staticmethod
    def _position(snap: Snapshot) -> int | None:
        """Satznummer im gezählten Programm (None, wenn gerade ein Unterprogramm läuft)."""
        return snap.line_no if (snap.current_program or snap.program) == snap.program else None

    def _start_run(self, snap: Snapshot, now: float) -> None:
        """Neuer Lauf – oder Fortsetzung des letzten, wenn das Programm mitten im Programm per
        Satzvorlauf dort wieder gestartet wird, wo der letzte Lauf endete (z. B. nach einer Störung)."""
        line = self._position(snap)
        blocks = self._blocks(snap.program)
        margin = max(MID_START_LINES, blocks * MID_START_SHARE if blocks else 0)
        mid_start = line is not None and line > margin
        ended = self._ended
        if (
            mid_start
            and ended is not None
            and ended.program == snap.program
            and now - ended.ended_at <= RESUME_MAX_GAP_S
            and ended.last_line is not None
            and abs(line - ended.last_line) <= margin
        ):
            self._db.reopen_run(ended.id)
            self._run = self._load_open_run()
            self._db.add_event(self.machine.id, now, "run_resumed", {"run": ended.id, "program": snap.program, "line": line})
            log.info("%s: Lauf %d fortgesetzt (Satzvorlauf bis Satz %d)", self.machine.name, ended.id, line)
            self._ended = None
            return
        # Ohne beobachteten Start (Neustart der Erfassung) oder mitten im Programm begonnen: kein
        # vollständiger Lauf, zählt nicht in Ø-Stückzeiten und Prognose
        start_observed = self._prev_state not in (None, MachineState.OFFLINE) and not mid_start
        code = parse_program(snap.program)
        run_id = self._db.start_run(self.machine.id, snap.program, now, start_observed, code.key if code else None)
        self._run = _Run(run_id, snap.program, now, False, start_observed, now)

    def _update_run(self, snap: Snapshot, state: MachineState, now: float) -> None:
        run = self._run
        cur = self._interval
        if run is not None and cur is not None and cur.key[0] == MachineState.RUNNING.value and cur.key[-1] == run.id:
            # Seit der letzten Abfrage lief das Programm durchgehend
            run.run_s += now - cur.last_seen

        if run is not None and run.program in self._callers:
            # Lauf eines Oberprogramms von vor seinem ersten erkannten Aufruf: verwerfen. Die Zeit
            # bleibt Laufzeit der Maschine, zählt aber wie die übrige Zeit im Oberprogramm zu keinem Lauf.
            self._db.discard_run(run.id)
            self._forecaster.invalidate(run.program)
            self._run = run = None

        if run is not None and (
            state is MachineState.READY or (state in RUN_ACTIVE_STATES and snap.program != run.program)
        ):
            # Lief der Lauf bis eben durchgehend, endet er jetzt; nach einer Unterbrechung
            # (offline / Neustart) beim letzten Lebenszeichen.
            continuous = cur is not None and cur.key[-1] == run.id
            pgm_state = snap.pgm_state
            if snap.caller is not None and state is not MachineState.READY:
                pgm_state = "FINISHED"  # Geht es im Oberprogramm weiter, hat das aufgerufene Programm sein Ende erreicht
            elif pgm_state == "IDLE" and self._reached_end():
                pgm_state = "FINISHED"
            ended_at = now if continuous else run.last_active
            self._db.end_run(run.id, ended_at, run_result(pgm_state, run.had_error))
            self._db.prune_progress(self.machine.id, run.program, PROGRESS_KEEP_RUNS)
            self._forecaster.invalidate(run.program)
            self._run_paths = None
            last = self._snapshot
            self._ended = _Ended(run.id, run.program, ended_at, self._position(last) if last else None)
            self._run = run = None

        if (
            run is None
            and state is MachineState.RUNNING
            and snap.exec_mode not in MANUAL_MODES
            and snap.program not in self._callers
        ):
            self._start_run(snap, now)
            run = self._run

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

    def _update_tool(self, snap: Snapshot | None, state: MachineState, now: float) -> None:
        """Werkzeug in der Spindel: neu anlegen, Einsatzzeit bei laufendem Programm fortschreiben."""
        parsed = parse_tool(snap.tool) if snap is not None else None
        number = parsed[0] if parsed else None
        if parsed is not None and number != self._seen_tool:
            # Die Spindelabfrage der echten Steuerung liefert keinen Namen – dann aus TOOL.T
            name = parsed[1] or self._tool_names.get(number, "")
            self._tool_name_missing = self._tool_name_missing or not name
            if self._db.ensure_tool(self.machine.id, number, name, now, DEFAULT_LIMIT_S, DEFAULT_WARN_S):
                self._db.add_event(self.machine.id, now, "tool_created", {"tool": number, "name": name})
                log.info("%s: Werkzeug T%d angelegt", self.machine.name, number)
            self._seen_tool = number
        spindle = spindle_number(snap.tool) if snap is not None else None
        if spindle is not None:
            # Aufruf: anderes Werkzeug als bei der letzten Abfrage mit Werkzeugangabe. Abfragen ohne
            # Angabe und Verbindungsabbrüche ändern das letzte bekannte Werkzeug nicht.
            if is_call(self._spindle, spindle):
                self._db.add_tool_call(self.machine.id, spindle, now, snap.program)
            self._spindle = spindle
        active = number if state is MachineState.RUNNING else None
        use = self._tool_use
        if use is not None:
            # Bis zu dieser Abfrage lief das Werkzeug; ohne Verbindung endet der Abschnitt
            # bei der letzten Abfrage (wie die Zustandsabschnitte).
            if snap is not None:
                self._db.touch_tool_usage(use[0], now)
            if use[1] == active:
                return
            self._tool_use = None
        if active is not None:
            self._tool_use = (self._db.open_tool_usage(self.machine.id, active, now), active)

    def forget_run(self, run_id: int, program: str | None) -> None:
        """Nach dem Löschen eines beendeten Laufs (Tab Aufträge): ihn nicht mehr per Satzvorlauf
        fortsetzen und die Prognose des Programms ohne ihn neu berechnen."""
        if self._ended is not None and self._ended.id == run_id:
            self._ended = None
        self._forecaster.invalidate(program)
        self._run_paths = None

    def forget_tool(self, number: int) -> None:
        """Nach dem Entfernen eines Werkzeugs: steckt es noch in der Spindel, bei der nächsten
        Abfrage neu anlegen."""
        if self._seen_tool == number:
            self._seen_tool = None

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
            row = self._db.program_file(self.machine.id, path)
            if row is not None:
                # Palettentabelle: Programmzeilen in Reihenfolge (Ablaufliste)
                row["pallet"] = pallet_entries(row["content"]) if row["content"] else None
            self._programs[path] = row
        return self._programs[path]

    def _blocks(self, path: str | None) -> int | None:
        row = self._program_row(path) if path else None
        return row["blocks"] if row else None

    def _maybe_fetch_programs(self, snap: Snapshot) -> None:
        """Angewählte Programme einmal je Lauf prüfen (bei Änderung neu einlesen). Programme, deren
        Datei noch nicht gelesen werden konnte, jede Minute erneut versuchen – ohne ihre Aufrufe
        lassen sich Oberprogramme schlechter trennen."""
        fetch = getattr(self._adapter, "fetch_program", None)
        if not self._fetch_programs or fetch is None or (self._fetch_task and not self._fetch_task.done()):
            return
        marker = self._run.id if self._run else None
        now = self._clock()
        paths = [
            p for p in dict.fromkeys((snap.program, snap.current_program))
            # PLC:\ sind Makros des Maschinenherstellers – ohne PLC-Login (den der Schreibschutz nicht
            # zulässt) nicht lesbar
            if p and not p.upper().startswith("PLC:") and (
                self._program_checked.get(p, -1) != marker
                or (
                    self._calls_of(p) is None
                    and not self._too_large(p)  # bleibt zu groß, bis sich die Datei ändert
                    and now - self._program_tried.get(p, now) >= PROGRAM_RETRY_S
                )
            )
        ]
        if paths:
            self._fetch_task = asyncio.create_task(self._fetch(fetch, paths, marker))

    def _too_large(self, path: str) -> bool:
        """Datei schon als zu groß zum Einlesen bekannt (dann nur prüfen, ob sie sich geändert hat)."""
        row = self._program_row(path)
        return row is not None and row["size"] is not None and row["size"] > self._program_max_bytes

    async def _fetch(self, fetch: Callable[..., Any], paths: list[str], marker: int | None) -> None:
        for path in paths:
            row = self._program_row(path)
            # Nach einem Fehler (z. B. Übertragung abgebrochen) ganz neu lesen, nicht nur auf Änderung
            # prüfen – außer die Datei ist zu groß: das bleibt so, bis sich Größe oder Datum ändern
            failed = row is not None and row["calls"] is None and row["error"] is not None and not self._too_large(path)
            known = (row["size"], row["mtime"]) if row and row["size"] is not None and not failed else None
            self._program_tried[path] = self._clock()
            try:
                result = await asyncio.to_thread(fetch, path, known, self._program_max_bytes)
            except Exception as exc:
                if self._program_failures.get(path) != str(exc):
                    log.warning("%s: Programm %s nicht lesbar: %s", self.machine.name, path, exc)
                self._program_failures[path] = str(exc)
                self._program_checked[path] = marker
                continue
            self._program_failures.pop(path, None)
            now = self._clock()
            if result is None:
                self._db.touch_program_file(self.machine.id, path, now)
            else:
                self._db.save_program_file(
                    self.machine.id, path, result.size, result.mtime, result.blocks, result.error, now, result.calls,
                    result.content,
                )
                parts = [result.error or (f"{result.blocks} Sätze" if result.blocks is not None else "")]
                if result.calls:
                    parts.append(f"ruft auf: {', '.join(result.calls)}")
                log.info("%s: %s eingelesen – %s", self.machine.name, path, ", ".join(p for p in parts if p))
            self._programs.pop(path, None)
            self._program_checked[path] = marker

    # --- Werkzeugtabelle (Namen) ----------------------------------------------------

    def _maybe_fetch_tool_table(self) -> None:
        fetch = getattr(self._adapter, "fetch_tool_table", None)
        if not self._fetch_programs or fetch is None or (self._fetch_task and not self._fetch_task.done()):
            return
        now = self._clock()
        last = self._tool_table_checked
        if last is None or now - last >= TOOL_TABLE_CHECK_S or (self._tool_name_missing and now - last >= TOOL_TABLE_RETRY_S):
            self._tool_table_checked = now
            self._tool_name_missing = False
            self._fetch_task = asyncio.create_task(self._fetch_tool_table(fetch))

    async def _fetch_tool_table(self, fetch: Callable[..., Any]) -> None:
        try:
            result = await asyncio.to_thread(fetch, self._tool_table)
        except Exception as exc:
            log.warning("%s: Werkzeugtabelle nicht lesbar: %s", self.machine.name, exc)
            return
        if result is None:
            return  # unverändert
        if not result.names:
            log.warning("%s: Werkzeugtabelle ohne Namen: %s", self.machine.name, result.error)
            return
        self._tool_table = (result.size, result.mtime)
        self._tool_names = dict(result.names)
        first = not self._tool_names_logged
        changed = self._db.set_tool_names(self.machine.id, self._tool_names)
        # TOOL.T ändert sich laufend (Standzeitzähler der Steuerung): nur das erste Lesen und echte
        # Namensänderungen ins Log
        if first or changed:
            log.info("%s: Werkzeugtabelle gelesen – %d Namen, %d übernommen", self.machine.name, len(result.names), changed)
        self._tool_names_logged = True

    # --- Palettenprogramm (Ablaufliste) ------------------------------------------------

    def _track_pallet(self, raw: Snapshot | None, snap: Snapshot | None, state: MachineState, now: float) -> None:
        """Welche Zeile des angewählten Palettenprogramms (.P) läuft? Die Steuerung meldet nur Haupt-
        und aktuelles Programm, nicht die Zeile der Tabelle: Jeder Wechsel auf ein Programm der Tabelle
        rückt zur nächsten Zeile mit diesem Programm vor. Je Zeile wird die reine Laufzeit bis zur
        nächsten Zeile gemessen (mit Palettenwechsel) – daraus die Restzeit der weiteren Zeilen.
        Grenze: Folgt dasselbe Auftragsprogramm direkt noch einmal (nur ein Palettenwechsel per
        PLC-Makro dazwischen), ist kein Wechsel zu sehen – im Feldtest steht immer Drehen dazwischen."""
        p = self._pallet
        if raw is None:
            if p is not None:
                p.entry_observed = False  # Lücke: diese Zeile nicht messen
            return
        table = raw.program
        if not table or not table.upper().endswith(".P"):
            self._pallet = None
            return
        if p is None or p.table != table:
            current = raw.current_program
            running = bool(current) and current != table and raw.exec_mode not in MANUAL_MODES
            p = self._pallet = _PalletRun(table, joined_running=running)
        if self._prev_state is MachineState.RUNNING and self._last_update is not None:
            p.entry_run_s += now - self._last_update  # lief seit der letzten Abfrage (wie die Laufzeit eines Laufs)
        if raw.exec_mode in MANUAL_MODES:
            return
        if not raw.current_program:
            p.stopped, p.name = True, None  # Palettenprogramm beendet oder abgebrochen
            return
        name = self._pallet_program(raw, snap)
        if name == p.name:
            return
        entries = (self._program_row(table) or {}).get("pallet")
        if name is not None and not entries:
            return  # Tabelle noch nicht gelesen (z. B. gleich nach dem Start): später zuordnen
        p.name = name
        if name is None:
            return
        if p.pos is None and p.joined_running and not p.stopped and not p.recovered:
            p.recovered = True
            self._recover_pallet(p, entries, now)
            if p.pos is not None and call_name(entries[p.pos].program) == name:
                return  # das laufende Programm ist die zuletzt gelaufene Zeile
        index = self._next_entry(entries, name, p.pos, p.stopped)
        if index is None:
            return  # kein Programm der Tabelle
        resumed, p.stopped = p.stopped, False
        if index == p.pos and resumed:
            return  # nach einer Unterbrechung an derselben Zeile weiter (z. B. Satzvorlauf)
        if p.pos is not None and p.entry_observed and index > p.pos and all(e.skipped for e in entries[p.pos + 1 : index]):
            p.durations.setdefault(call_name(entries[p.pos].program), []).append(p.entry_run_s)
        if p.pos is None or index < p.pos:
            p.started.clear()  # neuer Durchgang
        observed = self._prev_state not in (None, MachineState.OFFLINE, MachineState.NETWORK)
        p.pos, p.entry_run_s, p.entry_observed = index, 0.0, observed
        p.started[index] = (now, observed)

    def _recover_pallet(self, p: _PalletRun, entries: tuple[PalletEntry, ...], now: float) -> None:
        """Die App kam mitten in ein laufendes Palettenprogramm (Neustart, Update): Die Läufe dieses
        Durchgangs stehen in der Datenbank. Rückwärts durch die Zustandsabschnitte bis zum letzten Halt
        (Bereit länger als PALLET_PAUSE_S, Handbetrieb/MDI oder ein anderes Programm), dann die Läufe
        der Reihe nach den Zeilen zuordnen wie live."""
        names = {call_name(e.program) for e in entries}
        run_ids: set[int] = set()
        for iv in reversed(self._db.intervals(now - PALLET_LOOKBACK_S, now, self.machine.id)):
            if iv["state"] in (MachineState.OFFLINE.value, MachineState.UNKNOWN.value):
                continue  # Verbindungslücke: weiter zurück
            if iv["run_id"] is not None:
                if call_name(iv["program"] or "") not in names:
                    break
                run_ids.add(iv["run_id"])
                continue
            if iv["program"] != p.table or iv["exec_mode"] in MANUAL_MODES:
                break
            if iv["state"] == MachineState.READY.value and iv["end"] - iv["start"] > PALLET_PAUSE_S:
                break
        runs = sorted(filter(None, map(self._db.run, run_ids)), key=lambda r: r["started_at"])
        for run in runs:
            index = self._next_entry(entries, call_name(run["program"] or ""), p.pos, False)
            if index is None:
                continue
            if p.pos is None or index < p.pos:
                p.started.clear()
            p.pos = index
            p.started[index] = (run["started_at"], bool(run["start_observed"]))
        if p.pos is not None:
            log.info("%s: Palettenprogramm %s läuft schon – weiter bei Zeile %d", self.machine.name, p.table, p.pos + 1)

    @staticmethod
    def _pallet_program(raw: Snapshot, snap: Snapshot | None) -> str | None:
        """Programm der Palettentabelle, das gerade läuft (``call_name``): das gezählte Auftrags-
        programm (auch während seiner Unterprogramme) oder ein Programm, das die Tabelle selbst
        aufruft (z. B. Drehen). None = Tabelle selbst bzw. Palettenwechsel (PLC-Makro)."""
        if snap is not None and snap.program and snap.program != raw.program:
            return call_name(snap.program)
        current = raw.current_program
        if current and current != raw.program and not current.upper().startswith("PLC:"):
            return call_name(current)
        return None

    @staticmethod
    def _next_entry(entries: tuple[PalletEntry, ...], name: str, pos: int | None, resume: bool) -> int | None:
        """Zeile, die jetzt läuft: die nächste mit diesem Programm nach der aktuellen (am Ende wieder
        von vorn); nach einer Unterbrechung dieselbe Zeile oder ein Neustart von vorn. Gesperrte
        Zeilen nur, wenn das Programm sonst nirgends steht."""
        if resume and pos is not None and call_name(entries[pos].program) == name:
            return pos
        start = 0 if resume or pos is None else pos + 1
        found = [i for i in (*range(start, len(entries)), *range(start)) if call_name(entries[i].program) == name]
        return next((i for i in found if not entries[i].skipped), found[0] if found else None)

    def _entry_path(self, table: str, program: str) -> str | None:
        """Pfad, unter dem das Programm einer Tabellenzeile schon fertig gelaufen ist. Ohne Pfad in der
        Tabelle sucht die Steuerung im Verzeichnis der Tabelle; sonst zählt derselbe Name anderswo."""
        if self._run_paths is None:
            self._run_paths = {}
            for path in self._db.run_programs(self.machine.id):
                self._run_paths.setdefault(call_name(path), []).append(path)
        same_name = self._run_paths.get(call_name(program), [])
        if not same_name:
            return None
        folder = table.rsplit("\\", 1)[0]
        wanted = (program if re.search(r"[\\/:]", program) else f"{folder}\\{program}").upper()
        return next((path for path in same_name if path.upper() == wanted), same_name[0])

    def _entry_expected(self, p: _PalletRun, entry: PalletEntry) -> tuple[float | None, str | None]:
        """Erwartete reine Laufzeit einer Zeile und woher sie stammt, in dieser Reihenfolge:
        ``measured`` in diesem Palettenprogramm gemessen (mit Palettenwechsel), ``history`` übliche
        Laufzeit früherer Läufe, ``forecast`` das Programm läuft gerade zum ersten Mal – bisherige
        Laufzeit plus Restlaufzeit-Prognose."""
        name = call_name(entry.program)
        measured = p.durations.get(name)
        if measured:
            return median(measured), "measured"
        path = self._entry_path(p.table, entry.program)
        typical = self._forecaster.typical_run_s(path) if path else None
        if typical is not None:
            return typical, "history"
        run, forecast = self._run, self._forecast
        if run is not None and forecast is not None and call_name(run.program or "") == name:
            return run.run_s + forecast["remaining_s"], "forecast"
        return None, None

    def _pallet_info(self) -> dict[str, Any] | None:
        """Ablaufliste des angewählten Palettenprogramms für die Live-Seite."""
        p = self._pallet
        row = self._program_row(p.table) if p is not None else None
        entries: tuple[PalletEntry, ...] | None = row["pallet"] if row else None
        if p is None or not entries:
            return None
        pos = p.pos
        last = max((i for i, e in enumerate(entries) if not e.skipped), default=-1)
        finished = pos is not None and p.stopped and pos >= last
        items = []
        for i, entry in enumerate(entries):
            expected, source = self._entry_expected(p, entry)
            if entry.skipped:
                status = "skipped"
            elif pos is None or i > pos:
                status = "pending"
            elif i < pos or finished:
                status = "done"
            else:
                status = "current"
            started = p.started.get(i) if status in ("done", "current") else None
            items.append({
                "program": entry.program, "pallet": entry.pallet, "status": status,
                "expected_s": expected, "source": source,
                "started_at": started[0] if started else None,  # Beginn in diesem Durchgang
                "started_observed": started[1] if started else None,
            })
        active = [it for it in items if it["status"] != "skipped"]
        remaining = remaining_unknown = None
        if pos is not None and not finished:
            run, forecast = self._run, self._forecast
            remaining, remaining_unknown = 0.0, 0
            for i, it in enumerate(items):
                if it["status"] == "current":
                    if forecast is not None and run is not None and call_name(run.program or "") == call_name(it["program"]):
                        rest = forecast["remaining_s"]  # Prognose des laufenden Auftragsprogramms
                    else:
                        rest = None if it["expected_s"] is None else max(it["expected_s"] - p.entry_run_s, 0.0)
                elif it["status"] == "pending":
                    rest = it["expected_s"]
                else:
                    continue
                if rest is None:
                    remaining_unknown += 1
                else:
                    remaining += rest
        return {
            "table": p.table,
            "entries": items,
            "position": pos,
            "total_s": sum(it["expected_s"] or 0.0 for it in active),
            "unknown": sum(it["expected_s"] is None for it in active),
            "remaining_s": remaining,
            "remaining_unknown": remaining_unknown,
            "eta": self._last_update + remaining if remaining is not None and self._last_update is not None else None,
        }

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
            "caller": snap.caller if snap else None,
            "caller_file": self._caller_file(snap.caller) if snap and snap.caller else None,
            "pallet": self._pallet_info(),
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

    def _caller_file(self, path: str) -> dict[str, Any]:
        """Was über die Datei des Oberprogramms bekannt ist – zur Kontrolle auf der Live-Karte."""
        calls = self._calls_of(path)
        if calls is not None:
            return {"calls": list(calls), "error": None}
        row = self._program_row(path)
        error = (row["error"] if row else None) or self._program_failures.get(path) or "noch nicht gelesen"
        return {"calls": None, "error": error}

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
                    self._maybe_fetch_tool_table()
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
