"""Verbindungstest für eine Heidenhain-Steuerung – rein lesend.

Schritte: TCP-Port erreichbar → LSV2-Anmeldung und Steuerungsversion → DNC-Login (Option 18)
→ eine Statusabfrage über denselben Adapter, den auch die Erfassung verwendet
→ (optional) angewähltes Programm und Werkzeugtabelle lesen.
Genutzt von tools/probe.py und vom Button "Verbindung testen" im Konfigurations-Tab.
"""

from __future__ import annotations

import socket
from dataclasses import asdict, dataclass, field
from typing import Any

import pyLSV2

from .adapters.base import AdapterError
from .adapters.lsv2_adapter import Lsv2Adapter
from .adapters.lsv2_guard import open_lsv2
from .netcheck import reachable
from .state import EXEC_MODE_LABELS, PGM_STATE_LABELS, STATE_LABELS, classify
from .tools import parse_tool


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

    try:
        con = open_lsv2(host, port, timeout, dnc=True)  # mit Schreibschutz wie die Erfassung
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
    # Grundlage der Werkzeugauswertung – deshalb auch das Fehlen ausdrücklich nennen
    parts.append(f"Werkzeug {snap.tool}" if snap.tool else "Werkzeug: keine Angabe (Abfrage nicht unterstützt oder T0)")
    steps.append(Step("Statusabfrage", True, " · ".join(parts)))
    if snap.program:
        steps.append(_program_step(adapter, snap.current_program or snap.program, snap.line_no))
        if snap.current_program and snap.current_program != snap.program:
            # Hauptprogramm ruft gerade ein anderes auf (z. B. Palettenprogramm): seine Aufrufe zeigen
            steps.append(_program_step(adapter, snap.program, None, "Hauptprogramm lesen"))
    steps.append(_tool_table_step(adapter, snap.tool))
    return result


def _program_step(adapter: Lsv2Adapter, path: str, line_no: int | None, title: str = "Programm lesen") -> Step:
    """Angewähltes Programm lesen: Satzanzahl für Fortschritt und Restlaufzeit, aufgerufene Programme
    für Oberprogramme (z. B. Palettenprogramm)."""
    try:
        info = adapter.fetch_program(path, None, 20_000_000)
    except AdapterError as exc:
        return Step(title, False, str(exc), required=False)
    if info is None or info.blocks is None:
        return Step(title, False, f"{path}: {info.error if info else 'unbekannt'}", required=False)
    current = f", aktuell Satz {line_no}" if line_no is not None else ""
    calls = f", ruft auf: {', '.join(info.calls)}" if info.calls else ""
    return Step(title, True, f"{path}: {info.blocks} Sätze{current}{calls}", required=False)


def _tool_table_step(adapter: Lsv2Adapter, tool: str | None) -> Step:
    """Werkzeugtabelle lesen: Namen für die Werkzeugauswertung."""
    title = "Werkzeugtabelle lesen"
    try:
        info = adapter.fetch_tool_table(None)
    except AdapterError as exc:
        return Step(title, False, str(exc), required=False)
    if info is None or not info.names:
        return Step(title, False, info.error if info else "unbekannt", required=False)
    parsed = parse_tool(tool)
    if parsed and parsed[0] in info.names:
        example = f"in der Spindel: T{parsed[0]} {info.names[parsed[0]]}"
    else:
        number = min(info.names)
        example = f"z. B. T{number} {info.names[number]}"
    return Step(title, True, f"{len(info.names)} Werkzeuge mit Namen, {example}", required=False)
