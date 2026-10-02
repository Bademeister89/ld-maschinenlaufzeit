"""Ein Bild je Auftrag (fertiges Bauteil).

Der Browser verkleinert das Foto und schickt in *einer* Anfrage zwei JPEGs: das große Bild
(längste Kante 1280 px) und das Vorschaubild (256 px). Der Server kodiert nichts um (keine
Bildbibliothek), er prüft nur Typ und Größe. Gespeichert wird in ``<datenordner>/images/orders/``:

- ``26-21055-<token>.jpg``        großes Bild (Auftragsdetail)
- ``26-21055-<token>-thumb.jpg``  Vorschaubild (Auftragsliste, Live-Karte)

In der Datenbank steht nur der Dateiname des großen Bildes (``orders.image``), das Vorschaubild
folgt aus dem Namen. Beide Dateien werden geschrieben, bevor der Name in die Datenbank kommt –
so fehlt das Vorschaubild nie. Beim Ersetzen und Entfernen werden die alten Dateien gelöscht.
Fehlt eine Datei auf der Platte, gilt der Auftrag einfach als „ohne Bild“.

Verwaiste Dateien werden bewusst nicht automatisch aufgeräumt: Echt- und Demo-Datenbank teilen
sich den Datenordner, ein Abgleich mit nur einer der beiden würde die Bilder der anderen löschen.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import threading
from pathlib import Path
from typing import Any

from .db import Database

log = logging.getLogger(__name__)

# Auftragsschlüssel, z. B. 26-21055 oder mit Version 26-21055V1. Mit fullmatch geprüft (``$`` ließe ein
# "\n" am Ende durch) und nur ASCII-Ziffern (``\d`` erlaubt auch andere Schriften) – der Schlüssel landet
# im Dateinamen.
KEY_RE = re.compile(r"[0-9]{2}-[0-9]{4,5}(?:V[0-9]{1,2})?")
MAX_IMAGE_BYTES = 1024 * 1024  # großes Bild; die Verkleinerung im Browser liefert ca. 150–250 KB
MAX_THUMB_BYTES = 100 * 1024  # Vorschaubild; die Verkleinerung liefert ca. 10–20 KB
MAX_UPLOAD_BYTES = MAX_IMAGE_BYTES + MAX_THUMB_BYTES
JPEG_MAGIC = b"\xff\xd8\xff"
SIZES = ("full", "thumb")


class ImageError(ValueError):
    """Ungültiges Bild. ``status``: HTTP-Status (400 oder 413); der Text erscheint in der Oberfläche."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def valid_key(key: str) -> bool:
    return KEY_RE.fullmatch(key) is not None


def thumb_name(image: str) -> str:
    """``26-21055-1a2b3c4d.jpg`` → ``26-21055-1a2b3c4d-thumb.jpg``"""
    return image.removesuffix(".jpg") + "-thumb.jpg"


def split_upload(data: bytes, image_length: int) -> tuple[bytes, bytes]:
    """Rumpf der Anfrage = großes Bild, direkt dahinter das Vorschaubild.

    ``image_length`` ist die Länge des großen Bildes (Kopfzeile ``X-Image-Length``).
    """
    if not data:
        raise ImageError("Die Datei ist leer.")
    if not 0 < image_length < len(data):
        raise ImageError("Großes Bild und Vorschaubild müssen zusammen hochgeladen werden.")
    image, thumb = data[:image_length], data[image_length:]
    if not (image.startswith(JPEG_MAGIC) and thumb.startswith(JPEG_MAGIC)):
        raise ImageError("Bitte ein Foto als JPEG hochladen. Die Seite verkleinert das Foto und wandelt es dabei um.")
    if len(image) > MAX_IMAGE_BYTES:
        raise ImageError("Das Bild ist größer als 1 MB.", 413)
    if len(thumb) > MAX_THUMB_BYTES:
        raise ImageError("Das Vorschaubild ist größer als 100 KB.", 413)
    return image, thumb


class OrderImages:
    """Dateien im Bilderordner der Aufträge; der Dateiname steht in ``orders.image``."""

    def __init__(self, db: Database, directory: Path):
        self.db = db
        self.dir = directory
        # Ersetzen und Entfernen laufen nacheinander, sonst bliebe bei zwei gleichzeitigen Uploads
        # eine Datei ohne Eintrag liegen
        self._lock = threading.Lock()

    # --- Lesen ---------------------------------------------------------------------

    def path(self, image: str | None, size: str = "full") -> Path | None:
        """Pfad der Datei, falls sie auf der Platte liegt – sonst None („ohne Bild“)."""
        if not image or Path(image).name != image:  # nur einfache Dateinamen, nie Pfade
            return None
        path = self.dir / (thumb_name(image) if size == "thumb" else image)
        return path if path.is_file() else None

    def files(self) -> set[str]:
        """Alle Dateinamen im Ordner: ein Verzeichnislesen statt zwei Prüfungen je Auftrag (Liste)."""
        try:
            with os.scandir(self.dir) as entries:
                return {entry.name for entry in entries if entry.is_file()}
        except FileNotFoundError:
            return set()

    def public(self, order: dict[str, Any], files: set[str] | None = None) -> dict[str, Any]:
        """Auftrag für die API: statt des Dateinamens ``image_url`` und ``thumb_url`` (oder None).

        Der Dateiname steht als ``v=`` in der Adresse: Ein neues Bild hat eine neue Adresse, daher
        darf der Browser jedes Bild unbegrenzt zwischenspeichern.
        """
        order = dict(order)
        image = order.pop("image", None)
        if files is None:
            present = self.path(image) is not None and self.path(image, "thumb") is not None
        else:
            present = bool(image) and image in files and thumb_name(image) in files
        base = f"/api/orders/{order['key']}/image"
        order["image_url"] = f"{base}?size=full&v={image}" if present else None
        order["thumb_url"] = f"{base}?size=thumb&v={image}" if present else None
        return order

    # --- Ändern --------------------------------------------------------------------

    def save(self, key: str, image: bytes, thumb: bytes) -> None:
        """Bild setzen oder ersetzen; die Dateien des bisherigen Bildes werden gelöscht."""
        if not valid_key(key):
            raise ImageError(f"Ungültiger Auftragsschlüssel: {key}")
        name = f"{key}-{secrets.token_hex(4)}.jpg"
        with self._lock:
            order = self.db.order(key)
            if order is None:
                raise LookupError(key)
            self.dir.mkdir(parents=True, exist_ok=True)
            try:
                (self.dir / thumb_name(name)).write_bytes(thumb)
                (self.dir / name).write_bytes(image)
                self.db.set_order_image(key, name)
            except BaseException:
                self._unlink(name)
                raise
            self._unlink(order["image"])

    def delete(self, key: str) -> None:
        with self._lock:
            order = self.db.order(key)
            if order is None:
                raise LookupError(key)
            self.db.set_order_image(key, None)
            self._unlink(order["image"])

    def _unlink(self, image: str | None) -> None:
        if not image or Path(image).name != image:
            return
        for name in (image, thumb_name(image)):
            try:
                (self.dir / name).unlink(missing_ok=True)
            except OSError as exc:  # z. B. unter Windows, während die Datei gerade ausgeliefert wird
                log.warning("Auftragsbild %s konnte nicht gelöscht werden: %s", name, exc)
