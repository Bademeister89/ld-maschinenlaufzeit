"""Standort-Ausfall (z. B. VPN weg) von ausgeschalteten Maschinen unterscheiden."""

import asyncio
import socket
from zoneinfo import ZoneInfo

import pytest

from app import collector as collector_module
from app import stats
from app.adapters.base import AdapterError
from app.collector import MachineCollector
from app.config import MachineConfig, _timezone
from app.netcheck import parse_address, reachable
from app.probe import run_probe
from app.registry import ConfigError, validate_machine

from .conftest import feed, snap

SITE = MachineConfig(id="m1", name="Maschine 1", host="127.0.0.1", check_host="192.168.0.1")


def intervals(db):
    return db._query("SELECT state, started_at, ended_at FROM state_intervals ORDER BY id")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


# --- Adresse prüfen ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("192.168.0.1", ("192.168.0.1", 80)), ("fritz.box:443", ("fritz.box", 443)), (" 10.0.0.1:8080 ", ("10.0.0.1", 8080))],
)
def test_parse_address(text, expected):
    assert parse_address(text) == expected


@pytest.mark.parametrize("text", ["", "a b", "1.2.3.4:0", "1.2.3.4:99999", "http://1.2.3.4"])
def test_parse_address_rejects(text):
    with pytest.raises(ValueError):
        parse_address(text)


def test_reachable():
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen()
        assert reachable(f"127.0.0.1:{server.getsockname()[1]}", timeout=1)
    # TEST-NET-1 (RFC 5737): garantiert nicht vorhanden
    assert not reachable("192.0.2.1:80", timeout=0.3)


def test_validation_of_check_host():
    assert validate_machine({"name": "X", "host": "10.0.0.5", "check_host": " 10.0.0.1 "})["check_host"] == "10.0.0.1"
    assert validate_machine({"name": "X", "host": "10.0.0.5"})["check_host"] == ""
    with pytest.raises(ConfigError, match="Prüfadresse"):
        validate_machine({"name": "X", "host": "10.0.0.5", "check_host": "keine adresse"})


# --- Buchung -------------------------------------------------------------------------


def test_site_outage_leaves_gap_instead_of_offline(db):
    c = MachineCollector(SITE, None, db)
    feed(c, (0, snap("IDLE")), (1, snap("STARTED")), (3, snap("STARTED")))
    c.process(None, 10, "Timeout", site_down=True)
    c.process(None, 20, "Timeout", site_down=True)
    live = c.live()
    assert live["state"] == "NETWORK"
    assert live["state_since"] == 3
    assert live["forecast"] is None
    assert [(r["state"], r["started_at"], r["ended_at"]) for r in intervals(db)] == [("READY", 0, 1), ("RUNNING", 1, 3)]

    # Standort wieder da, Maschine ist fertig: Lauf endet beim letzten Lebenszeichen
    c.process(snap("FINISHED"), 30)
    [run] = db.runs(0, 100)
    assert (run["ended_at"], run["result"], run["run_s"]) == (3, "finished", 2)
    assert intervals(db)[-1]["state"] == "READY"
    assert intervals(db)[-1]["started_at"] == 30

    totals = stats.summarize(db, ["m1"], 0, 30, ZoneInfo("Europe/Berlin"), now=30)["machines"]["m1"]["totals"]
    assert totals["NO_DATA"] == pytest.approx(27)
    assert totals["OFFLINE"] == 0
    types = [e["type"] for e in sorted(db.events(0, 100), key=lambda e: e["id"])]
    assert types.count("site_unreachable") == 1


def test_machine_off_with_site_reachable_is_offline(db):
    c = MachineCollector(SITE, None, db)
    feed(c, (0, snap("IDLE")), (5, None), (10, None))
    assert c.live()["state"] == "OFFLINE"
    assert [r["state"] for r in intervals(db)] == ["READY", "OFFLINE"]


def test_site_outage_then_machine_off(db):
    c = MachineCollector(SITE, None, db)
    feed(c, (0, snap("IDLE")))
    c.process(None, 10, site_down=True)
    c.process(None, 20)  # Standort wieder erreichbar, Maschine aber aus
    rows = intervals(db)
    assert [(r["state"], r["started_at"]) for r in rows] == [("READY", 0), ("OFFLINE", 20)]


class FailingAdapter:
    def __init__(self, site_ok: bool):
        self.site_ok = site_ok
        self.checked = []

    def connect(self):
        raise AdapterError("Timeout")

    def read(self):
        raise AdapterError("nicht verbunden")

    def close(self):
        pass

    def site_reachable(self, address):
        self.checked.append(address)
        return self.site_ok


@pytest.mark.parametrize(("check_host", "site_ok", "expected"), [
    ("192.168.0.1", False, "NETWORK"),
    ("192.168.0.1", True, "OFFLINE"),
    ("", False, "OFFLINE"),  # ohne Prüfadresse wie bisher
])
def test_run_loop_checks_site(db, monkeypatch, check_host, site_ok, expected):
    monkeypatch.setattr(collector_module, "BACKOFF_S", (0.01,))
    adapter = FailingAdapter(site_ok)
    machine = MachineConfig(id="m1", name="M", host="10.0.0.9", check_host=check_host)
    c = MachineCollector(machine, adapter, db)

    async def run_briefly():
        task = asyncio.create_task(c.run())
        await asyncio.sleep(0.1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run_briefly())
    assert c.live()["state"] == expected
    assert adapter.checked == ([check_host] * len(adapter.checked) if check_host else [])
    if expected == "NETWORK":
        assert "Prüfadresse" in c.live()["connection_error"]


# --- Verbindungstest -----------------------------------------------------------------


def test_probe_reports_site_check():
    result = run_probe("127.0.0.1", free_port(), timeout=1, check_host="192.0.2.1")
    first, network = result.steps[0], result.steps[1]
    assert (first.title, first.ok, first.required) == ("Prüfadresse Standort", False, False)
    assert network.title == "Netzwerk" and not network.ok
    assert "VPN" in network.hints[0]


# --- Zeitzone ------------------------------------------------------------------------


def test_timezone_precedence(monkeypatch):
    monkeypatch.setenv("TZ", "America/New_York")
    assert _timezone({"timezone": "Europe/Vienna"}) == "Europe/Vienna"  # config.yaml gewinnt
    assert _timezone({}) == "America/New_York"  # sonst TZ (z. B. aus der Unraid-Vorlage)
    monkeypatch.setenv("TZ", "CET-1CEST")  # POSIX-Angabe: keine IANA-Zone
    assert _timezone({}) == "Europe/Berlin"
    monkeypatch.delenv("TZ")
    assert _timezone({"timezone": "Mars/Olympus"}) == "Europe/Berlin"
