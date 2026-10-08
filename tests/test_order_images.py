"""Ein Bild je Auftrag: Hochladen, Ersetzen, Entfernen, Auslieferung, Migration auf Schema 8."""

import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.config import MachineConfig, Settings
from app.db import SCHEMA_VERSION, Database
from app.main import create_app
from app.order_images import MAX_IMAGE_BYTES, MAX_THUMB_BYTES, ImageError, split_upload, thumb_name, valid_key

from .conftest import feed, snap

KEY = "21055"
PROGRAM = "TNC:\\AUFTRAG\\26-21055-01-01.H"
FULL = b"\xff\xd8\xff\xe0" + b"G" * 2000  # großes Bild (nur Kopf echt, Inhalt egal)
THUMB = b"\xff\xd8\xff\xe0" + b"k" * 200
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100


def upload(client, key=KEY, full=FULL, thumb=THUMB, length=None):
    headers = {"Content-Type": "application/octet-stream"}
    if length != "":
        headers["X-Image-Length"] = str(len(full) if length is None else length)
    return client.put(f"/api/orders/{key}/image", content=full + thumb, headers=headers)


@pytest.fixture
def app_client(tmp_path):
    settings = Settings(machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"),), db_path=tmp_path / "o.db", simulate=True)
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as client:
        app.state.ctx.db.ensure_order(KEY, 2026, "21055", 1000)
        app.state.ctx.db.ensure_order("4711", 2026, "4711", 900)
        yield app, client


@pytest.fixture
def client(app_client):
    return app_client[1]


@pytest.fixture
def folder(app_client):
    return app_client[0].state.ctx.settings.order_images_dir


def files(folder):
    return sorted(p.name for p in folder.iterdir()) if folder.exists() else []


def pair(folder):
    """(großes Bild, Vorschaubild) – nach Namen sortiert stünde "-thumb.jpg" vor ".jpg"."""
    names = files(folder)
    assert len(names) == 2
    return tuple(sorted(names, key=lambda n: n.endswith("-thumb.jpg")))


# --- Hochladen und Ausliefern -----------------------------------------------------------


def test_upload_stores_both_files_and_serves_them(client, folder):
    r = upload(client)
    assert r.status_code == 200
    order = r.json()
    assert "image" not in order
    full_name, thumb = pair(folder)
    assert re.fullmatch(r"21055-[0-9a-f]{8}\.jpg", full_name)
    assert thumb == thumb_name(full_name) == full_name.replace(".jpg", "-thumb.jpg")
    assert folder.parent.name == "images" and folder.name == "orders"
    assert (folder / full_name).read_bytes() == FULL and (folder / thumb).read_bytes() == THUMB
    assert order["image_url"] == f"/api/orders/{KEY}/image?size=full&v={full_name}"
    assert order["thumb_url"] == f"/api/orders/{KEY}/image?size=thumb&v={full_name}"

    for url, content in ((order["image_url"], FULL), (order["thumb_url"], THUMB)):
        got = client.get(url)
        assert got.status_code == 200
        assert got.content == content
        assert got.headers["content-type"] == "image/jpeg"
        assert got.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert client.get(f"/api/orders/{KEY}/image").content == FULL  # ohne size: großes Bild
    assert client.get(f"/api/orders/{KEY}/image?size=huge").status_code == 422


def test_upload_for_rim(app_client, folder):
    app, client = app_client
    app.state.ctx.db.ensure_order("10101018", 0, "10101018", 1100, kind="rim")
    r = upload(client, key="10101018")
    assert r.status_code == 200
    full_name, thumb = pair(folder)
    assert re.fullmatch(r"10101018-[0-9a-f]{8}\.jpg", full_name)
    assert client.get(r.json()["thumb_url"]).content == THUMB


def test_list_detail_and_update_carry_urls(client):
    upload(client)
    rows = {r["key"]: r for r in client.get("/api/orders").json()["orders"]}
    assert rows[KEY]["thumb_url"].startswith(f"/api/orders/{KEY}/image?size=thumb&v=")
    assert rows[KEY]["image_url"].startswith(f"/api/orders/{KEY}/image?size=full&v=")
    assert rows["4711"]["image_url"] is None and rows["4711"]["thumb_url"] is None
    assert all("image" not in r for r in rows.values())
    detail = client.get(f"/api/orders/{KEY}").json()["order"]
    assert detail["image_url"] == rows[KEY]["image_url"]
    assert detail["thumb_url"] == rows[KEY]["thumb_url"]
    updated = client.put(f"/api/orders/{KEY}", json={"title": "Flansch"}).json()
    assert (updated["title"], updated["thumb_url"]) == ("Flansch", rows[KEY]["thumb_url"])


def test_live_card_gets_thumbnail_of_running_order(app_client):
    app, client = app_client
    collector = app.state.ctx.collectors["m1"]
    feed(collector, (2000, snap("IDLE", PROGRAM)), (2010, snap("STARTED", PROGRAM)))
    assert client.get("/api/machines").json()["machines"][0]["order"]["thumb_url"] is None
    uploaded = upload(client).json()
    live = client.get("/api/machines").json()["machines"][0]
    assert live["order"]["key"] == KEY
    assert live["order"]["thumb_url"] == uploaded["thumb_url"]
    # Großes Bild nur als Auswahl für Bildschirme mit hoher Pixeldichte (srcset der Live-Karte)
    assert live["order"]["image_url"] == uploaded["image_url"]


def test_replace_removes_old_files(client, folder):
    first = upload(client).json()
    old = files(folder)
    second = upload(client, full=FULL + b"2", thumb=THUMB + b"2").json()
    new = files(folder)
    assert len(new) == 2 and not set(old) & set(new)
    assert second["image_url"] != first["image_url"]
    assert client.get(second["image_url"]).content == FULL + b"2"
    assert client.get(second["thumb_url"]).content == THUMB + b"2"


def test_delete_removes_both_files(client, folder):
    upload(client)
    upload(client, key="4711")
    r = client.delete(f"/api/orders/{KEY}/image")
    assert r.status_code == 200
    assert (r.json()["image_url"], r.json()["thumb_url"]) == (None, None)
    assert all(name.startswith("4711-") for name in files(folder))  # anderer Auftrag bleibt
    assert len(files(folder)) == 2
    assert client.get(f"/api/orders/{KEY}/image").status_code == 404
    assert client.get(f"/api/orders/{KEY}/image?size=thumb").status_code == 404
    assert client.delete(f"/api/orders/{KEY}/image").status_code == 200  # ohne Bild: nichts zu tun


# --- Prüfungen ----------------------------------------------------------------------------


def test_unknown_order(client, folder):
    assert upload(client, key="99999").status_code == 404
    assert client.get("/api/orders/99999/image").status_code == 404
    assert client.delete("/api/orders/99999/image").status_code == 404
    assert files(folder) == []


@pytest.mark.parametrize("key", ["2026-21055", "26-210555", "26-210", "26_21055", "abc", "26-21055.jpg", "..%5C26-21055"])
def test_invalid_key(client, key, folder):
    r = upload(client, key=key)
    assert r.status_code == 400
    assert "Ungültiger Auftragsschlüssel" in r.json()["detail"]
    assert client.get(f"/api/orders/{key}/image").status_code == 400
    assert client.delete(f"/api/orders/{key}/image").status_code == 400
    assert files(folder) == []


@pytest.mark.parametrize(
    ("full", "thumb", "length"),
    [
        (PNG, THUMB, None),  # großes Bild kein JPEG
        (FULL, PNG, None),  # Vorschaubild kein JPEG
        (b"<svg onload=alert(1)>", b"", None),
        (FULL, THUMB, ""),  # Länge fehlt
        (FULL, THUMB, "abc"),
        (FULL, b"", None),  # Vorschaubild fehlt
        (FULL, THUMB, 0),
        (b"", b"", 0),  # leer
    ],
)
def test_wrong_type_or_missing_thumbnail(client, folder, full, thumb, length):
    r = upload(client, full=full, thumb=thumb, length=length)
    assert r.status_code == 400
    assert r.json()["detail"]
    assert files(folder) == []
    assert client.get(f"/api/orders/{KEY}").json()["order"]["image_url"] is None


def test_too_large(client, folder):
    big = b"\xff\xd8\xff" + b"x" * (MAX_IMAGE_BYTES - 2)
    r = upload(client, full=big)
    assert r.status_code == 413
    assert r.json()["detail"] == "Das Bild ist größer als 1 MB."
    big_thumb = b"\xff\xd8\xff" + b"x" * (MAX_THUMB_BYTES - 2)
    r = upload(client, thumb=big_thumb)
    assert r.status_code == 413
    assert r.json()["detail"] == "Das Vorschaubild ist größer als 100 KB."
    assert upload(client, full=big, thumb=big_thumb).status_code == 413  # Rumpf insgesamt zu groß
    assert files(folder) == []


def test_failed_upload_keeps_existing_image(client, folder):
    before = upload(client).json()
    assert upload(client, full=PNG).status_code == 400
    assert upload(client, full=b"\xff\xd8\xff" + b"x" * MAX_IMAGE_BYTES).status_code == 413
    assert client.get(f"/api/orders/{KEY}").json()["order"]["image_url"] == before["image_url"]
    assert len(files(folder)) == 2


def test_missing_file_on_disk_means_no_image(app_client, client, folder):
    first = upload(client).json()
    full_name, thumb = pair(folder)
    (folder / thumb).unlink()  # z. B. von Hand gelöscht oder unvollständig zurückgesichert
    rows = {r["key"]: r for r in client.get("/api/orders").json()["orders"]}
    assert (rows[KEY]["image_url"], rows[KEY]["thumb_url"]) == (None, None)
    assert client.get(f"/api/orders/{KEY}").json()["order"]["image_url"] is None
    assert client.get(first["thumb_url"]).status_code == 404
    (folder / full_name).unlink()
    assert client.get(first["image_url"]).status_code == 404
    # Ersetzen und Entfernen klappen trotzdem
    assert upload(client).json()["thumb_url"]
    assert len(files(folder)) == 2
    assert client.delete(f"/api/orders/{KEY}/image").status_code == 200
    assert files(folder) == []


def test_missing_folder_means_no_image(app_client, client):
    """Ohne je hochgeladenes Bild gibt es den Ordner nicht – kein Fehler, alle Aufträge ohne Bild."""
    assert not app_client[0].state.ctx.settings.order_images_dir.exists()
    rows = client.get("/api/orders").json()["orders"]
    assert rows and all(r["image_url"] is None for r in rows)


# --- Hilfsfunktionen ------------------------------------------------------------------------


def test_helpers():
    assert valid_key("21055") and valid_key("4711") and valid_key("10101018")  # Auftrag, Felge
    assert not valid_key("26-21055") and not valid_key("21053V1")  # Jahr und Version gehören nicht zum Schlüssel
    assert not valid_key("21055\n") and not valid_key("../21055") and not valid_key("123")
    assert not valid_key("٢١٠٥٥")  # arabisch-indische Ziffern
    assert thumb_name("26-21055-1a2b3c4d.jpg") == "26-21055-1a2b3c4d-thumb.jpg"
    assert split_upload(FULL + THUMB, len(FULL)) == (FULL, THUMB)
    with pytest.raises(ImageError) as exc:
        split_upload(FULL + THUMB, len(FULL) + len(THUMB))
    assert exc.value.status == 400


# --- Migration -------------------------------------------------------------------------------


def test_update_from_schema_v7_database(tmp_path):
    """Datenbank der Version 1.7.0 (Schema 7, Aufträge ohne Bildspalte) nach dem Update."""
    path = tmp_path / "v7.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES ('schema_version', '7'), ('manufacturers_seeded', '1');"
        "CREATE TABLE orders (key TEXT PRIMARY KEY, year INTEGER NOT NULL, number TEXT NOT NULL,"
        " title TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'open', created_at REAL NOT NULL,"
        " closed_at REAL);"
        "INSERT INTO orders VALUES ('26-21055', 2026, '21055', 'Flansch', 'closed', 100, 200);"
    )
    con.commit()
    con.close()

    db = Database(path)
    assert db.get_meta("schema_version") == str(SCHEMA_VERSION)
    order = db.order("21055")
    assert (order["title"], order["status"], order["closed_at"], order["image"]) == ("Flansch", "closed", 200, None)
    db.set_order_image("21055", "26-21055-1a2b3c4d.jpg")
    db.close()

    db = Database(path)  # erneuter Start: Migration läuft nicht doppelt
    assert db.order("21055")["image"] == "26-21055-1a2b3c4d.jpg"
    db.close()

    settings = Settings(machines=(), db_path=path, simulate=True)
    with TestClient(create_app(settings, run_collectors=False)) as c:
        [row] = c.get("/api/orders?status=all").json()["orders"]
        # Eintrag vorhanden, Datei nicht: gilt als ohne Bild
        assert (row["key"], row["title"], row["image_url"]) == ("21055", "Flansch", None)
