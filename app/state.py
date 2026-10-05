"""Abbildung der Heidenhain-Zustände (pyLSV2 PgmState / ExecState) auf Maschinenzustände.

Das Mapping steht bewusst nur hier. Falls die echte iTNC 530 im Probe-Test andere
Werte liefert, wird nur diese Datei angepasst.
"""

from __future__ import annotations

from enum import Enum


class MachineState(str, Enum):
    RUNNING = "RUNNING"
    STOPPED = "STOPPED"
    ERROR = "ERROR"
    READY = "READY"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"
    # Nur live: weder Steuerung noch Prüfadresse am Standort erreichbar (z. B. VPN weg).
    # Wird nicht als Intervall gespeichert; die Lücke erscheint in der Auswertung als "Keine Daten".
    NETWORK = "NETWORK"


STATE_LABELS = {
    MachineState.RUNNING: "Läuft",
    MachineState.STOPPED: "Gestoppt",
    MachineState.ERROR: "Fehler",
    MachineState.READY: "Bereit",
    MachineState.OFFLINE: "Offline",
    MachineState.UNKNOWN: "Unbekannt",
    MachineState.NETWORK: "Standort nicht erreichbar",
}

# Zustände, die als Zustandsintervall gespeichert werden
STORED_STATES = tuple(s for s in MachineState if s is not MachineState.NETWORK)

_PGM_STATE_MAP = {
    "STARTED": MachineState.RUNNING,
    "STOPPED": MachineState.STOPPED,
    "INTERRUPTED": MachineState.STOPPED,
    "ERROR": MachineState.ERROR,
    "FINISHED": MachineState.READY,
    "CANCELLED": MachineState.READY,
    "ERROR_CLEARED": MachineState.READY,
    "IDLE": MachineState.READY,
}

PGM_STATE_LABELS = {
    "STARTED": "gestartet",
    "STOPPED": "gestoppt",
    "FINISHED": "beendet",
    "CANCELLED": "abgebrochen",
    "INTERRUPTED": "unterbrochen",
    "ERROR": "Fehler",
    "ERROR_CLEARED": "Fehler quittiert",
    "IDLE": "inaktiv",
    "UNDEFINED": "unbekannt",
}

EXEC_MODE_LABELS = {
    "MANUAL": "Manueller Betrieb",
    "MDI": "Positionieren mit Handeingabe",
    "PASS_REFERENCES": "Referenzpunkte anfahren",
    "SINGLE_STEP": "Programmlauf Einzelsatz",
    "AUTOMATIC": "Programmlauf Satzfolge",
    "UNDEFINED": "unbekannt",
}

# Solange einer dieser Zustände anliegt, läuft ein begonnener Programmdurchlauf weiter
# (NC-Stopp und Fehler zählen als Standzeit innerhalb des Laufs).
RUN_ACTIVE_STATES = frozenset({MachineState.RUNNING, MachineState.STOPPED, MachineState.ERROR})

# Betriebsarten ohne Programmlauf: Auch hier meldet die Steuerung "gestartet" (z. B. ein MDI-Satz oder
# ein Makro des Maschinenherstellers im Handbetrieb), daraus entsteht aber kein Programmdurchlauf.
MANUAL_MODES = frozenset({"MANUAL", "MDI", "PASS_REFERENCES"})

RUN_RESULT_LABELS = {
    "finished": "fertig",
    "cancelled": "abgebrochen",
    "error": "Fehler",
    "aborted": "unterbrochen",
}


def classify(pgm_state: str | None) -> MachineState:
    return _PGM_STATE_MAP.get(pgm_state or "", MachineState.UNKNOWN)


def run_result(pgm_state: str | None, had_error: bool) -> str:
    """Ergebnis eines Programmdurchlaufs anhand des Programmstatus, der ihn beendet hat."""
    if pgm_state == "FINISHED":
        return "finished"
    if had_error or pgm_state == "ERROR_CLEARED":
        return "error"
    if pgm_state == "CANCELLED":
        return "cancelled"
    return "aborted"
