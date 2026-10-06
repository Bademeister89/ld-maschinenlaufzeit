"""Diagnose-Datei für Fehlermeldungen (ZIP mit Logs, Mitschnitt und Auswertungsdaten)."""

import csv
import io
import json
import sqlite3
import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app.config import MachineConfig, Settings
from app.main import create_app

from .conftest import snap

PROGRAM = "TNC:\\PROD\\TEIL.H"


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"), MachineConfig("m2", "DMG 2", "10.0.0.2")),
        db_path=tmp_path / "diag.db",
        simulate=True,
    )
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as c:
        now = time.time()
        col = app.state.ctx.collectors["m1"]
        base = now - 3600
        for t, s in [
            (base, snap("IDLE", PROGRAM)),
            (base + 60, snap("STARTED", PROGRAM, line_no=5)),
            (base + 600, snap("STARTED", PROGRAM, line_no=180)),
            (base + 602, snap("STARTED", PROGRAM, line_no=199)),
            (base + 604, snap("IDLE", PROGRAM, line_no=0)),  # Programmende ohne FINISHED
            (base + 700, None),
        ]:
            col.process(s, t, reason="Zeitüberschreitung" if s is None else None)
        yield c


def _zip(response) -> zipfile.ZipFile:
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"].startswith('attachment; filename="ld-diagnose_')
    return zipfile.ZipFile(io.BytesIO(response.content))


def _rows(z: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(z.read(name).decode("utf-8-sig")), delimiter=";"))


def test_diagnose_zip_contents(client):
    z = _zip(client.get("/api/diagnose.zip"))
    names = set(z.namelist())
    assert {"LIESMICH.txt", "info.json", "live.json", "laeufe.csv", "zustaende.csv", "ereignisse.csv",
            "programmdateien.csv", "mitschnitt_m1.csv", "mitschnitt_m2.csv"} <= names
    assert "data.db" not in names
    assert any(n.startswith("logs/") for n in names)  # Log-Datei aus dem Datenordner

    info = json.loads(z.read("info.json"))
    assert info["version"] and info["zeitraum_tage"] == 7 and info["schema"] == "11"
    assert [m["id"] for m in info["maschinen"]] == ["m1", "m2"]
    assert set(json.loads(z.read("live.json"))) == {"m1", "m2"}


def test_run_end_state_shows_what_the_control_reported(client):
    z = _zip(client.get("/api/diagnose.zip"))
    [run] = _rows(z, "laeufe.csv")
    assert (run["program"], run["result"], run["status_am_ende"], run["status_danach"]) == (
        PROGRAM, "finished", "RUNNING/STARTED", "READY/IDLE",
    )


def test_raw_log_records_changes_with_line_before(client):
    z = _zip(client.get("/api/diagnose.zip"))
    rows = _rows(z, "mitschnitt_m1.csv")
    # Nur Änderungen: die Abfragen mit gleichem Status (Satz 180, 199) stehen nicht einzeln drin
    assert [(r["state"], r["pgm_state"], r["line_before"], r["line_no"]) for r in rows] == [
        ("READY", "IDLE", "", ""),
        ("RUNNING", "STARTED", "", "5"),
        ("READY", "IDLE", "199", "0"),  # letzter Satz vor dem Programmende
        ("OFFLINE", "", "0", ""),
    ]
    assert rows[-1]["reason"] == "Zeitüberschreitung"
    assert rows[1]["run_id"] and rows[1]["counted_program"] == PROGRAM


def test_diagnose_zip_with_database(client):
    z = _zip(client.get("/api/diagnose.zip", params={"days": 1, "db": "true"}))
    assert json.loads(z.read("info.json"))["zeitraum_tage"] == 1
    path = client.app.state.ctx.settings.data_dir / "kopie.db"
    path.write_bytes(z.read("data.db"))
    con = sqlite3.connect(path)
    assert con.execute("SELECT COUNT(*) FROM program_runs").fetchone()[0] == 1
    con.close()


def test_diagnose_zip_contains_pallet_tables(client):
    """Palettentabellen im Original (Zeichensatz der Steuerung), damit sich das Format prüfen lässt."""
    text = "BEGIN PAL1SP .P MM\nNR  TYPE NAME          LOCK\n0   PAL  1\n1   PGM  Bügel.H\n[END]\n"
    client.app.state.ctx.db.save_program_file("m1", "TNC:\\PROD\\pal1sp.p", len(text), 1.0, None, None, 0, ("1", "BÜGEL"), text)
    z = _zip(client.get("/api/diagnose.zip"))
    assert [n for n in z.namelist() if n.startswith("paletten/")] == ["paletten/m1/TNC_PROD_pal1sp.p"]
    assert z.read("paletten/m1/TNC_PROD_pal1sp.p") == text.encode("latin-1")
    assert "paletten/" in z.read("LIESMICH.txt").decode()


def test_diagnose_days_are_limited(client):
    assert client.get("/api/diagnose.zip", params={"days": 0}).status_code == 422
    assert client.get("/api/diagnose.zip", params={"days": 400}).status_code == 422
