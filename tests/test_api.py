import time

import pytest
from fastapi.testclient import TestClient

from app.config import MachineConfig, Settings
from app.main import create_app

from .conftest import snap


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"), MachineConfig("m2", "DMG 2", "10.0.0.2")),
        db_path=tmp_path / "api.db",
        simulate=True,
    )
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as c:
        now = time.time()
        col = app.state.ctx.collectors["m1"]
        base = now - 3600
        for t, s in [
            (base, snap("IDLE")),
            (base + 60, snap("STARTED", "TNC:\\PROD\\TEIL.H", tool="T1")),
            (base + 600, snap("STARTED", "TNC:\\PROD\\TEIL.H", tool="T2")),
            (base + 1260, snap("FINISHED", "TNC:\\PROD\\TEIL.H")),
            (now - 5, snap("FINISHED", "TNC:\\PROD\\TEIL.H")),
        ]:
            col.process(s, t)
        c.base = base
        yield c


def test_meta(client):
    data = client.get("/api/meta").json()
    assert [m["id"] for m in data["machines"]] == ["m1", "m2"]
    assert data["simulate"] is True
    assert data["labels"]["state"]["RUNNING"] == "Läuft"
    assert data["labels"]["state"]["NO_DATA"] == "Keine Daten"


def test_live(client):
    data = client.get("/api/machines").json()
    m1, m2 = data["machines"]
    assert m1["state"] == "READY"
    assert m1["program"] == "TNC:\\PROD\\TEIL.H"
    assert m2["state"] is None  # noch nie abgefragt


def test_stats_and_timeline(client):
    b = client.base
    stats = client.get("/api/stats", params={"from": b, "to": b + 3000}).json()
    m1 = stats["machines"]["m1"]
    assert m1["totals"]["RUNNING"] == pytest.approx(1200)
    assert sum(m1["totals"].values()) == pytest.approx(3000)
    assert stats["machines"]["m2"]["totals"]["NO_DATA"] == pytest.approx(3000)
    [prog] = stats["programs"]
    assert (prog["runs"], prog["finished"]) == (1, 1)

    tl = client.get("/api/machines/m1/timeline", params={"from": b, "to": b + 3000}).json()
    assert [iv["state"] for iv in tl["intervals"]] == ["READY", "RUNNING", "READY"]


def test_runs_and_events(client):
    b = client.base
    [run] = client.get("/api/runs", params={"from": b, "machine": "m1"}).json()
    assert (run["result"], run["run_s"]) == ("finished", pytest.approx(1200))
    events = client.get("/api/events", params={"from": b, "machine": "m1"}).json()
    assert [e["type"] for e in events] == ["tool_change"]


def test_iso_time_parameters(client):
    r = client.get("/api/stats", params={"from": "2026-01-01T00:00:00", "to": "2026-01-02"})
    assert r.status_code == 200
    assert r.json()["period_s"] == 86400


def test_errors(client):
    assert client.get("/api/machines/xx/timeline").status_code == 404
    assert client.get("/api/stats", params={"from": 200, "to": 100}).status_code == 400
    assert client.get("/api/stats", params={"from": "gestern"}).status_code == 400


def test_csv_export(client):
    b = client.base
    r = client.get("/api/export.csv", params={"kind": "runs", "from": b})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    text = r.content.decode("utf-8")
    assert text.startswith("\ufeff")
    header, row = text.lstrip("\ufeff").strip().split("\r\n")
    assert header.split(";")[:3] == ["Lauf-Nr.", "Maschine", "Programm"]
    cols = row.split(";")
    assert cols[1] == "DMG 1"
    assert cols[5] == "fertig"
    assert cols[6] == "20,00"  # Laufzeit in Minuten mit Dezimalkomma

    r = client.get("/api/export.csv", params={"kind": "intervals", "from": b, "machine": "m1"})
    lines = r.content.decode("utf-8").strip().split("\r\n")
    assert lines[1].split(";")[1] == "Bereit"


def test_static_pages(client):
    assert "Live-Status" in client.get("/").text
    assert client.get("/auswertung.html").status_code == 200
    js = client.get("/common.js")
    assert js.headers["content-type"].startswith("text/javascript")
