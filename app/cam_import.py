"""CAM-Planzeiten aus der Tebis-Dokumentation (PDF „Aufspannplan / Programmablauf“).

Je Aufspannung erzeugt Tebis eine PDF, z. B. ``doku_sp_2.pdf``::

    02 DMU 70 Erowa 11:10 Uhr                      ← Spannung, Maschine, Uhrzeit
    CAD-Datei: 21053 abdeckung rechts 1 cvo.cad
    Gesamtlaufzeit: 03:03:60                        ← Tebis schreibt auch 60 Sekunden
    Programmablauf
    26-21053-02-01 Abgearbeitet ?  (
     ) 03:00:15                                     ← Programm und seine Planzeit
    0,30 mm T019 SF 40R2 GL150 - … 19 00:04:24      ← Werkzeuge (hier nicht ausgewertet)

Der Text kommt über pypdf aus der PDF; ausgewertet wird er in ``parse_cam_doc`` (ohne PDF testbar).
Nur Programme, deren Name einem Auftrag oder einer Felge zugeordnet werden kann, zählen.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from typing import Any

from .db import Database
from .nc_program import call_name
from .orders import parse_program

MAX_PDF_BYTES = 10 * 1024 * 1024

_HEADER = re.compile(r"^\s*(?P<setup>\d{1,2})\s+(?P<machine>\S.*?)\s+\d{1,2}:\d{2}\s*Uhr\s*$", re.MULTILINE)
_CAD = re.compile(r"^\s*CAD-Datei:\s*(?P<cad>\S.*?)\s*$", re.MULTILINE)
_TOTAL = re.compile(r"Gesamtlaufzeit:\s*(?P<time>\d{1,3}:\d{2}:\d{2})")
# Programmzeile: Name (auch mit Leerzeichen, z. B. „10101018-01 tasche“) vor „Abgearbeitet“, die
# Planzeit folgt nach „? ( )“ – oft in der nächsten Zeile
_PROGRAM = re.compile(r"^\s*(?P<name>\S.*?)\s+Abgearbeitet\b[^\d]*?(?P<time>\d{1,3}:\d{2}:\d{2})", re.MULTILINE)
_ORDER_PREFIX = re.compile(r"^\s*\d{4,5}\S*[\s_-]+")


class CamImportError(ValueError):
    """PDF nicht lesbar oder keine Tebis-Doku; der Text erscheint in der Oberfläche."""


@dataclass
class CamDoc:
    setup: int | None = None
    machine: str | None = None
    cad_name: str | None = None
    total_s: float | None = None
    programs: list[tuple[str, float]] = field(default_factory=list)  # (Programmname, Planzeit in s)
    ignored: list[str] = field(default_factory=list)  # Programme ohne Auftrags-/Felgennummer

    @property
    def title(self) -> str | None:
        """Bezeichnung aus dem CAD-Namen: „21053 abdeckung rechts 1 cvo“ → „Abdeckung rechts 1 cvo“."""
        if not self.cad_name:
            return None
        title = _ORDER_PREFIX.sub("", self.cad_name).strip()
        return (title[:1].upper() + title[1:]) if title else None


def seconds(text: str) -> float:
    """``03:00:15`` → 10815; auch ``03:03:60`` (Tebis) wird einfach zusammengezählt."""
    hours, minutes, secs = (int(part) for part in text.split(":"))
    return float(hours * 3600 + minutes * 60 + secs)


def pdf_text(data: bytes) -> str:
    """Text aller Seiten der PDF."""
    if len(data) > MAX_PDF_BYTES:
        raise CamImportError("Die PDF ist größer als 10 MB.")
    if not data.startswith(b"%PDF"):
        raise CamImportError("Das ist keine PDF-Datei.")
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        raise CamImportError(f"Die PDF lässt sich nicht lesen ({exc}).") from exc


def parse_cam_doc(text: str) -> CamDoc:
    doc = CamDoc()
    if header := _HEADER.search(text):
        doc.setup, doc.machine = int(header["setup"]), header["machine"]
    if cad := _CAD.search(text):
        doc.cad_name = re.sub(r"\.cad$", "", cad["cad"], flags=re.IGNORECASE)
    if total := _TOTAL.search(text):
        doc.total_s = seconds(total["time"])
    seen: set[str] = set()
    for match in _PROGRAM.finditer(text):
        name = match["name"].strip()
        if name in seen:
            continue  # Seitenkopf wiederholt sich, das Programm nicht doppelt zählen
        seen.add(name)
        if parse_program(name) is None:
            doc.ignored.append(name)
        else:
            doc.programs.append((name, seconds(match["time"])))
    if not doc.programs:
        raise CamImportError(
            "In der PDF steht kein Programm mit Auftrags- oder Felgennummer (Tebis-Doku „Programmablauf“?)."
        )
    return doc


def read_cam_pdf(data: bytes) -> CamDoc:
    return parse_cam_doc(pdf_text(data))


def parse_plan_time(text: str) -> float:
    """Planzeit von Hand: Stunden mit Komma oder Punkt („4,5“) oder „h:mm“ bzw. „h:mm:ss“ („4:30“).
    Liefert Sekunden; ValueError bei unlesbarer oder nicht positiver Angabe."""
    text = text.strip().lower().removesuffix("h").strip()
    if re.fullmatch(r"\d{1,3}:\d{1,2}(:\d{1,2})?", text):
        parts = [int(p) for p in text.split(":")] + [0]
        value = parts[0] * 3600 + parts[1] * 60 + parts[2]
    else:
        value = float(text.replace(",", ".")) * 3600
    if not 0 < value <= 1000 * 3600:
        raise ValueError(text)
    return float(round(value))


def apply_cam_doc(db: Database, doc: CamDoc, file: str | None, now: float) -> dict[str, Any]:
    """Aufträge bzw. Felgen der Programme anlegen (falls neu), Planzeiten speichern und eine leere
    Bezeichnung aus dem CAD-Namen füllen."""
    result: dict[str, dict[str, Any]] = {}
    with db.transaction():
        for name, planned_s in doc.programs:
            code = parse_program(name)
            assert code is not None  # parse_cam_doc lässt nur zuordenbare Programme durch
            entry = result.get(code.key)
            if entry is None:
                created = db.ensure_order(code.key, code.year, code.order, now, kind=code.kind) == "created"
                title_set = bool(doc.title) and db.set_order_title_if_empty(code.key, doc.title)
                entry = result[code.key] = {
                    "key": code.key, "kind": code.kind, "created": created, "title_set": title_set, "programs": [],
                }
            db.set_plan(call_name(name), name, code.key, planned_s, "pdf", now, file, doc.machine)
            entry["programs"].append({"program": name, "planned_s": planned_s})
    return {
        "setup": doc.setup,
        "machine": doc.machine,
        "total_s": doc.total_s,
        "orders": list(result.values()),
        "ignored": doc.ignored,
    }
