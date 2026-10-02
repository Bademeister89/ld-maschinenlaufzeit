"""Maschinenverwaltung zur Laufzeit: Konfiguration in der Datenbank, ein Collector je Maschine.

Änderungen aus dem Konfigurations-Tab wirken sofort: Neue Maschinen werden abgefragt,
geänderte Adressen neu verbunden, entfernte nicht mehr erfasst. Ihre bisher erfassten
Daten bleiben in der Datenbank erhalten.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
import unicodedata
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import orders, tools
from .adapters.base import MachineAdapter
from .collector import MachineCollector
from .config import MachineConfig, Settings
from .db import Database
from .netcheck import parse_address

log = logging.getLogger(__name__)

MAX_IMAGE_BYTES = 5 * 1024 * 1024
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$")

AdapterFactory = Callable[[MachineConfig], MachineAdapter]


class ConfigError(ValueError):
    """Ungültige Eingabe; der Text wird im Konfigurations-Tab angezeigt."""


class MachineNotFound(LookupError):
    pass


def slugify(text: str) -> str:
    text = text.lower().translate(str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"}))
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:40].strip("-") or "maschine"


def validate_machine(data: dict[str, Any]) -> dict[str, Any]:
    name = str(data.get("name") or "").strip()
    host = str(data.get("host") or "").strip()
    note = str(data.get("note") or "").strip()
    if not name:
        raise ConfigError("Bitte einen Namen angeben.")
    if len(name) > 60:
        raise ConfigError("Der Name darf höchstens 60 Zeichen lang sein.")
    if not host:
        raise ConfigError("Bitte die IP-Adresse oder den Hostnamen der Steuerung angeben.")
    if not _HOST_RE.match(host):
        raise ConfigError(f"„{host}“ ist keine gültige IP-Adresse bzw. kein gültiger Hostname.")
    try:
        port = int(data.get("port") or 19000)
    except (TypeError, ValueError):
        raise ConfigError("Der Port muss eine Zahl sein.") from None
    if not 1 <= port <= 65535:
        raise ConfigError("Der Port muss zwischen 1 und 65535 liegen.")
    if len(note) > 200:
        raise ConfigError("Standort / Notiz darf höchstens 200 Zeichen lang sein.")
    check_host = str(data.get("check_host") or "").strip()
    if check_host:
        try:
            parse_address(check_host)
        except ValueError as exc:
            raise ConfigError(f"Prüfadresse: {exc}") from None
    return {"name": name, "host": host, "port": port, "note": note, "check_host": check_host}


def image_extension(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    raise ConfigError("Bitte ein Bild als JPG, PNG oder WebP hochladen.")


class MachineManager:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        adapter_factory: AdapterFactory,
        run_collectors: bool = True,
    ):
        self.settings = settings
        self.db = db
        self._adapter_factory = adapter_factory
        self._run = run_collectors
        self.collectors: dict[str, MachineCollector] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    # --- Lesen ---------------------------------------------------------------------

    @staticmethod
    def _config(row: dict[str, Any]) -> MachineConfig:
        return MachineConfig(
            id=row["id"],
            name=row["name"],
            host=row["host"],
            port=row["port"],
            note=row["note"],
            sort_order=row["sort_order"],
            image=row["image"],
            check_host=row["check_host"],
        )

    def machines(self) -> list[MachineConfig]:
        return [self._config(row) for row in self.db.machines()]

    def get(self, machine_id: str) -> MachineConfig:
        row = self.db.machine(machine_id)
        if row is None or row["removed"]:
            raise MachineNotFound(machine_id)
        return self._config(row)

    def ordered_collectors(self) -> list[MachineCollector]:
        return sorted(
            list(self.collectors.values()),
            key=lambda c: (c.machine.sort_order, c.machine.name.lower(), c.machine.id),
        )

    def image_path(self, machine_id: str) -> Path | None:
        row = self.db.machine(machine_id)
        if row is None or not row["image"]:
            return None
        path = self.settings.images_dir / row["image"]
        return path if path.is_file() else None

    # --- Start / Stopp -------------------------------------------------------------

    async def start(self) -> None:
        # Einmalig: Maschinen aus der config.yaml übernehmen (Startbestand)
        if self.db.get_meta("machines_seeded") is None:
            for m in self.settings.machines:
                if self.db.machine(m.id) is None:
                    self.db.insert_machine(m.id, m.name, m.host, m.port, m.note, m.sort_order, m.check_host)
            self.db.set_meta("machines_seeded", "1")
        # Auftragsnummern für Daten nachtragen, die vor der Auftragsauswertung erfasst wurden
        assigned = orders.backfill(self.db)
        if assigned:
            log.info("Aufträge: %d Programm(e) nachträglich Aufträgen zugeordnet", assigned)
        # Nach dem Update: Werkzeugaufrufe aus den bisher erfassten Werkzeugwechseln nachtragen
        calls = tools.backfill_calls(self.db)
        if calls:
            log.info("Werkzeuge: %d Aufruf(e) aus bisherigen Werkzeugwechseln nachgetragen", calls)
        for machine in self.machines():
            self._spawn(machine)

    async def stop(self) -> None:
        await asyncio.gather(*(self._stop(mid) for mid in list(self.collectors)))

    def _spawn(self, machine: MachineConfig) -> None:
        collector = MachineCollector(
            machine,
            self._adapter_factory(machine),
            self.db,
            self.settings.poll_interval_s,
            fetch_programs=self.settings.fetch_programs,
            program_max_bytes=int(self.settings.program_max_mb * 1_000_000),
        )
        self.collectors[machine.id] = collector
        if self._run:
            self._tasks[machine.id] = asyncio.create_task(collector.run(), name=f"collector-{machine.id}")

    async def _stop(self, machine_id: str) -> None:
        self.collectors.pop(machine_id, None)
        task = self._tasks.pop(machine_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    def _refresh(self) -> None:
        """Geänderte Stammdaten (Name, Bild, Reihenfolge, Prüfadresse) an laufende Collectors weitergeben."""
        for machine in self.machines():
            if machine.id in self.collectors:
                self.collectors[machine.id].machine = machine

    # --- Ändern --------------------------------------------------------------------

    async def add(self, data: dict[str, Any]) -> MachineConfig:
        fields = validate_machine(data)
        async with self._lock:
            self._check_address(fields["host"], fields["port"])
            machine_id = self._unique_id(slugify(fields["name"]))
            order = max((m.sort_order for m in self.machines()), default=-10) + 10
            self.db.insert_machine(machine_id, **fields, sort_order=order)
            machine = self.get(machine_id)
            self._event(machine_id, "config", {"action": "added", **fields})
            self._spawn(machine)
            log.info("Maschine angelegt: %s (%s:%s)", machine.name, machine.host, machine.port)
            return machine

    async def update(self, machine_id: str, data: dict[str, Any]) -> MachineConfig:
        fields = validate_machine(data)
        async with self._lock:
            old = self.get(machine_id)
            self._check_address(fields["host"], fields["port"], exclude=machine_id)
            self.db.update_machine(machine_id, **fields)
            new = self.get(machine_id)
            if (old.host, old.port) != (new.host, new.port):
                # Neue Adresse: Verbindung sauber schließen und neu aufbauen
                await self._stop(machine_id)
                self._spawn(new)
                self._event(machine_id, "config", {"action": "address", "from": f"{old.host}:{old.port}", "to": f"{new.host}:{new.port}"})
            else:
                self._refresh()
            return new

    async def remove(self, machine_id: str) -> None:
        async with self._lock:
            machine = self.get(machine_id)
            await self._stop(machine_id)
            self.db.remove_machine(machine_id)
            self._delete_image_file(machine.image)
            self._event(machine_id, "config", {"action": "removed"})
            log.info("Maschine entfernt: %s (Daten bleiben erhalten)", machine.name)

    async def reorder(self, machine_ids: list[str]) -> None:
        async with self._lock:
            if sorted(machine_ids) != sorted(m.id for m in self.machines()):
                raise ConfigError("Die Reihenfolge muss alle Maschinen genau einmal enthalten.")
            self.db.set_machine_order(machine_ids)
            self._refresh()

    async def set_image(self, machine_id: str, data: bytes) -> MachineConfig:
        if not data:
            raise ConfigError("Die Datei ist leer.")
        if len(data) > MAX_IMAGE_BYTES:
            raise ConfigError("Das Bild ist größer als 5 MB.")
        extension = image_extension(data)
        async with self._lock:
            old = self.get(machine_id)
            filename = f"{slugify(machine_id)}-{secrets.token_hex(4)}.{extension}"
            self.settings.images_dir.mkdir(parents=True, exist_ok=True)
            (self.settings.images_dir / filename).write_bytes(data)
            self.db.set_machine_image(machine_id, filename)
            self._delete_image_file(old.image)
            self._refresh()
            return self.get(machine_id)

    async def delete_image(self, machine_id: str) -> MachineConfig:
        async with self._lock:
            old = self.get(machine_id)
            self.db.set_machine_image(machine_id, None)
            self._delete_image_file(old.image)
            self._refresh()
            return self.get(machine_id)

    # --- Hilfen --------------------------------------------------------------------

    def _check_address(self, host: str, port: int, exclude: str | None = None) -> None:
        for m in self.machines():
            if m.id != exclude and m.host.lower() == host.lower() and m.port == port:
                raise ConfigError(f"Die Adresse {host}:{port} ist bereits für „{m.name}“ eingetragen.")

    def _unique_id(self, base: str) -> str:
        # Auch entfernte Maschinen zählen, damit alte Daten nie einer neuen Maschine zugeordnet werden
        taken = {row["id"] for row in self.db.machines(include_removed=True)}
        candidate, n = base, 2
        while candidate in taken:
            candidate, n = f"{base}-{n}", n + 1
        return candidate

    def _delete_image_file(self, filename: str | None) -> None:
        if filename:
            (self.settings.images_dir / filename).unlink(missing_ok=True)

    def _event(self, machine_id: str, event_type: str, payload: dict[str, Any]) -> None:
        self.db.add_event(machine_id, time.time(), event_type, payload)
