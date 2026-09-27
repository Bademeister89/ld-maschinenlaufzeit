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
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import api, config_api
from .adapters.base import MachineAdapter
from .adapters.lsv2_adapter import Lsv2Adapter
from .adapters.sim_adapter import SimAdapter, SimulatedMachine
from .collector import MachineCollector
from .config import MachineConfig, Settings, load_settings
from .db import Database
from .registry import MachineManager

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Windows liest MIME-Typen aus der Registry; dort steht für .js teils "text/plain",
# womit Browser ES-Module verweigern.
mimetypes.add_type("text/javascript", ".js")
mimetypes.add_type("text/css", ".css")


@dataclass
class AppContext:
    settings: Settings
    db: Database
    manager: MachineManager
    tz: ZoneInfo

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
            "Start: %d Maschine(n), %s, Datenordner %s",
            len(manager.collectors),
            "SIMULATION" if s.simulate else "LSV2",
            s.data_dir,
        )
        try:
            yield
        finally:
            await manager.stop()
            db.close()

    app = FastAPI(title="LD Maschinenlaufzeit", lifespan=lifespan)
    app.include_router(api.router)
    app.include_router(config_api.router)
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
app = create_app()
