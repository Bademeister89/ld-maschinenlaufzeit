"""API für den Tab „Artikel“: Liste mit Kosten und Preisen, Artikel anlegen, bearbeiten, entfernen,
Excel-Export. Materialliste und Stundensätze stehen in der Konfiguration."""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Body, HTTPException, Query, Request
from fastapi.responses import Response

from . import articles

if TYPE_CHECKING:
    from .main import AppContext

router = APIRouter(prefix="/api/articles")
log = logging.getLogger(__name__)

STATUS = Query("all", pattern="^(all|open|closed)$")


def _ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def _rows(ctx: AppContext, status: str, thumbs: dict[str, bytes] | None = None) -> list[dict[str, Any]]:
    """Artikel mit Bild-Adressen; ``thumbs`` (falls übergeben) bekommt je Artikel das Vorschaubild
    als JPEG für den Excel-Export."""
    files = ctx.order_images.files()
    rows = articles.list_articles(ctx.db, ctx.tz, status)
    for row in rows:
        # Vorschaubild des Auftrags (die Versionen teilen sich das Bild)
        image = row.pop("image")
        public = ctx.order_images.public({"key": row["order_key"], "image": image}, files)
        row["thumb_url"], row["image_url"] = public["thumb_url"], public["image_url"]
        path = ctx.order_images.path(image, "thumb") if thumbs is not None and public["thumb_url"] else None
        if path is not None:
            try:
                thumbs[row["key"]] = path.read_bytes()
            except OSError:
                pass  # Bild fehlt: Zeile ohne Bild
    return rows


@router.get("")
def list_articles(request: Request, status: str = STATUS) -> dict[str, Any]:
    ctx = _ctx(request)
    rows = _rows(ctx, status)
    rates = [m.hourly_rate for m in ctx.manager.machines()]
    return {
        "now": time.time(),
        "articles": rows,
        "materials": ctx.db.materials(),
        "rates_missing": not any(r is not None for r in rates),
    }


@router.get("/export.xlsx")
async def export(request: Request, status: str = STATUS) -> Response:
    ctx = _ctx(request)
    thumbs: dict[str, bytes] = {}
    rows = _rows(ctx, status, thumbs)
    now = time.time()
    data = await asyncio.to_thread(articles.export_xlsx, rows, ctx.db.materials(), ctx.tz, now, thumbs)
    name = f"artikel_{datetime.fromtimestamp(now, ctx.tz):%Y-%m-%d}.xlsx"
    return Response(
        data,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.post("", status_code=201)
def create_article(request: Request, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Artikel von Hand anlegen (``{"key": "21060V1"}``); legt den Auftrag bzw. die Felge mit an."""
    ctx = _ctx(request)
    try:
        result = articles.create(ctx.db, str(payload.get("key") or ""))
    except articles.ArticleError as exc:
        raise HTTPException(400, str(exc)) from None
    log.info("Artikel %s angelegt%s", result["key"], " (mit Auftrag)" if result["order_created"] else "")
    return result


@router.put("/{key}")
def update_article(request: Request, key: str, payload: dict[str, Any] = Body(...)) -> dict[str, Any]:
    """Rohling (Material, Form, Maße), EK, VK und Notiz; fehlende Felder bleiben unverändert."""
    ctx = _ctx(request)
    current = ctx.db.article(key)
    if current is None and articles.sync(ctx.db):  # neue Version seit dem letzten Abgleich?
        current = ctx.db.article(key)
    if current is None:
        raise HTTPException(404, f"Unbekannter Artikel: {key}")
    try:
        fields = articles.validate_article(ctx.db, payload, current)
    except articles.ArticleError as exc:
        raise HTTPException(400, str(exc)) from None
    ctx.db.update_article(key, fields, time.time())
    return next((r for r in _rows(ctx, "all") if r["key"] == key), {"key": key})


@router.delete("/{key}", status_code=204)
def delete_article(request: Request, key: str) -> Response:
    """Stammdaten des Artikels entfernen. Hat der Auftrag Läufe oder Planzeiten dieser Version, legt der
    nächste Abgleich den Artikel wieder an – ohne Rohling und Preise."""
    ctx = _ctx(request)
    if not ctx.db.delete_article(key):
        raise HTTPException(404, f"Unbekannter Artikel: {key}")
    log.info("Artikel %s entfernt", key)
    return Response(status_code=204)
