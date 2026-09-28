"""App-Symbol: Browser, Startbildschirm (Manifest), Unraid, Windows-Verknüpfung."""

import json
import re
import struct
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import MachineConfig, Settings
from app.main import create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "app" / "static"
PAGES = ["index.html", "auswertung.html", "auftraege.html", "werkzeuge.html", "konfiguration.html"]
ICON_URL = re.compile(r"https://raw\.githubusercontent\.com/Bademeister89/ld-maschinenlaufzeit/main/(\S+?\.png)")


@pytest.fixture
def client(tmp_path):
    settings = Settings(machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"),), db_path=tmp_path / "i.db", simulate=True)
    with TestClient(create_app(settings, run_collectors=False)) as c:
        yield c


@pytest.mark.parametrize(
    ("path", "media_type"),
    [
        ("/favicon.ico", "image/x-icon"),
        ("/icons/icon.svg", "image/svg+xml"),
        ("/icons/apple-touch-icon.png", "image/png"),
        ("/manifest.webmanifest", "application/manifest+json"),
    ],
)
def test_icon_files_are_served(client, path, media_type):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].split(";")[0] == media_type


def test_manifest_icons_exist(client):
    manifest = json.loads(client.get("/manifest.webmanifest").content)
    assert manifest["name"] == "LD Maschinenlaufzeit" and manifest["start_url"] == "./"
    assert any(icon.get("purpose") == "maskable" for icon in manifest["icons"])
    for icon in manifest["icons"]:
        r = client.get(f"/{icon['src']}")
        assert r.status_code == 200 and r.headers["content-type"].startswith(icon["type"])


def test_every_page_links_icons():
    for page in PAGES:
        html = (STATIC / page).read_text(encoding="utf-8")
        for tag in ('href="favicon.ico"', 'href="icons/icon.svg"', 'href="icons/apple-touch-icon.png"',
                    'href="manifest.webmanifest"', 'class="brand-icon"'):
            assert tag in html, f"{page}: {tag} fehlt"


def test_windows_icon_has_large_sizes():
    data = (STATIC / "icons" / "ld-maschinenlaufzeit.ico").read_bytes()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1)
    sizes = {data[6 + 16 * i] or 256 for i in range(count)}
    assert {16, 32, 48, 256} <= sizes


def test_unraid_icon_points_to_repo_file():
    urls = ICON_URL.findall((ROOT / "unraid" / "ld-maschinenlaufzeit.xml").read_text(encoding="utf-8"))
    urls += ICON_URL.findall((ROOT / "Dockerfile").read_text(encoding="utf-8"))
    assert len(urls) == 2 and len(set(urls)) == 1
    assert (ROOT / urls[0]).is_file()


def test_portable_shortcut_scripts():
    for name in ("verknuepfung.ps1", "verknuepfung-erstellen.cmd"):
        data = (ROOT / "portable" / name).read_bytes()
        assert data.isascii() and b"\r\n" in data  # Windows-Skripte: ASCII, CRLF
    script = (ROOT / "portable" / "verknuepfung.ps1").read_text(encoding="ascii")
    assert "app\\static\\icons\\ld-maschinenlaufzeit.ico" in script
    assert "portable\\verknuepfung.ps1" in (ROOT / "tools" / "build_portable.ps1").read_text(encoding="utf-8")
