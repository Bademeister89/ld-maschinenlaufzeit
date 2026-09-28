"""Versionsnummer, Änderungsprotokoll und Cache-Vorgaben für den Browser."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app
from app import changelog
from app.config import MachineConfig, Settings
from app.main import create_app

SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def test_version_is_semver():
    assert SEMVER.match(app.__version__)


def test_workflow_can_read_version():
    # GitHub Actions liest die Nummer per sed mit genau diesem Muster (Tag-Prüfung, Image-Label)
    source = Path(app.__file__).read_text(encoding="utf-8")
    assert re.findall(r'^__version__ = "(.*)"$', source, re.M) == [app.__version__]


def test_changelog_matches_version():
    releases = changelog.load()
    assert releases, "CHANGELOG.md fehlt oder hat kein erkennbares Format"
    newest = releases[0]
    assert newest["version"] == app.__version__, "Neue Version in CHANGELOG.md eintragen"
    assert newest["date"] and newest["sections"] and newest["sections"][0]["items"]
    versions = [tuple(map(int, r["version"].split("."))) for r in releases]
    assert versions == sorted(versions, reverse=True) and len(set(versions)) == len(versions)


def test_changelog_parse():
    text = """# Änderungsprotokoll

Einleitung, wird ignoriert.
- auch diese Liste

## [2.0.0] – 2026-10-01

### Neu
- Erster Punkt mit `Code` und **fett**,
  über zwei Zeilen
- Zweiter Punkt

### Behoben
- Fehler

## 1.0.0
- ohne Abschnitt
"""
    assert changelog.parse(text) == [
        {
            "version": "2.0.0",
            "date": "2026-10-01",
            "sections": [
                {"title": "Neu", "items": ["Erster Punkt mit Code und fett, über zwei Zeilen", "Zweiter Punkt"]},
                {"title": "Behoben", "items": ["Fehler"]},
            ],
        },
        {"version": "1.0.0", "date": None, "sections": [{"title": "", "items": ["ohne Abschnitt"]}]},
    ]


def test_changelog_missing_file(tmp_path):
    assert changelog.load(tmp_path / "fehlt.md") == []


def test_build_from_env(monkeypatch):
    monkeypatch.setenv("LDM_BUILD", " 2026-09-28-abc1234\n")
    assert app._build() == "2026-09-28-abc1234"


@pytest.fixture
def client(tmp_path):
    settings = Settings(machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"),), db_path=tmp_path / "v.db", simulate=True)
    with TestClient(create_app(settings, run_collectors=False)) as c:
        yield c


def test_api_version(client):
    data = client.get("/api/version").json()
    assert data["version"] == app.__version__
    assert "build" in data
    assert data["changelog"][0]["version"] == app.__version__
    meta = client.get("/api/meta").json()
    assert meta["version"] == app.__version__ and "build" in meta
    settings = client.get("/api/config").json()["settings"]
    assert settings["version"] == app.__version__ and "build" in settings


@pytest.mark.parametrize("path", ["/", "/auswertung.html", "/auftraege.html", "/common.js", "/style.css"])
def test_pages_are_revalidated(client, path):
    # Ohne Vorgabe zeigten Browser nach einem Update alte Seiten (z. B. ohne Reiter „Aufträge“)
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-cache"
    again = client.get(path, headers={"If-None-Match": r.headers["etag"]})
    assert again.status_code == 304


def test_api_is_not_stored(client):
    assert client.get("/api/meta").headers["cache-control"] == "no-store"
    assert client.get("/api/machines/m1/image").headers["cache-control"] == "no-store"  # 404 ohne Bild


def test_every_page_links_all_tabs():
    static = Path(app.__file__).with_name("static")
    for page in ["index.html", "auswertung.html", "auftraege.html", "konfiguration.html"]:
        html = (static / page).read_text(encoding="utf-8")
        for target in ['href="./"', 'href="auswertung.html"', 'href="auftraege.html"', 'href="konfiguration.html"']:
            assert target in html, f"{page}: Link {target} fehlt"
