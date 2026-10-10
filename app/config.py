"""Einstellungen aus config.yaml, überschreibbar per Umgebungsvariablen.

Die Maschinen selbst werden im Konfigurations-Tab gepflegt und in der Datenbank gespeichert.
``machines`` in der config.yaml dient nur als Startbestand für eine leere Datenbank.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

PROJECT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_DIR / "config.yaml"
# Portable Version: eigenes Python liegt in runtime\ – dann bleiben die Daten im Ordner.
IS_PORTABLE = (PROJECT_DIR / "runtime" / "python.exe").exists()


def default_data_dir() -> Path:
    if os.environ.get("LDM_DATA_DIR"):
        return Path(os.environ["LDM_DATA_DIR"])
    if IS_PORTABLE:
        return PROJECT_DIR / "data"
    # Entwicklung: außerhalb des Nextcloud-Ordners, Sync und SQLite-WAL vertragen sich nicht.
    return Path.home() / "ld-mainmachine" / "data"


@dataclass(frozen=True)
class MachineConfig:
    id: str
    name: str
    host: str
    port: int = 19000
    note: str = ""
    sort_order: int = 0
    image: str | None = None
    check_host: str = ""  # Prüfadresse am Standort (z. B. Router vor Ort), optional
    tool_slots: int | None = None  # Werkzeugplätze im Magazin (Top-Werkzeuge nach Aufrufen), optional
    hourly_rate: float | None = None  # Stundensatz in €/h (Preis Fräsen der Artikel), optional

    @property
    def image_url(self) -> str | None:
        # Dateiname als Versionsparameter: ein neues Bild umgeht den Browser-Cache
        return f"/api/machines/{self.id}/image?v={self.image}" if self.image else None

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "note": self.note,
            "sort_order": self.sort_order,
            "image_url": self.image_url,
            "check_host": self.check_host,
            "tool_slots": self.tool_slots,
            "hourly_rate": self.hourly_rate,
        }


@dataclass(frozen=True)
class Settings:
    machines: tuple[MachineConfig, ...] = ()
    poll_interval_s: float = 2.0
    timeout_s: float = 10.0
    timezone: str = "Europe/Berlin"
    db_path: Path = Path("data.db")
    simulate: bool = False
    sim_speed: float = 1.0
    listen_host: str = "0.0.0.0"
    port: int = 8000
    fetch_programs: bool = True
    program_max_mb: float = 20.0

    @property
    def data_dir(self) -> Path:
        return self.db_path.parent

    @property
    def images_dir(self) -> Path:
        return self.data_dir / "images"

    @property
    def order_images_dir(self) -> Path:
        return self.images_dir / "orders"


def _timezone(raw: dict) -> str:
    """config.yaml → Umgebungsvariable TZ (z. B. aus der Unraid-Vorlage) → Europe/Berlin."""
    for candidate in (raw.get("timezone"), os.environ.get("TZ")):
        if not candidate:
            continue
        try:
            ZoneInfo(str(candidate))
            return str(candidate)
        except (ZoneInfoNotFoundError, ValueError):
            continue  # z. B. POSIX-Angaben wie "CET-1CEST" sind keine IANA-Zone
    return "Europe/Berlin"


def _env_bool(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "ja", "on"}


def load_settings(path: Path | str | None = None) -> Settings:
    path = Path(path or os.environ.get("CONFIG_PATH") or DEFAULT_CONFIG)
    raw = (yaml.safe_load(path.read_text(encoding="utf-8")) if path.exists() else None) or {}

    machines = tuple(
        MachineConfig(
            id=str(m["id"]),
            name=str(m.get("name") or m["id"]),
            host=str(m["host"]),
            port=int(m.get("port", 19000)),
            note=str(m.get("note") or ""),
            sort_order=index * 10,
            check_host=str(m.get("check_host") or ""),
        )
        for index, m in enumerate(raw.get("machines") or [])
    )
    ids = [m.id for m in machines]
    if len(set(ids)) != len(ids):
        raise ValueError(f"Maschinen-IDs in {path} müssen eindeutig sein: {ids}")

    simulate = _env_bool("SIMULATE") or bool(raw.get("simulate", False))
    # Simulationsdaten landen nie in der echten Datenbank.
    default_db = default_data_dir() / ("demo.db" if simulate else "data.db")
    server = raw.get("server") or {}

    return Settings(
        machines=machines,
        poll_interval_s=float(os.environ.get("POLL_INTERVAL_S") or raw.get("poll_interval_s", 2.0)),
        timeout_s=float(raw.get("timeout_s", 10.0)),
        timezone=_timezone(raw),
        db_path=Path(os.environ.get("DB_PATH") or raw.get("db_path") or default_db),
        simulate=simulate,
        sim_speed=float(os.environ.get("SIM_SPEED") or raw.get("sim_speed", 1.0)),
        # LDM_LISTEN_HOST / LDM_PORT setzt der Starter (python -m app), damit die Anzeige
        # im Konfigurations-Tab den tatsächlich verwendeten Werten entspricht
        listen_host=str(os.environ.get("LDM_LISTEN_HOST") or server.get("host", "0.0.0.0")),
        port=int(os.environ.get("LDM_PORT") or server.get("port", 8000)),
        fetch_programs=bool(raw.get("fetch_programs", True)),
        program_max_mb=float(raw.get("program_max_mb", 20.0)),
    )
