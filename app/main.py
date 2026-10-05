"""Web-App: ``python -m app`` oder ``uvicorn app.main:app``.

Beim Start wird je konfigurierter Maschine ein Collector-Task gestartet; die Web-Oberfläche
liegt unter ``/``, die API unter ``/api``.
"""

from __future__ import annotations

import logging
import mimetypes
import time
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import BUILD, __version__, api, config_api, orders_api, tools_api
from .adapters.base import MachineAdapter
from .adapters.lsv2_adapter import Lsv2Adapter
from .adapters.sim_adapter import SimAdapter, SimulatedMachine
from .collector import MachineCollector
from .config import MachineConfig, Settings, load_settings
from .db import Database
from .order_images import OrderImages
from .registry import MachineManager

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Windows liest MIME-Typen aus der Registry; dort steht für .js teils "text/plain",
# womit Browser ES-Module verweigern.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")
mimetypes.add_type("image/svg+xml", ".svg")
mimetypes.add_type("image/x-icon", ".ico")
mimetypes.add_type("application/manifest+json", ".webmanifest")


class CacheControl:
    """Browser sollen Seiten und Skripte bei jedem Aufruf auf Änderungen prüfen (ETag → 304).

    Ohne Vorgabe halten Browser Dateien nach eigener Schätzung oft stundenlang vor – nach einem
    Update erschienen dann alte Seiten neben neuen. API-Antworten werden gar nicht gespeichert.
    Antworten mit eigener Vorgabe (z. B. Maschinenbilder) bleiben unverändert.
    """

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        value = b"no-store" if scope["path"].startswith("/api/") else b"no-cache"

        async def send_with_header(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                if not any(name.lower() == b"cache-control" for name, _ in headers):
                    message = {**message, "headers": [*headers, (b"cache-control", value)]}
            await send(message)

        await self.app(scope, receive, send_with_header)


@dataclass
class AppContext:
    settings: Settings
    db: Database
    manager: MachineManager
    tz: ZoneInfo
    order_images: OrderImages = field(init=False)
    started_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        self.order_images = OrderImages(self.db, self.settings.order_images_dir)

    @property
    def collectors(self) -> dict[str, MachineCollector]:
        return self.manager.collectors


def adapter_factory(settings: Settings):
    def build(machine: MachineConfig) -> MachineAdapter:
        if settings.simulate:
            seed = zlib.crc32(machine.id.encode())
            # Zwei Stunden vor "jetzt" beginnen: Beim Start steckt die Maschine mitten im Betrieb
            start = time.time() - 2 * 3600 / settings.sim_speed
            sim = SimulatedMachine(seed=seed, start=start, speed=settings.sim_speed, tz=settings.timezone)
            return SimAdapter(sim)
        return Lsv2Adapter(machine.host, machine.port, settings.timeout_s)

    return build


def setup_file_logging(settings: Settings) -> None:
    """Log-Datei im Datenordner – wichtig im Hintergrundbetrieb ohne Konsolenfenster."""
    log_dir = settings.data_dir / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / "laufzeit.log"
    for name in ("", "uvicorn"):
        logger = logging.getLogger(name)
        if any(getattr(h, "baseFilename", None) == str(path) for h in logger.handlers):
            continue
        handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5, encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)


def create_app(settings: Settings | None = None, run_collectors: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        s = settings or load_settings()
        setup_file_logging(s)
        db = Database(s.db_path)
        manager = MachineManager(s, db, adapter_factory(s), run_collectors)
        app.state.ctx = AppContext(s, db, manager, ZoneInfo(s.timezone))
        await manager.start()
        log.info(
            "Start (Version %s, Build %s): %d Maschine(n), %s, Datenordner %s",
            __version__,
            BUILD or "lokal",
            len(manager.collectors),
            "SIMULATION" if s.simulate else "LSV2",
            s.data_dir,
        )
        try:
            yield
        finally:
            await manager.stop()
            db.close()

    app = FastAPI(title="LD-Machine-Viewer", version=__version__, lifespan=lifespan)
    app.add_middleware(CacheControl)
    app.include_router(api.router)
    app.include_router(config_api.router)
    app.include_router(orders_api.router)
    app.include_router(tools_api.router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
app = create_app()
