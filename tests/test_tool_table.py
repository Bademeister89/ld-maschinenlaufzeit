"""Werkzeugnamen aus der Werkzeugtabelle TOOL.T der Steuerung."""

import asyncio
import re
from datetime import datetime, timezone

import pytest

from app.adapters import lsv2_adapter
from app.adapters.base import AdapterError
from app.adapters.sim_adapter import TOOLS, SimAdapter, SimulatedMachine
from app.collector import TOOL_TABLE_CHECK_S, TOOL_TABLE_RETRY_S, MachineCollector
from app.tool_table import TOOL_TABLE, ToolTableFile, parse_tool_table

from .conftest import MACHINE, NullAdapter, snap

# Ausschnitt im Format der iTNC 530 (feste Spaltenbreiten, Kopfzeile gibt die Spalten vor)
TOOL_T = (
    "BEGIN TOOL     .T     MM\r\n"
    "T    NAME             L           R           R2          DL      TL RT  TIME1 CUR.TIME DOC\r\n"
    "0    NULLWERKZEUG     +0          +0          +0          +0             0     0        Nullwerkzeug\r\n"
    "1    NC-ANBOHRER      +80.5       +5          +0          +0             0     0        \r\n"
    "5    BOHRER D8,5      +120.25     +4.25       +0          +0             60    12       Kühlung innen\r\n"
    "5.1  BOHRER SCHWESTER +119        +4.25       +0          +0             60    0        \r\n"
    "7                     +50         +3          +0          +0             0     0        ohne Namen\r\n"
    "8.1  NUR INDEX        +50         +3          +0          +0             0     0        \r\n"
    "100  FRÄSER_D10       +95.1       +5          +0          +0          L  0     0        \r\n"
    "[END]\r\n"
)


def test_parse_tool_table():
    assert parse_tool_table(TOOL_T) == {
        1: "NC-ANBOHRER",
        5: "BOHRER D8,5",  # Leerzeichen im Namen bleiben, T5.1 überschreibt das Hauptwerkzeug nicht
        8: "NUR INDEX",  # nur indiziert vorhanden
        100: "FRÄSER_D10",
    }


def test_parse_tool_table_without_header():
    assert parse_tool_table("") == {}
    assert parse_tool_table("BEGIN TOOL .T MM\n1 X\n[END]\n") == {}


# --- LSV2-Adapter (Steuerung durch eine Attrappe ersetzt) --------------------------------


class FakeLSV2:
    files = {}
    connected = 0

    def __init__(self, host, port, timeout, safe_mode):
        assert safe_mode is True  # Datei lesen ohne DNC-Login
        self.last_error = "Fehler"
        # Übertragungsschicht, in die sich der Schreibschutz einhängt (ohne sie: keine Verbindung)
        self._llcom = type("Telegramme", (), {"telegram": staticmethod(lambda *args, **kwargs: None)})()

    def connect(self):
        FakeLSV2.connected += 1

    def disconnect(self):
        FakeLSV2.connected -= 1

    def file_info(self, path):
        if path not in self.files:
            return None
        text, mtime = self.files[path]
        return type("Info", (), {"size": len(text), "timestamp": datetime.fromtimestamp(mtime, timezone.utc)})

    def recive_file(self, remote, local, override_file, binary_mode):
        assert binary_mode is False
        local.write_bytes(self.files[remote][0].encode("latin-1"))  # Heidenhain: ISO-8859-1
        return True


@pytest.fixture
def fake_lsv2(monkeypatch):
    FakeLSV2.files = {TOOL_TABLE: (TOOL_T, 1_700_000_000.0)}
    FakeLSV2.connected = 0
    monkeypatch.setattr(lsv2_adapter.pyLSV2, "LSV2", FakeLSV2)
    return FakeLSV2


def test_lsv2_fetch_tool_table(fake_lsv2):
    adapter = lsv2_adapter.Lsv2Adapter("10.0.0.1")
    result = adapter.fetch_tool_table(None)
    assert result.names[100] == "FRÄSER_D10" and len(result.names) == 4
    assert adapter.fetch_tool_table((result.size, result.mtime)) is None  # unverändert
    assert fake_lsv2.connected == 0  # Verbindung jedes Mal wieder geschlossen
    fake_lsv2.files = {}
    assert adapter.fetch_tool_table(None).error == f"{TOOL_TABLE} nicht gefunden"


def test_lsv2_fetch_tool_table_unreachable():
    adapter = lsv2_adapter.Lsv2Adapter("127.0.0.1", port=1, timeout=0.5)
    with pytest.raises(AdapterError, match="Keine LSV2-Verbindung"):
        adapter.fetch_tool_table(None)


def test_simulation_has_tool_table():
    sim = SimulatedMachine(seed=1, start=0, speed=1.0)
    adapter = SimAdapter(sim, clock=lambda: 0)
    result = adapter.fetch_tool_table(None)
    assert result.names == dict(TOOLS)
    assert adapter.fetch_tool_table((result.size, result.mtime)) is None
    # Wie die echte Steuerung meldet die Simulation in der Spindel nur die Nummer
    seen, t = set(), 0.0
    while t < 6 * 3600:
        s = sim.state_at(t)
        if s and s.tool:
            seen.add(s.tool)
        t = sim.next_change
    assert seen and all(re.fullmatch(r"T\d+", tool) for tool in seen)


# --- Collector ------------------------------------------------------------------------------


class TableAdapter(NullAdapter):
    def __init__(self, names):
        self.names = names
        self.calls = []
        self.fail = False

    def fetch_tool_table(self, known):
        self.calls.append(known)
        if self.fail:
            raise AdapterError("Übertragung gestört")
        if known == (100, 1.0):
            return None
        return ToolTableFile(100, 1.0, dict(self.names))


def run_fetch(c):
    async def scenario():
        c._maybe_fetch_tool_table()
        if c._fetch_task:
            await c._fetch_task

    asyncio.run(scenario())


def test_collector_applies_tool_names(db):
    now = [0.0]
    adapter = TableAdapter({12: "FRAESER_D16", 30: "GEWINDEFR_M10"})
    c = MachineCollector(MACHINE, adapter, db, clock=lambda: now[0])
    c.process(snap("STARTED", tool="T12"), 0)
    assert db.tool("m1", 12)["name"] == ""  # Spindelabfrage ohne Namen
    run_fetch(c)
    assert db.tool("m1", 12)["name"] == "FRAESER_D16"
    # Später auftauchende Werkzeuge bekommen den Namen gleich beim Anlegen
    c.process(snap("STARTED", tool="T30"), 10)
    assert db.tool("m1", 30)["name"] == "GEWINDEFR_M10"
    # Innerhalb von 10 min kein neues Lesen; danach nur die Prüfung auf Änderungen
    now[0] = TOOL_TABLE_CHECK_S - 1
    run_fetch(c)
    assert adapter.calls == [None]
    now[0] = TOOL_TABLE_CHECK_S
    run_fetch(c)
    assert adapter.calls == [None, (100, 1.0)]


def test_collector_retries_early_for_unknown_tool(db):
    now = [0.0]
    adapter = TableAdapter({})
    c = MachineCollector(MACHINE, adapter, db, clock=lambda: now[0])
    run_fetch(c)
    adapter.names = {44: "NEU"}  # an der Steuerung neu eingetragen
    c.process(snap("STARTED", tool="T44"), 5)
    now[0] = TOOL_TABLE_RETRY_S - 1
    run_fetch(c)
    assert len(adapter.calls) == 1
    now[0] = TOOL_TABLE_RETRY_S
    run_fetch(c)
    assert db.tool("m1", 44)["name"] == "NEU"


def test_collector_survives_tool_table_error(db):
    adapter = TableAdapter({12: "X"})
    adapter.fail = True
    c = MachineCollector(MACHINE, adapter, db, clock=lambda: 0.0)
    c.process(snap("STARTED", tool="T12"), 0)
    run_fetch(c)  # kein Absturz, nur Log
    assert db.tool("m1", 12)["name"] == ""
