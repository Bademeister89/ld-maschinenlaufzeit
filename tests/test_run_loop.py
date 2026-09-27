import asyncio
import socket

import pytest

from app import collector as collector_module
from app.adapters.base import AdapterError
from app.adapters.lsv2_adapter import Lsv2Adapter
from app.collector import MachineCollector

from .conftest import MACHINE, snap


class ScriptedAdapter:
    """Verbindung schlägt einmal fehl, dann zwei Abfragen, dann Abbruch, dann wieder da."""

    def __init__(self):
        self.script = ["fail", "ok", "read", "read", "drop", "ok", "read"]
        self.closed = 0

    def _next(self):
        return self.script.pop(0) if self.script else "read"

    def connect(self):
        if self._next() == "fail":
            raise AdapterError("Steuerung aus")
        return {"control": "TEST"}

    def read(self):
        if self._next() == "drop":
            raise AdapterError("Verbindung abgebrochen")
        return snap("STARTED")

    def close(self):
        self.closed += 1


def test_run_loop_reconnects_and_records_offline(db, monkeypatch):
    monkeypatch.setattr(collector_module, "BACKOFF_S", (0.01,))
    adapter = ScriptedAdapter()
    clock = iter(range(1000))
    c = MachineCollector(MACHINE, adapter, db, poll_interval_s=0.01, clock=lambda: float(next(clock)))

    async def run_briefly():
        task = asyncio.create_task(c.run())
        while adapter.script:
            await asyncio.sleep(0.01)
        await asyncio.sleep(0.05)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run_briefly())

    states = [r["state"] for r in db._query("SELECT state FROM state_intervals ORDER BY id")]
    assert states[:3] == ["OFFLINE", "RUNNING", "OFFLINE"]
    assert states[-1] == "RUNNING"
    types = [e["type"] for e in sorted(db.events(0, 1e9), key=lambda e: e["id"])]
    assert types[:4] == ["offline", "online", "offline", "online"]
    # Kurzer Abbruch mitten im Lauf: es bleibt ein einziger Programmdurchlauf
    assert len(db.runs(0, 1e9)) == 1
    assert c.live()["connected"] is True
    assert adapter.closed >= 2  # nach Abbruch und beim Beenden


def test_lsv2_adapter_reports_unreachable_host():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]  # Port ohne Listener
    adapter = Lsv2Adapter("127.0.0.1", port=port, timeout=1)
    with pytest.raises(AdapterError, match="Keine LSV2-Verbindung"):
        adapter.connect()
    with pytest.raises(AdapterError, match="nicht verbunden"):
        adapter.read()
