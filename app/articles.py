"""Artikel: je Auftrag und Version (bei Felgen je Nummer) ein Artikel mit kaufmännischen Daten.

Die Artikel entstehen automatisch aus den erfassten Daten (``sync``): Jede Version, die in Läufen oder
Planzeiten eines Auftrags vorkommt, wird ein Artikel – ``21053``, ``21053V1``, ``21053V2``; eine Felge
ist ``10101018``. Umgekehrt legt ein von Hand angelegter Artikel den Auftrag an (``create``).

Je Artikel rechnet ``list_articles``:

- **Laufzeit je Teil:** Ø-Laufzeiten aller Programme der Version über alle Aufspannungen (ohne
  Vorrichtung). Fehlt einem Programm die Ø-Zeit, gilt seine CAM-Planzeit („geschätzt“).
- **Rohling:** Material (Liste mit Dichte und Preis je kg) und Maße – Block L×B×H oder Rund Ø×L in mm.
  Gewicht = Volumen in Litern × Dichte (g/cm³ = kg/l), Materialpreis = Gewicht × Preis je kg.
- **Fräsen:** je Programm Laufzeit je Teil × Stundensatz seiner Maschine (lief es auf mehreren,
  gewichtet nach Laufzeit). Ohne Lauf: Maschine aus der Tebis-Doku, sonst der Durchschnitt aller
  eingetragenen Stundensätze („geschätzt“).
- **Herstellkosten** = Material + Fräsen, **Marge** = VK − Herstellkosten.
- Fertige Teile, Gesamtlaufzeit und letzte Produktion (letzter fertiger Lauf des letzten Programms
  der letzten Aufspannung) kommen aus dem Auftrag.
"""

from __future__ import annotations

import io
import math
import re
import time
from collections import defaultdict
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from . import orders
from .db import Database
from .nc_program import call_name

SHAPES = {"block": "Block", "round": "Rund"}
DIM_MAX_MM = 5000.0
PRICE_MAX = 10_000_000.0
NOTE_MAX = 500
MATERIAL_NAME_MAX = 60
DENSITY_MAX = 25.0  # g/cm³ (Osmium hat 22,6)

_ORDER_KEY = re.compile(r"(\d{4,5})(V\d{1,2})?")
_RIM_KEY = re.compile(r"1\d{7}")


class ArticleError(ValueError):
    """Ungültige Eingabe (Text für die Oberfläche)."""


def article_key(order_key: str, version: str) -> str:
    return f"{order_key}{version}"


def parse_key(text: str) -> tuple[str, str, str]:
    """Artikelnummer → (Auftrag, Version, Art). ``21060``, ``21060v1`` → ("21060", "V1", "order"),
    ``10101018`` → ("10101018", "", "rim")."""
    value = re.sub(r"\s+", "", text or "").upper()
    if _RIM_KEY.fullmatch(value):
        return value, "", "rim"
    match = _ORDER_KEY.fullmatch(value)
    if match:
        return match[1], match[2] or "", "order"
    raise ArticleError(
        f"„{text}“ ist keine Artikelnummer. Beispiele: 21060, 21060V1 (Version) oder 10101018 (Felge)."
    )


# --- Abgleich ------------------------------------------------------------------------------


def sync(db: Database, now: float | None = None) -> int:
    """Für jede Version eines Auftrags, die in Läufen oder Planzeiten vorkommt, einen Artikel anlegen.
    Liefert die Zahl der neuen Artikel. Idempotent."""
    now = time.time() if now is None else now
    kinds = {o["key"]: o["kind"] for o in db.orders()}
    created = 0
    with db.transaction():
        for row in db.order_versions():
            code = orders.parse_program(row["program"])
            if code is None or row["order_key"] not in kinds:
                continue
            version = code.version if kinds[row["order_key"]] == "order" else ""
            created += db.ensure_article(article_key(row["order_key"], version), row["order_key"], version, now)
    return created


def create(db: Database, text: str, now: float | None = None) -> dict[str, Any]:
    """Artikel von Hand anlegen; legt den Auftrag bzw. die Felge mit an, falls es ihn noch nicht gibt."""
    now = time.time() if now is None else now
    order_key, version, kind = parse_key(text)
    key = article_key(order_key, version)
    if db.article(key) is not None:
        raise ArticleError(f"Den Artikel {key} gibt es schon.")
    year = 0 if kind == "rim" else datetime.now().year
    with db.transaction():
        order_created = db.ensure_order(order_key, year, order_key, now, kind=kind) == "created"
        db.ensure_article(key, order_key, version, now, manual=True)
    return {"key": key, "order_key": order_key, "version": version, "kind": kind, "order_created": order_created}


# --- Eingaben prüfen -------------------------------------------------------------------------


def _number(value: Any, label: str, maximum: float, positive: bool = False) -> float | None:
    """Zahl mit Dezimalkomma; leer = None."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    text = str(value).strip().replace("€", "").replace(" ", "")
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        raise ArticleError(f"{label}: bitte eine Zahl eintragen, z. B. 12,50.") from None
    if not math.isfinite(number) or number < 0 or (positive and number == 0) or number > maximum:
        raise ArticleError(f"{label}: bitte eine Zahl von {'über ' if positive else ''}0 bis {maximum:,.0f} eintragen.".replace(",", "."))
    return number


def validate_article(db: Database, payload: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Felder für ``db.update_article``; fehlt ein Schlüssel, bleibt der bisherige Wert."""
    fields = {k: current[k] for k in ("material_id", "shape", "dim_a", "dim_b", "dim_c", "price_ek", "price_vk", "note")}
    if "material_id" in payload:
        value = payload["material_id"]
        if value in (None, ""):
            fields["material_id"] = None
        else:
            try:
                material_id = int(value)
            except (TypeError, ValueError):
                raise ArticleError("Unbekanntes Material.") from None
            if db.material(material_id) is None:
                raise ArticleError("Unbekanntes Material.")
            fields["material_id"] = material_id
    if "shape" in payload:
        shape = payload["shape"] or None
        if shape is not None and shape not in SHAPES:
            raise ArticleError("Form: Block oder Rund.")
        fields["shape"] = shape
    labels = {"dim_a": "Länge bzw. Durchmesser", "dim_b": "Breite bzw. Länge", "dim_c": "Höhe"}
    for name, label in labels.items():
        if name in payload:
            fields[name] = _number(payload[name], f"{label} (mm)", DIM_MAX_MM, positive=True)
    for name, label in (("price_ek", "Preis EK"), ("price_vk", "Preis VK")):
        if name in payload:
            fields[name] = _number(payload[name], label, PRICE_MAX)
    if "note" in payload:
        note = str(payload["note"] or "").strip()
        if len(note) > NOTE_MAX:
            raise ArticleError(f"Die Notiz darf höchstens {NOTE_MAX} Zeichen lang sein.")
        fields["note"] = note
    if fields["shape"] == "round":
        fields["dim_c"] = None  # Rund: Ø und Länge
    return fields


def validate_material(payload: dict[str, Any]) -> tuple[str, float | None, float | None]:
    """(Name, Dichte in g/cm³, Preis je kg)."""
    name = str(payload.get("name") or "").strip()
    if not name:
        raise ArticleError("Bitte einen Namen für das Material eintragen, z. B. Alu 7075.")
    if len(name) > MATERIAL_NAME_MAX:
        raise ArticleError(f"Der Name darf höchstens {MATERIAL_NAME_MAX} Zeichen lang sein.")
    density = _number(payload.get("density"), "Dichte (g/cm³)", DENSITY_MAX, positive=True)
    return name, density, _number(payload.get("price_per_kg"), "Preis je kg", PRICE_MAX)


# --- Rechnen ---------------------------------------------------------------------------------


def volume_l(shape: str | None, a: float | None, b: float | None, c: float | None) -> float | None:
    """Volumen des Rohlings in Litern (Maße in mm)."""
    if shape == "block" and a and b and c:
        return a * b * c / 1e6
    if shape == "round" and a and b:
        return math.pi * (a / 2) ** 2 * b / 1e6
    return None


def _mm(value: float) -> str:
    return f"{value:g}".replace(".", ",")


def blank_text(material: dict[str, Any] | None, shape: str | None, a: float | None, b: float | None, c: float | None) -> str:
    """„Alu 7075 · Block 120 × 80 × 40 mm“ bzw. „Rund Ø 80 × 120 mm“."""
    size = None
    if shape == "block" and a and b and c:
        size = f"Block {_mm(a)} × {_mm(b)} × {_mm(c)} mm"
    elif shape == "round" and a and b:
        size = f"Rund Ø {_mm(a)} × {_mm(b)} mm"
    return " · ".join(p for p in (material["name"] if material else None, size) if p)


def _rate(
    name: str,
    machine_time: dict[str, dict[str, float]],
    plan_machine: str | None,
    machines: list[dict[str, Any]],
) -> tuple[float | None, bool]:
    """Stundensatz für ein Programm: (€/h, geschätzt)."""
    rates = {m["id"]: m["hourly_rate"] for m in machines if m["hourly_rate"] is not None}
    seconds = {mid: s for mid, s in machine_time.get(name, {}).items() if mid in rates and s > 0}
    if seconds:
        return sum(rates[mid] * s for mid, s in seconds.items()) / sum(seconds.values()), False
    if plan_machine:
        hits = [m for m in machines if m["hourly_rate"] is not None and m["name"].lower() in plan_machine.lower()]
        if len(hits) == 1:
            return hits[0]["hourly_rate"], False
    if rates:
        return sum(rates.values()) / len(rates), True
    return None, False


def _version_row(
    version: dict[str, Any] | None,
    machine_time: dict[str, dict[str, float]],
    plans: dict[str, dict[str, Any]],
    machines: list[dict[str, Any]],
    last_finished: dict[str, float],
) -> dict[str, Any]:
    """Laufzeit je Teil, Fräsen, Stückzahl, Laufzeit und letzte Produktion einer Version."""
    setups = [s for s in (version or {}).get("setups", []) if not s["fixture"]]
    programs = [p for s in setups for p in s["programs"]]
    part_s: float | None = 0.0 if programs else None
    part_estimated = False
    mill: float | None = 0.0 if programs else None
    mill_estimated = False
    for p in programs:
        seconds = p["avg_run_s"] if p["avg_run_s"] is not None else p["plan_s"]
        if p["avg_run_s"] is None and p["plan_s"] is not None:
            part_estimated = True
        if seconds is None:
            part_s = mill = None
            break
        part_s += seconds
        plan = plans.get(p["call_name"])
        rate, estimated = _rate(p["call_name"], machine_time, plan["machine"] if plan else None, machines)
        if rate is None:
            mill = None
        elif mill is not None:
            mill += seconds / 3600 * rate
            mill_estimated = mill_estimated or estimated
    last = None
    if setups:
        final = max(setups, key=lambda s: s["setup"])["programs"][-1]
        last = last_finished.get(final["call_name"])
    return {
        "part_s": part_s,
        "part_estimated": part_estimated,
        "mill_cost": round(mill, 2) if mill is not None else None,
        "mill_estimated": mill_estimated,
        "parts": (version or {}).get("parts", 0),
        "running_s": (version or {}).get("running_s", 0.0),
        "last_production": last,
    }


def list_articles(db: Database, tz: ZoneInfo, status: str = "all") -> list[dict[str, Any]]:
    """Alle Artikel mit Rohling, Kosten, Preisen und Produktionsdaten (je Auftrag einmal ausgewertet)."""
    sync(db)
    order_rows = {o["key"]: o for o in db.orders(status)}
    materials = {m["id"]: m for m in db.materials()}
    machines = db.machines(include_removed=True)
    by_order: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for art in db.articles():
        if art["order_key"] in order_rows:
            by_order[art["order_key"]].append(art)
    rows = []
    for order_key, arts in by_order.items():
        detail = orders.order_detail(db, order_key, tz)
        if detail is None:
            continue
        machine_time: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
        for r in db.machine_run_time(order_key):
            machine_time[call_name(r["program"])][r["machine_id"]] += r["seconds"] or 0.0
        last_finished: dict[str, float] = {}
        for r in db.last_finished(order_key):
            name = call_name(r["program"])
            last_finished[name] = max(last_finished.get(name, 0.0), r["last"] or 0.0)
        plans = {p["name"]: p for p in db.plans_of_order(order_key)}
        versions = {v["version"]: v for v in detail["versions"]}
        order = detail["order"]
        for art in arts:
            material = materials.get(art["material_id"])
            volume = volume_l(art["shape"], art["dim_a"], art["dim_b"], art["dim_c"])
            density = material["density"] if material else None
            weight = volume * density if volume is not None and density is not None else None  # g/cm³ = kg/l
            price_kg = material["price_per_kg"] if material else None
            material_cost = round(weight * price_kg, 2) if weight is not None and price_kg is not None else None
            prod = _version_row(versions.get(art["version"]), machine_time, plans, machines, last_finished)
            known = [c for c in (material_cost, prod["mill_cost"]) if c is not None]
            cost = round(sum(known), 2) if known else None
            cost_complete = material_cost is not None and prod["mill_cost"] is not None
            vk = art["price_vk"]
            margin = round(vk - cost, 2) if vk is not None and cost is not None and cost_complete else None
            rows.append({
                "key": art["key"],
                "order_key": order_key,
                "version": art["version"],
                "kind": order["kind"],
                "title": order["title"],
                "rim": order.get("rim"),
                "status": order["status"],
                "image": order["image"],
                "manual": bool(art["manual"]),
                "material_id": art["material_id"],
                "material": material["name"] if material else None,
                "shape": art["shape"],
                "dim_a": art["dim_a"],
                "dim_b": art["dim_b"],
                "dim_c": art["dim_c"],
                "blank": blank_text(material, art["shape"], art["dim_a"], art["dim_b"], art["dim_c"]),
                "volume_l": volume,
                "density": density,
                "weight_kg": weight,
                "price_per_kg": price_kg,
                "material_cost": material_cost,
                **prod,
                "cost": cost,
                "cost_complete": cost_complete,
                "price_ek": art["price_ek"],
                "price_vk": vk,
                "margin": margin,
                "margin_pct": round(margin / vk * 100, 1) if margin is not None and vk else None,
                "note": art["note"],
            })
    return sorted(rows, key=lambda r: (r["order_key"], orders.version_order(r["version"])))


# --- Excel-Export ------------------------------------------------------------------------------

EUR = '#,##0.00 "€"'
DURATION = "[h]:mm"
COLUMNS = [
    # (Überschrift, Schlüssel bzw. Funktion, Format, Breite)
    ("Artikel", "key", "@", 12),
    ("Art", lambda r: "Felge" if r["kind"] == "rim" else "Auftrag", "@", 9),
    ("Bezeichnung", lambda r: r["title"] or (r["rim"] or {}).get("label") or "", "@", 32),
    ("Status", lambda r: "abgeschlossen" if r["status"] == "closed" else "offen", "@", 13),
    ("Material", "material", "@", 16),
    ("Rohling", lambda r: blank_text(None, r["shape"], r["dim_a"], r["dim_b"], r["dim_c"]), "@", 26),
    ("Volumen (l)", "volume_l", "0.000", 10),
    ("Dichte (g/cm³)", "density", "0.00", 10),
    ("Gewicht (kg)", "weight_kg", "0.000", 11),
    ("Preis je kg", "price_per_kg", EUR, 12),
    ("Materialpreis", "material_cost", EUR, 13),
    ("Laufzeit je Teil", lambda r: r["part_s"] / 86400 if r["part_s"] is not None else None, DURATION, 14),
    ("Preis Fräsen", "mill_cost", EUR, 13),
    ("Herstellkosten", "cost", EUR, 14),
    ("Preis EK", "price_ek", EUR, 12),
    ("Preis VK", "price_vk", EUR, 12),
    ("Marge", "margin", EUR, 12),
    ("Marge %", lambda r: r["margin_pct"] / 100 if r["margin_pct"] is not None else None, "0.0%", 9),
    ("Stück produziert", "parts", "0", 10),
    ("Gesamtlaufzeit", lambda r: r["running_s"] / 86400, DURATION, 13),
    ("Letzte Produktion", "last_production", "DD.MM.YYYY", 14),
    ("Hinweise", "hints", "@", 30),
    ("Notiz", "note", "@", 30),
]
SUM_COLUMNS = {"Stück produziert", "Gesamtlaufzeit"}


def hints(row: dict[str, Any]) -> str:
    notes = []
    if row["part_estimated"]:
        notes.append("Laufzeit teils aus CAM-Planzeit")
    if row["mill_estimated"]:
        notes.append("Stundensatz geschätzt (Durchschnitt)")
    if row["part_s"] is not None and row["mill_cost"] is None:
        notes.append("Stundensatz fehlt")
    return "; ".join(notes)


def export_xlsx(rows: list[dict[str, Any]], materials: list[dict[str, Any]], tz: ZoneInfo, now: float) -> bytes:
    """Artikelliste als formatierte Excel-Datei: Kopf fett und fixiert, Filter, €- und Zeitformate,
    Summenzeile; zweites Blatt mit der Materialliste."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "Artikel"
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="2F4F6F")
    thin = Side(style="thin", color="B4B2A9")
    ws.append([c[0] for c in COLUMNS])
    for i, (title, _, _, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=i)
        cell.font, cell.fill = head_font, head_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 30

    def value(row: dict[str, Any], getter: Any) -> Any:
        if getter == "hints":
            return hints(row) or None
        v = getter(row) if callable(getter) else row.get(getter)
        if getter == "last_production" and v is not None:
            return datetime.fromtimestamp(v, tz).replace(tzinfo=None)
        return v

    for r_index, row in enumerate(rows, start=2):
        for c_index, (_, getter, fmt, _) in enumerate(COLUMNS, start=1):
            cell = ws.cell(row=r_index, column=c_index, value=value(row, getter))
            cell.number_format = fmt
    last = len(rows) + 1
    if rows:
        total = last + 1
        ws.cell(row=total, column=1, value="Summe").font = Font(bold=True)
        for c_index, (title, _, fmt, _) in enumerate(COLUMNS, start=1):
            if title in SUM_COLUMNS:
                letter = get_column_letter(c_index)
                cell = ws.cell(row=total, column=c_index, value=f"=SUM({letter}2:{letter}{last})")
                cell.number_format, cell.font = fmt, Font(bold=True)
            ws.cell(row=total, column=c_index).border = Border(top=thin)
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{last}"
    ws.sheet_view.zoomScale = 100

    ms = wb.create_sheet("Materialien")
    ms.append(["Material", "Dichte (g/cm³)", "Preis je kg", "Artikel"])
    for i, width in enumerate((24, 14, 14, 10), start=1):
        cell = ms.cell(row=1, column=i)
        cell.font, cell.fill = head_font, head_fill
        ms.column_dimensions[get_column_letter(i)].width = width
    for m in materials:
        ms.append([m["name"], m["density"], m["price_per_kg"], m["articles"]])
        ms.cell(row=ms.max_row, column=2).number_format = "0.00"
        ms.cell(row=ms.max_row, column=3).number_format = EUR
    ms.freeze_panes = "A2"

    wb.properties.creator = "LD-Machine-Viewer"
    wb.properties.created = datetime.fromtimestamp(now, tz).replace(tzinfo=None)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
