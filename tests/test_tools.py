"""Smoke-Tests für die Kommandozeilen-Werkzeuge in tools/."""

import os
import socket
import subprocess
import sys
from pathlib import Path

from app.db import Database

ROOT = Path(__file__).resolve().parent.parent


def run_tool(*args: str) -> subprocess.CompletedProcess:
    # Ohne PYTHONIOENCODING: prüft, dass die Werkzeuge selbst UTF-8 ausgeben
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "PYTHONUTF8")}
    return subprocess.run(
        [sys.executable, *args], cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=120
    )


def test_seed_demo_creates_history_and_keeps_configured_machines(tmp_path):
    db_path = tmp_path / "demo.db"
    result = run_tool("tools/seed_demo.py", "--days", "2", "--db", str(db_path))
    assert result.returncode == 0, result.stderr
    db = Database(db_path)
    assert [m["id"] for m in db.machines()] == ["dmg1", "dmg2"]
    assert db.get_meta("machines_seeded") == "1"
    assert db.intervals(0, 1e12)
    # Eine im Konfigurations-Tab ergänzte Maschine bleibt beim erneuten Erzeugen erhalten
    db.insert_machine("neu", "Neue Maschine", "10.9.9.9", 19000, "", 30)
    db.close()

    assert run_tool("tools/seed_demo.py", "--days", "1", "--db", str(db_path)).returncode != 0  # ohne --force
    result = run_tool("tools/seed_demo.py", "--days", "1", "--db", str(db_path), "--force")
    assert result.returncode == 0, result.stderr
    db = Database(db_path)
    assert [m["id"] for m in db.machines()] == ["dmg1", "dmg2", "neu"]
    db.close()


def test_seed_demo_refuses_real_database(tmp_path):
    result = run_tool("tools/seed_demo.py", "--db", str(tmp_path / "data.db"))
    assert result.returncode != 0
    assert not (tmp_path / "data.db").exists()


def test_probe_reports_unreachable_host():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    result = run_tool("tools/probe.py", "127.0.0.1", "--port", str(port), "--timeout", "1")
    assert result.returncode == 1
    assert "[1] Netzwerk" in result.stdout
    assert "FEHLER" in result.stdout
    assert "MOD → Netzwerk" in result.stdout  # Sonderzeichen korrekt ausgegeben
