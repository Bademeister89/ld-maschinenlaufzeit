"""Schreibschutz für alle LSV2-Verbindungen: Nur Lesebefehle verlassen den Rechner.

Die App ist ein reines Lesewerkzeug. pyLSV2 kennt zwar einen ``safe_mode``, der aber nur
Anmeldungen und Systembefehle begrenzt – nicht das Senden, Löschen oder Umbenennen von Dateien,
Maschinenparameter oder Tastendrücke. Außerdem muss er für die Statusabfrage ausgeschaltet sein,
weil er den DNC-Login (Option 18) verweigert.

Deshalb prüft dieser Schutz jedes einzelne Telegramm vor dem Senden gegen eine Positivliste.
Alles andere wird mit ``WriteBlocked`` abgebrochen, bevor es die Steuerung erreicht. Lässt sich
der Schutz nicht einrichten (andere pyLSV2-Version), kommt keine Verbindung zustande.
"""

from __future__ import annotations

import logging
import struct
from typing import Any

import pyLSV2
from pyLSV2.const import CMD, RSP, Login, ParCCC

from .base import AdapterError

log = logging.getLogger(__name__)

# Telegramme, die nur lesen (plus An-/Abmelden und die Quittung beim Empfang einer Datei)
READ_TELEGRAMS = frozenset(
    {
        CMD.A_LG,  # Anmelden (nur die Stufen unten)
        CMD.A_LO,  # Abmelden
        CMD.R_VR,  # Steuerungstyp und Softwarestand
        CMD.R_PR,  # Systemparameter der Schnittstelle (Puffergröße …)
        CMD.R_CI,  # Systeminformationen (Turbo-Modus, DNC erlaubt …)
        CMD.R_RI,  # Programmstatus, Betriebsart, Programm/Satz, Override, Fehler, Werkzeug
        CMD.R_FI,  # Dateiinfo (Größe, Änderungszeit)
        CMD.R_FL,  # Datei von der Steuerung lesen (NC-Programm, TOOL.T)
        RSP.T_OK,  # Quittung „nächster Block“ beim Lesen einer Datei
    }
)
# Anmeldestufen: INSPECT und FILE baut pyLSV2 beim Verbinden auf, DNC braucht die Statusabfrage.
# FILE erlaubt der Steuerung auch Schreibzugriffe – die Schreibtelegramme blockiert dieser Schutz.
ALLOWED_LOGINS = frozenset({Login.INSPECT.value, Login.FILETRANSFER.value, Login.DNC.value})
# Systembefehle, die nur die Übertragung dieser Verbindung einstellen (keine Daten der Steuerung)
ALLOWED_SYSTEM_COMMANDS = frozenset(
    {
        ParCCC.SET_BUF512,
        ParCCC.SET_BUF1024,
        ParCCC.SET_BUF2048,
        ParCCC.SET_BUF3072,
        ParCCC.SET_BUF4096,
        ParCCC.SECURE_FILE_SEND,
    }
)


class WriteBlocked(AdapterError):
    """Ein Telegramm hätte die Steuerung verändern können und wurde nicht gesendet."""


def check_telegram(command: Any, payload: bytes | bytearray | None) -> None:
    """Lässt nur Lesetelegramme durch; alles andere löst ``WriteBlocked`` aus."""
    data = bytes(payload or b"")
    if command == CMD.C_CC:
        code = struct.unpack("!H", data[:2])[0] if len(data) >= 2 else None
        if code not in ALLOWED_SYSTEM_COMMANDS:
            raise WriteBlocked(f"Systembefehl {code} blockiert – die App ist ein reines Lesewerkzeug")
        return
    if command == CMD.A_LG:
        login = data.split(b"\x00", 1)[0].decode("ascii", errors="replace")
        if login not in ALLOWED_LOGINS:
            raise WriteBlocked(f"Anmeldung „{login}“ blockiert – die App ist ein reines Lesewerkzeug")
        return
    if command not in READ_TELEGRAMS:
        name = getattr(command, "value", command)
        raise WriteBlocked(f"Telegramm {name} blockiert – die App ist ein reines Lesewerkzeug")


def read_only(con: pyLSV2.LSV2) -> pyLSV2.LSV2:
    """Schreibschutz in eine (noch nicht verbundene) pyLSV2-Verbindung einbauen."""
    llcom = getattr(con, "_llcom", None)
    send = getattr(llcom, "telegram", None)
    if not callable(send):
        raise AdapterError("Schreibschutz lässt sich nicht einrichten (unbekannte pyLSV2-Version) – keine Verbindung")

    def guarded(*args: Any, **kwargs: Any) -> Any:
        command = args[0] if args else kwargs.get("command")
        payload = args[1] if len(args) > 1 else kwargs.get("payload")
        try:
            check_telegram(command, payload)
        except WriteBlocked as exc:
            log.error("%s", exc)
            raise
        return send(*args, **kwargs)

    llcom.telegram = guarded
    return con


def open_lsv2(host: str, port: int, timeout: float, dnc: bool) -> pyLSV2.LSV2:
    """Neue, schreibgeschützte LSV2-Verbindung (noch nicht verbunden).

    ``dnc=True`` für die Statusabfrage: pyLSV2 braucht dafür ``safe_mode=False``; der
    Schreibschutz oben gilt trotzdem. Dateilesen läuft mit ``safe_mode=True``.
    """
    return read_only(pyLSV2.LSV2(host, port=port, timeout=timeout, safe_mode=not dnc))
