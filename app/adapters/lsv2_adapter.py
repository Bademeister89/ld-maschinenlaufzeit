"""Adapter für Heidenhain-Steuerungen (iTNC 530 / TNC 640) über LSV2 mit pyLSV2.

Es werden ausschließlich lesende Abfragen verwendet. Jede Verbindung entsteht über
``open_lsv2`` (lsv2_guard.py): Ein Schreibschutz lässt nur Lesetelegramme zur Steuerung durch.
``safe_mode=False`` (nur Statusverbindung) ist nötig, weil pyLSV2 sonst den DNC-Login
(Option 18) verweigert – ohne ihn gibt es keinen Programmstatus.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import pyLSV2

from ..nc_program import ProgramFile, count_blocks, program_calls
from ..netcheck import reachable
from ..tool_table import TOOL_TABLE, TOOL_TABLE_MAX_BYTES, ToolTableFile, parse_tool_table
from .base import AdapterError, Snapshot
from .lsv2_guard import open_lsv2

log = logging.getLogger(__name__)


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _percent(value: float | None) -> float | None:
    return None if value is None or value < 0 else float(value)


class Lsv2Adapter:
    def __init__(self, host: str, port: int = 19000, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._con: pyLSV2.LSV2 | None = None
        self._tool_supported = True

    def connect(self) -> dict[str, str]:
        self.close()
        try:
            con = open_lsv2(self.host, self.port, self.timeout, dnc=True)
            con.connect()
        except Exception as exc:
            raise AdapterError(f"Keine LSV2-Verbindung zu {self.host}:{self.port} ({exc})") from exc

        if not con.login(pyLSV2.Login.DNC):
            self._disconnect(con)
            raise AdapterError(
                f"DNC-Login an {self.host} abgelehnt – ist Option 18 (HEIDENHAIN DNC) freigeschaltet?"
            )

        self._con = con
        self._tool_supported = True
        versions = con.versions
        return {"control": versions.control, "nc_sw": versions.nc_sw, "plc": versions.plc}

    def read(self) -> Snapshot:
        con = self._con
        if con is None:
            raise AdapterError("nicht verbunden")
        try:
            pgm_state = con.program_status()
            exec_mode = con.execution_state()
            stack = con.program_stack()
            override = con.override_state()
            tool = self._read_tool(con)
            errors = tuple(t for t in (_text(m.e_text) for m in con.get_error_messages()) if t)
        except Exception as exc:
            self.close()
            raise AdapterError(f"Abfrage an {self.host} fehlgeschlagen ({exc})") from exc

        if pgm_state is pyLSV2.PgmState.UNDEFINED and exec_mode is pyLSV2.ExecState.UNDEFINED:
            # Beide Abfragen mit Fehlertelegramm beantwortet: Sitzung als gestört behandeln.
            last_error = con.last_error
            self.close()
            raise AdapterError(f"{self.host} liefert keinen Status ({last_error})")

        return Snapshot(
            pgm_state=pgm_state.name,
            exec_mode=exec_mode.name,
            program=_text(stack.main) if stack else None,
            current_program=_text(stack.current) if stack else None,
            line_no=stack.line_no if stack and stack.line_no >= 0 else None,
            tool=tool,
            override_feed=_percent(override.feed) if override else None,
            override_spindle=_percent(override.spindle) if override else None,
            override_rapid=_percent(override.rapid) if override else None,
            errors=errors,
        )

    def close(self) -> None:
        con, self._con = self._con, None
        if con is not None:
            self._disconnect(con)

    def site_reachable(self, address: str) -> bool:
        """Antwortet die Prüfadresse am Standort (z. B. der Router vor Ort)?"""
        return reachable(address, timeout=min(self.timeout, 3.0))

    def fetch_program(self, path: str, known: tuple[int, float] | None, max_bytes: int) -> ProgramFile | None:
        """NC-Programm lesen und Sätze zählen – über eine eigene, rein lesende Verbindung
        (ohne DNC-Login), damit die laufende Statusabfrage nicht blockiert wird."""
        try:
            con = open_lsv2(self.host, self.port, max(self.timeout, 10.0), dnc=False)
            con.connect()
        except Exception as exc:
            raise AdapterError(f"Keine LSV2-Verbindung zum Lesen von {path} ({exc})") from exc
        try:
            info = con.file_info(path)
            if info is None:
                return ProgramFile(path, error="Programmdatei nicht gefunden")
            size, mtime = int(info.size), info.timestamp.timestamp()
            if known is not None and known == (size, mtime):
                return None
            if size > max_bytes:
                return ProgramFile(path, size, mtime, error=f"Programm zu groß zum Einlesen ({size / 1e6:.1f} MB)")
            with tempfile.TemporaryDirectory() as tmp:
                local = Path(tmp) / "programm.txt"
                if not con.recive_file(path, local, override_file=True, binary_mode=False):
                    return ProgramFile(path, size, mtime, error=f"Übertragung fehlgeschlagen ({con.last_error})")
                # Heidenhain-Steuerungen speichern Programme in ISO-8859-1
                text = local.read_text(encoding="latin-1")
            blocks = count_blocks(path, text)
            error = None if blocks is not None else "Satzanzahl nicht erkennbar"
            return ProgramFile(path, size, mtime, blocks, error, program_calls(text))
        except Exception as exc:
            raise AdapterError(f"Lesen von {path} fehlgeschlagen ({exc})") from exc
        finally:
            self._disconnect(con)

    def fetch_tool_table(self, known: tuple[int, float] | None) -> ToolTableFile | None:
        """Werkzeugnamen aus TOOL.T lesen (eigene, rein lesende Verbindung wie bei den Programmen).
        None = seit dem letzten Lesen unverändert."""
        try:
            con = open_lsv2(self.host, self.port, max(self.timeout, 10.0), dnc=False)
            con.connect()
        except Exception as exc:
            raise AdapterError(f"Keine LSV2-Verbindung zum Lesen von {TOOL_TABLE} ({exc})") from exc
        try:
            info = con.file_info(TOOL_TABLE)
            if info is None:
                return ToolTableFile(error=f"{TOOL_TABLE} nicht gefunden")
            size, mtime = int(info.size), info.timestamp.timestamp()
            if known is not None and known == (size, mtime):
                return None
            if size > TOOL_TABLE_MAX_BYTES:
                return ToolTableFile(size, mtime, error=f"Werkzeugtabelle zu groß ({size / 1e6:.1f} MB)")
            with tempfile.TemporaryDirectory() as tmp:
                local = Path(tmp) / "tool.t"
                if not con.recive_file(TOOL_TABLE, local, override_file=True, binary_mode=False):
                    return ToolTableFile(size, mtime, error=f"Übertragung fehlgeschlagen ({con.last_error})")
                text = local.read_text(encoding="latin-1")
            names = parse_tool_table(text)
            return ToolTableFile(size, mtime, names, None if names else "keine Werkzeugnamen gefunden")
        except Exception as exc:
            raise AdapterError(f"Lesen von {TOOL_TABLE} fehlgeschlagen ({exc})") from exc
        finally:
            self._disconnect(con)

    def _read_tool(self, con: pyLSV2.LSV2) -> str | None:
        # Nicht jede Steuerung beantwortet die Werkzeugabfrage; nach dem ersten Fehlschlag
        # bis zum nächsten Verbindungsaufbau nicht mehr fragen.
        if not self._tool_supported:
            return None
        info = con.spindle_tool_status()
        if info is None:
            self._tool_supported = False
            log.info("%s: Werkzeugabfrage nicht unterstützt", self.host)
            return None
        if info.number < 0:
            return None
        name = _text(info.name)
        return f"T{info.number}" + (f" {name}" if name else "")

    @staticmethod
    def _disconnect(con: pyLSV2.LSV2) -> None:
        try:
            con.disconnect()
        except Exception:
            pass
