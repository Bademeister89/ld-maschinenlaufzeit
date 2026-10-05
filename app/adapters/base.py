"""Gemeinsame Schnittstelle für Maschinen-Adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class AdapterError(Exception):
    """Verbindung oder Abfrage fehlgeschlagen – der Collector wertet die Maschine dann als offline."""


@dataclass(frozen=True)
class Snapshot:
    """Momentaufnahme einer Steuerung. Zustandsnamen entsprechen pyLSV2 (PgmState / ExecState)."""

    pgm_state: str
    exec_mode: str
    program: str | None = None
    current_program: str | None = None
    line_no: int | None = None
    tool: str | None = None
    override_feed: float | None = None
    override_spindle: float | None = None
    override_rapid: float | None = None
    errors: tuple[str, ...] = ()
    # Oberprogramm (z. B. Palettenprogramm), das ``program`` per CALL PGM aufgerufen hat.
    # Setzt der Collector; der Adapter liefert in ``program`` immer das angewählte Hauptprogramm.
    caller: str | None = None


class MachineAdapter(Protocol):
    """Blockierende Schnittstelle; der Collector ruft sie in einem Worker-Thread auf.

    Optional: ``fetch_program(path, known, max_bytes) -> ProgramFile | None`` liest ein
    NC-Programm und ermittelt die Satzanzahl. ``known`` = (Größe, Änderungszeit) aus dem
    Cache; ist die Datei unverändert, liefert die Methode ``None``.

    Optional: ``site_reachable(address) -> bool`` prüft die Prüfadresse am Standort, um einen
    Verbindungsausfall (VPN) von ausgeschalteten Maschinen zu unterscheiden.
    """

    def connect(self) -> dict[str, str]:
        """Verbindung aufbauen, liefert Steuerungsinfos (Typ, NC-Software)."""
        ...

    def read(self) -> Snapshot: ...

    def close(self) -> None: ...
