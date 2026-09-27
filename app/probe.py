"""Verbindungstest für eine Heidenhain-Steuerung – rein lesend.

Schritte: TCP-Port erreichbar → LSV2-Anmeldung und Steuerungsversion → DNC-Login (Option 18)
→ eine Statusabfrage über denselben Adapter, den auch die Erfassung verwendet.
Genutzt von tools/probe.py und vom Button "Verbindung testen" im Konfigurations-Tab.
"""

from __future__ import annotations

import socket
from dataclasses import asdict, dataclass, field
from typing import Any

import pyLSV2

from .adapters.base import AdapterError
from .adapters.lsv2_adapter import Lsv2Adapter
from .netcheck import reachable
from .state import EXEC_MODE_LABELS, PGM_STATE_LABELS, STATE_LABELS, classify


@dataclass
class Step:
    title: str
    ok: bool
    detail: str = ""
    hints: list[str] = field(default_factory=list)
    required: bool = True  # optionale Schritte (Programm lesen) entscheiden nicht über "ok"


@dataclass
class ProbeResult:
    host: str
    port: int
    steps: list[Step] = field(default_factory=list)
    control: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        required = [s for s in self.steps if s.required]
        return bool(required) and all(s.ok for s in required)

    def as_dict(self) -> dict[str, Any]:
        return {"host": self.host, "port": self.port, "ok": self.ok, "control": self.control,
                "steps": [asdict(s) for s in self.steps]}


def run_probe(host: str, port: int = 19000, timeout: float = 5.0, check_host: str = "") -> ProbeResult:
    result = ProbeResult(host, port)
    steps = result.steps

    site_ok = None
    if check_host:
        site_ok = reachable(check_host, timeout=min(timeout, 3.0))
        steps.append(
            Step(
                "Prüfadresse Standort",
                site_ok,
                f"{check_host} {'antwortet' if site_ok else 'antwortet nicht'}",
                [] if site_ok else ["Verbindung zum Standort gestört (VPN aus?) oder Prüfadresse falsch."],
                required=False,
            )
        )

    try:
        with socket.create_connection((host, port), timeout=timeout):
            pass
        steps.append(Step("Netzwerk", True, f"Port {port} erreichbar"))
    except OSError as exc:
        steps.append(
            Step(
                "Netzwerk",
                False,
                f"{host}:{port} nicht erreichbar ({exc})",
                (["Auch die Prüfadresse antwortet nicht: zuerst die Verbindung zum Standort (VPN) prüfen."]
                 if site_ok is False else [])
                + [
                    "Maschine eingeschaltet und Steuerung hochgefahren?",
                    "IP-Adresse richtig? (an der Steuerung: MOD → Netzwerk)",
                    "Ist das Maschinennetz von diesem PC aus erreichbar (VPN, Routing, ping)?",
                    "Erlaubt die Firewall der Steuerung LSV2 für diesen PC? (MOD → Firewall)",
                ],
            )
        )
        return result

    con = pyLSV2.LSV2(host, port=port, timeout=timeout, safe_mode=False)
    try:
        con.connect()
    except Exception as exc:
        steps.append(
            Step(
                "LSV2-Anmeldung",
                False,
                f"{type(exc).__name__}: {exc}",
                ["Neuere Steuerungen (TNC 640 ab 34059x-10, TNC7) verlangen LSV2 über SSH."],
            )
        )
        return result
    try:
        v = con.versions
        result.control = {"control": v.control, "nc_sw": v.nc_sw, "plc": v.plc}
        steps.append(Step("LSV2-Anmeldung", True, f"{v.control} · NC-Software {v.nc_sw}"))
        if con.login(pyLSV2.Login.DNC):
            steps.append(Step("Option 18 (DNC)", True, "DNC-Login erfolgreich – Programmstatus lesbar"))
        else:
            steps.append(
                Step(
                    "Option 18 (DNC)",
                    False,
                    f"DNC-Login abgelehnt ({con.last_error})",
                    [
                        "Option 18 (HEIDENHAIN DNC) ist nicht freigeschaltet – ohne sie gibt es keinen Programmstatus.",
                        "Freischaltung mit Heidenhain bzw. DMG klären (SIK-Optionen).",
                    ],
                )
            )
            return result
    finally:
        try:
            con.disconnect()
        except Exception:
            pass

    adapter = Lsv2Adapter(host, port, timeout)
    try:
        adapter.connect()
        snap = adapter.read()
    except AdapterError as exc:
        steps.append(Step("Statusabfrage", False, str(exc)))
        return result
    finally:
        adapter.close()
    parts = [
        STATE_LABELS[classify(snap.pgm_state)],
        f"Programmstatus {PGM_STATE_LABELS.get(snap.pgm_state, snap.pgm_state)}",
        EXEC_MODE_LABELS.get(snap.exec_mode, snap.exec_mode),
        snap.program or "kein Programm angewählt",
    ]
    if snap.tool:
        parts.append(snap.tool)
    steps.append(Step("Statusabfrage", True, " · ".join(parts)))
    if snap.program:
        steps.append(_program_step(adapter, snap.current_program or snap.program, snap.line_no))
    return result


def _program_step(adapter: Lsv2Adapter, path: str, line_no: int | None) -> Step:
    """Angewähltes Programm lesen: Satzanzahl für Fortschritt und Restlaufzeit."""
    try:
        info = adapter.fetch_program(path, None, 20_000_000)
    except AdapterError as exc:
        return Step("Programm lesen", False, str(exc), required=False)
    if info is None or info.blocks is None:
        return Step("Programm lesen", False, f"{path}: {info.error if info else 'unbekannt'}", required=False)
    current = f", aktuell Satz {line_no}" if line_no is not None else ""
    return Step("Programm lesen", True, f"{path}: {info.blocks} Sätze{current}", required=False)
