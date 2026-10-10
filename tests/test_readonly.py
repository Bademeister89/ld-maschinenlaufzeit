"""Schreibschutz: Nur Lesebefehle erreichen die Steuerung.

Die Steuerung wird auf Telegrammebene nachgebaut (``FakeControl`` ersetzt die TCP-Schicht von
pyLSV2). So laufen die echten pyLSV2-Funktionen samt Schreibschutz, und der Test sieht genau,
welche Telegramme bei der „Steuerung“ ankommen.
"""

import ast
import struct
from pathlib import Path

import pyLSV2
import pytest
from pyLSV2 import client as pylsv2_client
from pyLSV2.const import CMD, MODE_BINARY, RSP, KeyCode, Login, LSV2StatusCode, ParCCC, ParRRI
from pyLSV2.dat_cls import LSV2Error

from app.adapters.base import AdapterError
from app.adapters.lsv2_adapter import Lsv2Adapter
from app.adapters.lsv2_guard import (
    ALLOWED_LOGINS,
    ALLOWED_SYSTEM_COMMANDS,
    READ_TELEGRAMS,
    WriteBlocked,
    check_telegram,
    open_lsv2,
    read_only,
)
from app.tool_table import TOOL_TABLE

ROOT = Path(__file__).resolve().parent.parent
PROGRAM = "TNC:\\AUFTRAG\\26-21055-01-01.H"
FILES = {
    PROGRAM: "0 BEGIN PGM 26-21055-01-01 MM\n1 TOOL CALL 12 Z S8000\n2 L X+0 Y+0 R0 FMAX\n3 END PGM 26-21055-01-01 MM\n",
    TOOL_TABLE: (
        "BEGIN TOOL     .T     MM\n"
        "T    NAME             L           R\n"
        "0    NULLWERKZEUG     +0          +0\n"
        "12   FRAESER_D16      +80.5       +8\n"
        "[END]\n"
    ),
}
WRITE_TELEGRAMS = {CMD.C_FL, CMD.C_FD, CMD.C_FC, CMD.C_FR, CMD.C_DM, CMD.C_DD, CMD.C_DC, CMD.C_MC, CMD.C_EK, CMD.C_LK}


class FakeControl:
    """Nachgebaute iTNC 530 auf Telegrammebene; merkt sich jedes angekommene Telegramm."""

    instances: list["FakeControl"] = []
    pgm_code = 0  # Programmstatus: 0 = gestartet, 7 = inaktiv

    def __init__(self, hostname, port=19000, timeout=15.0):
        self.received: list[tuple[object, bytes]] = []
        self.buffer_size = 256
        self._response = RSP.NONE
        self._error = LSV2Error()
        self._sending_file = False
        FakeControl.instances.append(self)

    @property
    def last_response(self):
        return self._response

    @property
    def last_error(self):
        return self._error

    def connect(self):
        pass

    def disconnect(self):
        pass

    def telegram(self, command, payload=bytearray(), wait_for_response=True):
        data = bytes(payload or b"")
        self.received.append((command, data))
        return self._answer(command, data)

    def _ok(self, response, content=b""):
        self._response = response
        return bytearray(content)

    def _fail(self, code):
        self._response = RSP.T_ER
        self._error = LSV2Error()
        self._error.e_code = code
        return bytearray()

    def _answer(self, command, data):
        if command in (CMD.A_LG, CMD.A_LO, CMD.C_CC):
            return self._ok(RSP.T_OK)
        if command == CMD.R_VR:
            return self._ok(RSP.S_VR, b"iTNC530\x00340494 08 SP3\x00PLC\x0000000000\x00") if not data else self._fail(
                LSV2StatusCode.T_ER_WRONG_PARA
            )
        if command == CMD.R_PR:
            values = [0] * 42
            values[30], values[32] = 1, 4096  # LSV2-Version, maximale Blocklänge
            return self._ok(RSP.S_PR, struct.pack("!14L8B8L2BH4B2L2HL", *values))
        if command == CMD.R_CI:
            return self._ok(RSP.S_CI, struct.pack("!L", 1) + b"\x00\x00\x00\x00")  # Typ „Wahrheitswert“: nein
        if command == CMD.R_RI:
            return self._status(struct.unpack("!H", data[:2])[0])
        if command == CMD.R_FI:
            name = data.split(b"\x00")[0].decode("latin-1")
            if name not in FILES:
                return self._fail(LSV2StatusCode.T_ER_NO_FILE)
            size = len(FILES[name].encode("latin-1"))
            return self._ok(RSP.S_FI, struct.pack("!LLL", size, 1_700_000_000, 0) + name.encode("latin-1") + b"\x00")
        if command == CMD.R_FL:
            name, _, mode = data.partition(b"\x00")
            content = FILES[name.decode("latin-1")].encode("latin-1")
            self._sending_file = True
            return self._ok(RSP.S_FL, content if mode[:1] == bytes([MODE_BINARY]) else content.replace(b"\n", b"\x00"))
        if command == RSP.T_OK and self._sending_file:
            self._sending_file = False
            return self._ok(RSP.T_FD)
        raise AssertionError(f"Die nachgebaute Steuerung kennt das Telegramm {command} nicht")

    def _status(self, code):
        if code == ParRRI.PGM_STATE:
            return self._ok(RSP.S_RI, struct.pack("!H", self.pgm_code))
        if code == ParRRI.EXEC_STATE:
            return self._ok(RSP.S_RI, struct.pack("!H", 4))  # Automatik
        if code == ParRRI.SELECTED_PGM:
            path = PROGRAM.encode("latin-1") + b"\x00"
            return self._ok(RSP.S_RI, struct.pack("!L", 2) + path + path)
        if code == ParRRI.OVERRIDE:
            return self._ok(RSP.S_RI, struct.pack("!LLL", 10000, 10000, 5000))
        if code == ParRRI.CURRENT_TOOL:
            return self._ok(RSP.S_RI, struct.pack("!LHH", 12, 0, 2) + struct.pack("<dd", 80.5, 8.0))
        if code == ParRRI.FIRST_ERROR:
            return self._fail(LSV2StatusCode.T_ER_NO_NEXT_ERROR)  # keine Fehlermeldung aktiv
        raise AssertionError(f"Unerwartete Statusabfrage {code}")


@pytest.fixture
def control(monkeypatch):
    FakeControl.instances = []
    monkeypatch.setattr(pylsv2_client, "LSV2TCP", FakeControl)
    return FakeControl


def received(control):
    return [cmd for inst in control.instances for cmd, _ in inst.received]


def assert_only_reads(control):
    for inst in control.instances:
        for cmd, data in inst.received:
            check_telegram(cmd, data)  # löst bei allem außer Lesebefehlen aus
    assert not WRITE_TELEGRAMS & set(received(control))


# --- Normalbetrieb läuft durch den Schutz ----------------------------------------------------------


def status_reads(control):
    """Abgefragte Statuswerte (R_RI) seit dem letzten Aufruf, ohne Wiederholung."""
    codes = {ParRRI.PGM_STATE: "status", ParRRI.EXEC_STATE: "mode", ParRRI.SELECTED_PGM: "program",
             ParRRI.OVERRIDE: "override", ParRRI.CURRENT_TOOL: "tool", ParRRI.FIRST_ERROR: "errors"}
    inst = control.instances[0]
    names = [codes[struct.unpack("!H", data[:2])[0]] for cmd, data in inst.received if cmd == CMD.R_RI]
    inst.received.clear()
    return names


def test_rare_values_are_read_only_when_needed(control):
    """Weniger Last für die Steuerung: Status, Betriebsart und Programm bei jeder Abfrage, Werkzeug bei
    laufendem Programm auch – Override alle 6 s, Fehlermeldungen alle 10 s, im Leerlauf noch seltener.
    Ändert sich der Status, wird sofort alles gelesen; dazwischen gilt der letzte Wert."""
    now = [0.0]
    adapter = Lsv2Adapter("10.0.0.1", clock=lambda: now[0])
    adapter.connect()
    status_reads(control)
    everything = ["status", "mode", "program", "tool", "override", "errors"]
    assert status_reads(control) == [] and adapter.read().tool == "T12"
    assert status_reads(control) == everything
    reads = []
    for t in (2.2, 4.4, 6.6, 8.8, 11.0):
        now[0] = t
        snap = adapter.read()
        reads.append(status_reads(control)[3:])
    assert reads == [["tool"], ["tool"], ["tool", "override"], ["tool"], ["tool", "errors"]]
    assert (snap.tool, snap.override_feed, snap.errors) == ("T12", 100.0, ())  # letzte Werte bleiben

    control.instances[0].pgm_code = 7  # Programm zu Ende: alles sofort lesen …
    now[0] = 13.2
    adapter.read()
    assert status_reads(control) == everything
    now[0] = 15.4
    assert adapter.read().tool == "T12"
    assert status_reads(control) == ["status", "mode", "program"]  # … danach im Leerlauf nur das Nötigste
    adapter.close()
    assert_only_reads(control)


def test_normal_operation_passes_guard(control):
    adapter = Lsv2Adapter("10.0.0.1")
    assert adapter.connect()["control"] == "iTNC530"
    snap = adapter.read()
    assert (snap.pgm_state, snap.exec_mode, snap.program, snap.line_no) == ("STARTED", "AUTOMATIC", PROGRAM, 2)
    assert (snap.tool, snap.override_feed, snap.errors) == ("T12", 100.0, ())
    assert adapter.fetch_program(PROGRAM, None, 10**6).blocks == 3
    assert adapter.fetch_tool_table(None).names == {12: "FRAESER_D16"}
    adapter.close()
    assert len(control.instances) == 3  # Status, Programm, Werkzeugtabelle
    assert_only_reads(control)
    assert CMD.R_FL in received(control)  # Dateien wurden wirklich gelesen


# --- Schreibversuche kommen nie an ------------------------------------------------------------------


WRITE_ATTEMPTS = {
    "Datei löschen": lambda con, local: con.delete_file(PROGRAM),
    "Datei senden": lambda con, local: con.send_file(local, "TNC:\\X.H", override_file=True),
    "Datei kopieren": lambda con, local: con.copy_remote_file(PROGRAM, "TNC:\\KOPIE.H"),
    "Datei umbenennen": lambda con, local: con.move_file(PROGRAM, "TNC:\\NEU.H"),
    "Ordner anlegen": lambda con, local: con.make_directory("TNC:\\NEU"),
    "Ordner löschen": lambda con, local: con.delete_empty_directory("TNC:\\AUFTRAG"),
    "Verzeichnis wechseln": lambda con, local: con.change_directory("TNC:\\AUFTRAG"),
    "Maschinenparameter": lambda con, local: con.set_machine_parameter("CfgDisplayLanguage.ncLanguage", "1"),
    "Tastendruck": lambda con, local: con.send_key_code(KeyCode.CE),
    "Tastatur sperren": lambda con, local: con.set_keyboard_access(False),
    "Steuerung zurücksetzen": lambda con, local: con._send_recive(CMD.C_CC, struct.pack("!H", ParCCC.RESET_TNC), RSP.T_OK),
    "PLC-Anmeldung": lambda con, local: con.login(Login.PLCDEBUG),
}


@pytest.mark.parametrize("dnc", [True, False], ids=["Statusverbindung", "Dateiverbindung"])
@pytest.mark.parametrize("attempt", list(WRITE_ATTEMPTS))
def test_write_attempts_are_blocked(control, tmp_path, attempt, dnc):
    local = tmp_path / "x.h"
    local.write_text("0 BEGIN PGM X MM\n1 END PGM X MM\n")
    con = open_lsv2("10.0.0.1", 19000, 5.0, dnc=dnc)
    con.connect()
    if dnc:
        assert con.login(Login.DNC)
    before = len(received(control))
    try:
        WRITE_ATTEMPTS[attempt](con, local)
    except WriteBlocked:
        pass  # blockiert, bevor etwas gesendet wurde
    else:
        # pyLSV2 selbst hat abgelehnt (safe_mode) – dann darf erst recht nichts angekommen sein
        assert not dnc, f"{attempt}: weder blockiert noch abgelehnt"
    assert_only_reads(control)
    assert all(cmd in READ_TELEGRAMS or cmd == CMD.C_CC for cmd in received(control)[before:])


# --- Positivliste und Einrichtung ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        (CMD.R_RI, struct.pack("!H", ParRRI.PGM_STATE)),
        (CMD.R_FL, b"TNC:\\TOOL.T\x00\x00"),
        (RSP.T_OK, b""),
        (CMD.A_LG, b"DNC\x00"),
        (CMD.C_CC, struct.pack("!H", ParCCC.SET_BUF4096)),
    ],
)
def test_reads_are_allowed(command, payload):
    check_telegram(command, payload)


@pytest.mark.parametrize(
    ("command", "payload"),
    [
        (CMD.C_FD, b"TNC:\\X.H\x00"),
        (CMD.C_FL, b"TNC:\\X.H\x00\x00"),
        (CMD.C_MC, b""),
        (CMD.C_EK, b""),
        (CMD.C_CC, struct.pack("!H", ParCCC.RESET_TNC)),
        (CMD.C_CC, struct.pack("!H", ParCCC.DELETE_TABLE_ENTRY)),
        (CMD.C_CC, b""),
        (CMD.A_LG, b"PLCDEBUG\x00"),
        (CMD.A_LG, b"DATA\x00"),
        (RSP.T_FD, b""),
    ],
)
def test_writes_are_blocked(command, payload):
    with pytest.raises(WriteBlocked):
        check_telegram(command, payload)


def test_allowlist_is_minimal():
    assert {c.value for c in READ_TELEGRAMS} == {"A_LG", "A_LO", "R_VR", "R_PR", "R_CI", "R_RI", "R_FI", "R_FL", "T_OK"}
    assert ALLOWED_LOGINS == {"INSPECT", "FILE", "DNC"}
    assert {c.name for c in ALLOWED_SYSTEM_COMMANDS} == {
        "SET_BUF512", "SET_BUF1024", "SET_BUF2048", "SET_BUF3072", "SET_BUF4096", "SECURE_FILE_SEND"
    }


def test_fail_closed_without_known_internals():
    with pytest.raises(AdapterError, match="Schreibschutz"):
        read_only(object())


def test_pylsv2_version_is_pinned():
    # Der Schutz hängt an pyLSV2-Interna (_llcom.telegram) – Updates nur mit erneutem Test
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    assert "pyLSV2==1.7.0" in requirements and pyLSV2.__version__ == "1.7.0"


# --- Code: Verbindungen nur über den Schutz, nur erlaubte pyLSV2-Funktionen ------------------------

ALLOWED_CALLS = {
    "connect", "disconnect", "login", "program_status", "execution_state", "program_stack",
    "override_state", "spindle_tool_status", "get_error_messages", "file_info", "recive_file",
}
ALLOWED_ATTRIBUTES = {"versions", "last_error"}


def test_connections_only_via_guard():
    for path in (ROOT / "app").rglob("*.py"):
        if path.name != "lsv2_guard.py":
            assert "pyLSV2.LSV2(" not in path.read_text(encoding="utf-8"), f"{path.name}: Verbindung ohne Schreibschutz"


@pytest.mark.parametrize("rel", ["app/adapters/lsv2_adapter.py", "app/probe.py"])
def test_only_read_functions_are_used(rel):
    tree = ast.parse((ROOT / rel).read_text(encoding="utf-8"))
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "con"
    }
    assert used <= ALLOWED_CALLS | ALLOWED_ATTRIBUTES, used - ALLOWED_CALLS - ALLOWED_ATTRIBUTES
