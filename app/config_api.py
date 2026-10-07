"""API für den Konfigurations-Tab: Maschinen anlegen, ändern, entfernen, sortieren,
Bild hochladen und Verbindung testen; Werkzeughersteller pflegen."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, HTTPException, Request, Response

from . import BUILD, __version__
from .config import DEFAULT_CONFIG, IS_PORTABLE
from .probe import run_probe
from .registry import MAX_IMAGE_BYTES, ConfigError, MachineManager, MachineNotFound, validate_machine

if TYPE_CHECKING:
    from .main import AppContext

router = APIRouter(prefix="/api/config")

MANUFACTURER_MAX = 80  # wie das Feld „Hersteller“ am Werkzeug


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _manager(request: Request) -> MachineManager:
    return _ctx(request).manager


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except ConfigError as exc:
        raise HTTPException(400, str(exc)) from exc
    except MachineNotFound as exc:
        raise HTTPException(404, f"Unbekannte Maschine: {exc}") from exc


@router.get("")
def overview(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    s = ctx.settings
    return {
        "machines": [m.public() for m in ctx.manager.machines()],
        "settings": {
            "version": __version__,
            "build": BUILD,
            "simulate": s.simulate,
            "portable": IS_PORTABLE,
            "data_dir": str(s.data_dir),
            "db_path": str(s.db_path),
            "config_path": os.environ.get("CONFIG_PATH") or str(DEFAULT_CONFIG),
            "poll_interval_s": s.poll_interval_s,
            "timeout_s": s.timeout_s,
            "timezone": s.timezone,
            "listen_host": s.listen_host,
            "port": s.port,
        },
    }


@router.post("/machines", status_code=201)
async def add_machine(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    with _errors():
        return (await _manager(request).add(payload)).public()


@router.put("/machines/{machine_id}")
async def update_machine(request: Request, machine_id: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    with _errors():
        return (await _manager(request).update(machine_id, payload)).public()


@router.delete("/machines/{machine_id}", status_code=204)
async def remove_machine(request: Request, machine_id: str) -> Response:
    with _errors():
        await _manager(request).remove(machine_id)
    return Response(status_code=204)


@router.put("/machines/{machine_id}/image")
async def upload_image(request: Request, machine_id: str) -> dict[str, Any]:
    """Bild als Rohdaten im Body (JPG, PNG oder WebP, höchstens 5 MB)."""
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > MAX_IMAGE_BYTES:
            raise HTTPException(413, "Das Bild ist größer als 5 MB.")
    with _errors():
        return (await _manager(request).set_image(machine_id, bytes(data))).public()


@router.delete("/machines/{machine_id}/image")
async def delete_image(request: Request, machine_id: str) -> dict[str, Any]:
    with _errors():
        return (await _manager(request).delete_image(machine_id)).public()


@router.post("/order", status_code=204)
async def reorder(request: Request, payload: dict[str, Any] = Body(...)) -> Response:
    ids = payload.get("ids")
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise HTTPException(400, "Erwartet: {\"ids\": [...]}")
    with _errors():
        await _manager(request).reorder(ids)
    return Response(status_code=204)


@router.post("/test")
async def test_connection(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Verbindungstest (rein lesend) für eine Adresse – auch vor dem Speichern nutzbar."""
    with _errors():
        fields = validate_machine({**payload, "name": "Test"})
    result = await asyncio.to_thread(
        run_probe, fields["host"], fields["port"], _ctx(request).settings.timeout_s, fields["check_host"]
    )
    return result.as_dict()


# --- Werkzeughersteller -------------------------------------------------------------------


def _manufacturer_name(payload: dict[str, Any]) -> str:
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Bitte einen Herstellernamen eintragen.")
    if len(name) > MANUFACTURER_MAX:
        raise HTTPException(400, f"Der Herstellername darf höchstens {MANUFACTURER_MAX} Zeichen lang sein.")
    return name


@router.get("/manufacturers")
def list_manufacturers(request: Request) -> dict[str, Any]:
    return {"manufacturers": _ctx(request).db.manufacturers()}


@router.post("/manufacturers", status_code=201)
def add_manufacturer(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    db = _ctx(request).db
    name = _manufacturer_name(payload)
    manufacturer_id = db.add_manufacturer(name, time.time())
    if manufacturer_id is None:
        raise HTTPException(409, f"„{name}“ ist schon in der Liste.")
    return db.manufacturer(manufacturer_id)


@router.put("/manufacturers/{manufacturer_id}")
def rename_manufacturer(request: Request, manufacturer_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    db = _ctx(request).db
    if db.manufacturer(manufacturer_id) is None:
        raise HTTPException(404, "Unbekannter Hersteller")
    name = _manufacturer_name(payload)
    if not db.rename_manufacturer(manufacturer_id, name):
        raise HTTPException(409, f"„{name}“ ist schon in der Liste.")
    return db.manufacturer(manufacturer_id)


@router.delete("/manufacturers/{manufacturer_id}", status_code=204)
def delete_manufacturer(request: Request, manufacturer_id: int) -> Response:
    db = _ctx(request).db
    if db.manufacturer(manufacturer_id) is None:
        raise HTTPException(404, "Unbekannter Hersteller")
    db.delete_manufacturer(manufacturer_id)
    return Response(status_code=204)


# --- Felgen-Designs ------------------------------------------------------------------------
# Ziffern 3–4 der Felgennummer (z. B. 10 in 10101018) → Name des Designs (999, Z06 …)

RIM_DESIGN_MAX = 40


@router.get("/rim-designs")
def list_rim_designs(request: Request) -> dict[str, Any]:
    return {"designs": _ctx(request).db.rim_designs()}


@router.put("/rim-designs/{code}")
def set_rim_design(request: Request, code: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Design anlegen oder umbenennen."""
    if not 0 <= code <= 99:
        raise HTTPException(400, "Die Design-Nummer hat zwei Ziffern (00–99), z. B. 10 für 999.")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "Bitte einen Namen für das Design eintragen.")
    if len(name) > RIM_DESIGN_MAX:
        raise HTTPException(400, f"Der Name darf höchstens {RIM_DESIGN_MAX} Zeichen lang sein.")
    db = _ctx(request).db
    created = db.set_rim_design(code, name)
    return {"code": code, "name": name, "created": created}


@router.delete("/rim-designs/{code}", status_code=204)
def delete_rim_design(request: Request, code: int) -> Response:
    if not _ctx(request).db.delete_rim_design(code):
        raise HTTPException(404, "Unbekanntes Design")
    return Response(status_code=204)
