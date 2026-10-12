"""API für den Tab „Planung“: Board mit Aufgaben je Meilenstein, Änderungsliste je Artikel mit Bildern.
Meilensteine und Bearbeiter stehen in der Konfiguration (``config_api``)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse

from . import planning
from .order_images import ImageError
from .orders_api import _read_upload

if TYPE_CHECKING:
    from .main import AppContext

router = APIRouter(prefix="/api/planning")

STATUS = Query("all", pattern="^(all|open|done)$")


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


@contextmanager
def _errors() -> Iterator[None]:
    try:
        yield
    except planning.PlanningError as exc:
        raise HTTPException(exc.status, str(exc)) from None


@router.get("/board")
def board(request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    today = datetime.now(ctx.tz).date()
    return {**planning.board(ctx.db, today), "workers": ctx.db.workers(), "today": today.isoformat()}


@router.get("/articles")
def article_choices(request: Request) -> dict[str, Any]:
    """Artikel zur Auswahl (Schlüssel, Bezeichnung, Vorschaubild des Auftrags) – ohne die Kosten-
    und Laufzeitrechnung der Artikelliste."""
    ctx = _ctx(request)
    orders = {o["key"]: o for o in ctx.db.orders()}
    files = ctx.order_images.files()
    rows = []
    for art in ctx.db.articles():
        order = orders.get(art["order_key"])
        if order is None:
            continue
        thumb = ctx.order_images.public({"key": order["key"], "image": order["image"]}, files)["thumb_url"]
        rows.append({"key": art["key"], "title": order["title"], "kind": order["kind"], "thumb_url": thumb})
    return {"articles": rows}


@router.post("/tasks", status_code=201)
def create_task(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    db = _ctx(request).db
    with _errors():
        task_id = planning.create_task(db, payload, time.time())
    return db.task(task_id)


@router.put("/tasks/{task_id}")
def update_task(request: Request, task_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    db = _ctx(request).db
    with _errors():
        planning.update_task(db, task_id, payload, time.time())
    return db.task(task_id)


@router.post("/tasks/{task_id}/move")
def move_task(request: Request, task_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Aufgabe in eine Spalte (``milestone_id``) an Stelle ``index`` (0 = oben) setzen."""
    db = _ctx(request).db
    with _errors():
        planning.move_task(db, task_id, payload.get("milestone_id"), payload.get("index", 0), time.time())
    return db.task(task_id)


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(request: Request, task_id: int) -> Response:
    with _errors():
        planning.delete_task(_ctx(request).db, task_id)
    return Response(status_code=204)


# --- Änderungen -------------------------------------------------------------------------------


def _public_changes(ctx: AppContext, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    files = ctx.change_images.files()
    for row in rows:
        row["images"] = [ctx.change_images.urls(image, files) for image in row["images"]]
    return rows


@router.get("/changes")
def list_changes(request: Request, article: str | None = None, status: str = STATUS) -> dict[str, Any]:
    ctx = _ctx(request)
    key = article.strip().upper() if article else None
    rows = planning.changes(ctx.db, key, None if status == "all" else status)
    order = None
    if key:
        article = ctx.db.article(key)
        order = ctx.db.order(article["order_key"]) if article else None
    return {
        "changes": _public_changes(ctx, rows),
        "workers": ctx.db.workers(),
        "milestones": [{"id": m["id"], "name": m["name"], "done": bool(m["done"])} for m in ctx.db.milestones()],
        "order_title": order["title"] if order else None,
        "exists": order is not None,  # False: neue Nummer, die erste Änderung legt den Auftrag an
    }


def _change(ctx: AppContext, change_id: int) -> dict[str, Any]:
    rows = [r for r in planning.changes(ctx.db, ctx.db.change(change_id)["article_key"]) if r["id"] == change_id]
    return _public_changes(ctx, rows)[0]


@router.post("/changes", status_code=201)
def create_change(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    ctx = _ctx(request)
    with _errors():
        change_id = planning.create_change(ctx.db, payload, time.time())
    return _change(ctx, change_id)


@router.put("/changes/{change_id}")
def update_change(request: Request, change_id: int, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    ctx = _ctx(request)
    with _errors():
        planning.update_change(ctx.db, change_id, payload, time.time())
    return _change(ctx, change_id)


@router.delete("/changes/{change_id}", status_code=204)
def delete_change(request: Request, change_id: int) -> Response:
    ctx = _ctx(request)
    with _errors():
        images = planning.delete_change(ctx.db, change_id)
    for image in images:
        ctx.change_images.remove_files(image)
    return Response(status_code=204)


@router.put("/changes/{change_id}/images", status_code=201)
async def upload_change_image(
    request: Request, change_id: int, x_image_length: str | None = Header(None)
) -> dict[str, Any]:
    """Ein Bild mehr zur Änderung: großes Bild und Vorschaubild in *einem* Rumpf, wie beim Auftragsbild."""
    ctx = _ctx(request)
    if ctx.db.change(change_id) is None:
        raise HTTPException(404, "Unbekannte Änderung")
    image, thumb = await _read_upload(request, x_image_length)
    try:
        ctx.change_images.add(change_id, image, thumb)
    except ImageError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(404, "Unbekannte Änderung") from exc
    return _change(ctx, change_id)


@router.delete("/images/{image_id}", status_code=204)
def delete_change_image(request: Request, image_id: int) -> Response:
    try:
        _ctx(request).change_images.delete(image_id)
    except LookupError as exc:
        raise HTTPException(404, "Unbekanntes Bild") from exc
    return Response(status_code=204)


@router.get("/images/{image_id}")
def change_image(request: Request, image_id: int, size: str = Query("full", pattern="^(full|thumb)$")) -> FileResponse:
    ctx = _ctx(request)
    row = ctx.db.change_image(image_id)
    path = ctx.change_images.path(row["image"], size) if row else None
    if path is None:
        raise HTTPException(404, "Kein Bild")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=31536000, immutable"})
