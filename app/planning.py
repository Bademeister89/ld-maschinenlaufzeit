"""Planung (Board mit Aufgaben je Meilenstein) und Änderungsliste (A01, A02 … je Artikel).

- Die Spalten des Boards sind die **Meilensteine** (Offen, Design, CAD, CAM, Fräsen, Fertig), in der
  Konfiguration pflegbar. Genau einer ist der „Fertig“-Meilenstein.
- Eine **Aufgabe** hat Titel, Notiz, optional Artikel, Bearbeiter, Priorität (hoch/normal/niedrig) und
  Termin. Ihre Reihenfolge in der Spalte ist die Priorität beim Abarbeiten (oben zuerst).
- Auftragsnummern werden fortlaufend vergeben: Eine neue Nummer (oder Version) an einer Aufgabe oder
  Änderung legt Auftrag und Artikel an (``articles.create``); ihre Programme buchen später darauf.
- Eine **Änderung** gehört zu genau einem Artikel (21053V1 hat eine eigene Liste neben 21053) und
  bekommt die nächste Nummer: A01, A02 … Beim Anlegen entsteht dazu eine Aufgabe oben in der ersten
  Spalte. Beide bleiben gleich: Aufgabe erledigt („Fertig“-Meilenstein, nicht im Board) → Änderung
  umgesetzt, wieder geöffnet → offen; die Änderung von Hand umsetzen oder öffnen verschiebt die Aufgabe.
- Eine Änderung kann an einer **Spannung** hängen (``setup``, sonst alle). Zur Wahl stehen die eigenen
  Spannungen des Artikels ohne Vorrichtung (08/09); die von einer Version übernommene Spannung der
  Grundversion gehört zur Grundversion (``article_setups``).
- Läuft ein Programm eines Artikels mit offener Änderung, zeigt die Live-Ansicht einen Alarm
  (``open_changes``) – bei einer Änderung an einer Spannung nur für Programme dieser Spannung.
- Startet ein Programm eines Auftrags, rücken seine Aufgaben aus den Spalten davor in die Spalte
  „Programmlauf“ (Standard: Fräsen; ``on_run_started``).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from typing import Any

from .db import Database

# Meilensteine beim ersten Start (Name, fertig, Programmlauf); danach in der Konfiguration pflegbar
MILESTONES = [
    ("Offen", False, False), ("Design", False, False), ("CAD", False, False), ("CAM", False, False),
    ("Fräsen", False, True), ("Fertig", True, False),
]
PRIORITIES = {1: "hoch", 2: "normal", 3: "niedrig"}
TITLE_MAX = 120
NOTE_MAX = 1000
DESCRIPTION_MAX = 500
NAME_MAX = 40  # Meilenstein, Bearbeiter
STATUSES = ("open", "done")
# Artikelschlüssel: Auftrag (4–5 Ziffern) mit optionaler Version, oder Felge (8 Ziffern)
_ARTICLE_KEY = re.compile(r"(?P<order>[0-9]{4,5})(?P<version>V[0-9]{1,2})?|(?P<rim>1[0-9]{7})")


class PlanningError(ValueError):
    """Ungültige Eingabe; der Text erscheint in der Oberfläche. ``status``: 400, 404 oder 409."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def change_label(number: int) -> str:
    return f"A{number:02d}"


def _text(payload: dict[str, Any], field: str, limit: int, label: str, required: bool = False) -> str:
    value = payload.get(field)
    value = "" if value is None else str(value).strip()
    if required and not value:
        raise PlanningError(f"Bitte {label} eintragen.")
    if len(value) > limit:
        raise PlanningError(f"{label} darf höchstens {limit} Zeichen lang sein.")
    return value


def _worker_id(db: Database, value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        worker_id = int(value)
    except (TypeError, ValueError):
        raise PlanningError("Unbekannter Bearbeiter.") from None
    if db.worker(worker_id) is None:
        raise PlanningError("Unbekannter Bearbeiter.")
    return worker_id


def _article_key(db: Database, value: Any, required: bool = False) -> str | None:
    key = "" if value is None else str(value).strip().upper()
    if not key:
        if required:
            raise PlanningError("Bitte einen Artikel angeben.")
        return None
    from . import articles  # hier, articles nutzt planning (Bezeichnung A01)

    try:
        order_key, version, _ = articles.parse_key(key)
    except articles.ArticleError as exc:
        raise PlanningError(str(exc)) from None
    key = articles.article_key(order_key, version)
    if db.article(key) is None:
        # Auftragsnummern werden fortlaufend vergeben: eine neue Nummer (oder Version) wird hier mit
        # angelegt, wie im Tab Artikel. Laufen später ihre Programme, buchen sie auf diesen Auftrag.
        articles.create(db, key)
    return key


def article_setups(db: Database, article_key: str) -> dict[str, Any]:
    """Spannungen eines Artikels zur Wahl an einer Änderung, ermittelt wie im Auftragsdetail aus den
    Programmen (gelaufen oder mit Planzeit): ``setups`` die eigenen ohne Vorrichtung (08/09);
    ``inherited`` die von der Grundversion übernommenen (Version, z. B. Spannung 1 bei 21053V1) – sie
    gehören zur Grundversion. ``base``: Artikel der Grundversion; ``label``: „Spannung“ bei Felgen."""
    from . import articles, orders  # hier, beide nutzen planning

    order_key, version, kind = articles.parse_key(article_key)
    found: dict[str, set[int]] = defaultdict(set)
    for program in db.order_program_names(order_key):
        code = orders.parse_program(program)
        if code is None or code.key != order_key or (kind == "order" and code.setup in orders.FIXTURE_SETUPS):
            continue
        found[code.version].add(code.setup)
    own = sorted(found.get(version, ()))
    inherited = sorted(s for s in found.get("", ()) if s < min(own)) if version and own else []
    return {
        "setups": own, "inherited": inherited, "base": order_key, "kind": kind,
        "label": "Spannung" if kind == "rim" else "Aufspannung",
    }


def _setup(db: Database, article_key: str, value: Any) -> int | None:
    """Spannung einer Änderung prüfen (leer = alle Spannungen)."""
    if value in (None, ""):
        return None
    from . import orders

    try:
        setup = int(value)
    except (TypeError, ValueError):
        raise PlanningError("Ungültige Spannung.") from None
    info = article_setups(db, article_key)
    if setup in info["inherited"]:
        raise PlanningError(
            f"{info['label']} {setup} ist die gemeinsame der Grundversion {info['base']}. "
            f"Bitte die Änderung bei {info['base']} anlegen."
        )
    if not 1 <= setup <= 99 or (info["kind"] == "order" and setup in orders.FIXTURE_SETUPS):
        raise PlanningError("Ungültige Spannung.")
    return setup


def _due_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value)).isoformat()
    except ValueError:
        raise PlanningError("Ungültiger Termin.") from None


def _priority(value: Any) -> int:
    try:
        priority = int(value if value not in (None, "") else 2)
    except (TypeError, ValueError):
        priority = 0
    if priority not in PRIORITIES:
        raise PlanningError("Ungültige Priorität.")
    return priority


def _apply_order_title(db: Database, article_key: str | None, payload: dict[str, Any]) -> None:
    """Auftragsname (``order_title``) als Bezeichnung des Auftrags speichern – nur wenn er mitgeschickt
    wird; leer löscht ihn. Der Name gilt für den Auftrag, also für alle seine Versionen."""
    if article_key is None or "order_title" not in payload:
        return
    from . import articles

    try:
        title = articles.order_title(payload["order_title"])
    except articles.ArticleError as exc:
        raise PlanningError(str(exc)) from None
    db.set_order_title(articles.parse_key(article_key)[0], title)


def validate_task(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    """Eingaben prüfen; der Artikel zuletzt, weil er (neue Auftragsnummer) dabei angelegt wird. In
    einer Transaktion aufrufen, damit ein Fehler danach ihn wieder zurücknimmt."""
    fields = {
        "title": _text(payload, "title", TITLE_MAX, "einen Titel", required=True),
        "note": _text(payload, "note", NOTE_MAX, "Die Notiz"),
        "worker_id": _worker_id(db, payload.get("worker_id")),
        "priority": _priority(payload.get("priority")),
        "due_date": _due_date(payload.get("due_date")),
    }
    milestone = _milestone_of(db, payload.get("milestone_id"))
    fields["article_key"] = _article_key(db, payload.get("article_key"))
    _apply_order_title(db, fields["article_key"], payload)
    fields["milestone"] = milestone
    return fields


# --- Meilensteine -----------------------------------------------------------------------


def done_milestone(db: Database) -> dict[str, Any]:
    return next(m for m in db.milestones() if m["done"])


def first_milestone(db: Database) -> dict[str, Any]:
    """Spalte für neue Aufgaben: die erste, die nicht „Fertig“ ist."""
    milestones = db.milestones()
    return next((m for m in milestones if not m["done"]), milestones[0])


def _milestone_name(payload: dict[str, Any]) -> str:
    return _text(payload, "name", NAME_MAX, "einen Namen", required=True)


def add_milestone(db: Database, payload: dict[str, Any]) -> dict[str, Any]:
    name = _milestone_name(payload)
    with db.transaction():
        milestone_id = db.add_milestone(name, False)
        if milestone_id is None:
            raise PlanningError(f"„{name}“ ist schon in der Liste.", 409)
        # die Fertig-Spalte bleibt ganz rechts
        done = done_milestone(db)
        order = [m["id"] for m in db.milestones() if m["id"] != done["id"]] + [done["id"]]
        db.set_milestone_order(order)
        if payload.get("done"):
            _make_done(db, milestone_id)
    return db.milestone(milestone_id)


def update_milestone(db: Database, milestone_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    milestone = db.milestone(milestone_id)
    if milestone is None:
        raise PlanningError("Unbekannter Meilenstein.", 404)
    name = _milestone_name(payload)
    done = bool(payload.get("done", milestone["done"]))
    if milestone["done"] and not done:
        raise PlanningError("Ein Meilenstein muss „Erledigt“ sein. Bitte einen anderen als „Erledigt“ markieren.")
    with db.transaction():
        if not db.update_milestone(milestone_id, name, bool(milestone["done"])):
            raise PlanningError(f"„{name}“ ist schon in der Liste.", 409)
        if done and not milestone["done"]:
            _make_done(db, milestone_id)
        if "milling" in payload and bool(payload["milling"]) != bool(milestone["milling"]):
            if payload["milling"] and (done or milestone["done"]):
                raise PlanningError("Der „Erledigt“-Meilenstein kann nicht die Spalte für laufende Programme sein.")
            db.set_milling_milestone(milestone_id if payload["milling"] else None)
    return db.milestone(milestone_id)


def _make_done(db: Database, milestone_id: int) -> None:
    """Genau ein Meilenstein ist „Fertig“ (und dann nicht die Spalte für laufende Programme)."""
    for m in db.milestones():
        if bool(m["done"]) != (m["id"] == milestone_id):
            db.update_milestone(m["id"], m["name"], m["id"] == milestone_id)
        if m["id"] == milestone_id and m["milling"]:
            db.set_milling_milestone(None)


def reorder_milestones(db: Database, ids: list[int]) -> None:
    if sorted(ids) != sorted(m["id"] for m in db.milestones()):
        raise PlanningError("Die Liste der Meilensteine hat sich geändert. Bitte die Seite neu laden.", 409)
    db.set_milestone_order(ids)


def delete_milestone(db: Database, milestone_id: int) -> None:
    milestone = db.milestone(milestone_id)
    if milestone is None:
        raise PlanningError("Unbekannter Meilenstein.", 404)
    if milestone["done"]:
        raise PlanningError("Den „Erledigt“-Meilenstein kann man nicht entfernen. Erst einen anderen als „Erledigt“ markieren.", 409)
    if db.task_ids(milestone_id):
        raise PlanningError(f"In „{milestone['name']}“ liegen noch Aufgaben. Bitte erst verschieben.", 409)
    db.delete_milestone(milestone_id)


def worker_name(payload: dict[str, Any]) -> str:
    return _text(payload, "name", NAME_MAX, "einen Namen", required=True)


# --- Board und Aufgaben -------------------------------------------------------------------


def board(db: Database, today: date) -> dict[str, Any]:
    """Spalten mit ihren Aufgaben (Reihenfolge der Spalte); ``overdue``: Termin vorbei, nicht fertig."""
    milestones = db.milestones()
    columns = {m["id"]: {**m, "done": bool(m["done"]), "tasks": []} for m in milestones}
    for task in db.tasks():
        column = columns.get(task["milestone_id"])
        if column is None:
            continue
        column["tasks"].append(public_task(task, today, column["done"]))
    return {"milestones": list(columns.values()), "priorities": PRIORITIES}


def public_task(task: dict[str, Any], today: date, done: bool) -> dict[str, Any]:
    return {
        **task,
        "change": change_label(task["change_number"]) if task["change_number"] else None,
        "overdue": bool(task["due_date"]) and not done and task["due_date"] < today.isoformat(),
    }


def create_task(db: Database, payload: dict[str, Any], now: float) -> int:
    with db.transaction():
        fields = validate_task(db, payload)
        column = fields.pop("milestone") or first_milestone(db)
        task_id = db.insert_task(fields, column["id"], -10, now)  # oben einsortieren
        _place(db, task_id, column, 0, now)
    return task_id


def update_task(db: Database, task_id: int, payload: dict[str, Any], now: float) -> None:
    if db.task(task_id) is None:
        raise PlanningError("Unbekannte Aufgabe.", 404)
    with db.transaction():
        fields = validate_task(db, payload)
        fields.pop("milestone")  # Spalte wechselt nur über move_task
        db.update_task(task_id, fields, now)


def delete_task(db: Database, task_id: int) -> None:
    """Aufgabe entfernen. Eine verknüpfte Änderung bleibt in der Liste (mit ihrem Status)."""
    if not db.delete_task(task_id):
        raise PlanningError("Unbekannte Aufgabe.", 404)


def _milestone_of(db: Database, value: Any) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    try:
        milestone = db.milestone(int(value))
    except (TypeError, ValueError):
        milestone = None
    if milestone is None:
        raise PlanningError("Unbekannter Meilenstein.")
    return milestone


def move_task(db: Database, task_id: int, milestone_id: Any, index: Any, now: float) -> None:
    """Aufgabe an Stelle ``index`` (0 = oben) der Spalte ``milestone_id`` setzen. In der Fertig-Spalte
    gilt eine verknüpfte Änderung als umgesetzt, außerhalb als offen."""
    try:
        index = int(index)
    except (TypeError, ValueError):
        raise PlanningError("Ungültige Position.") from None
    with db.transaction():
        if db.task(task_id) is None:
            raise PlanningError("Unbekannte Aufgabe.", 404)
        column = _milestone_of(db, milestone_id)
        if column is None:
            raise PlanningError("Unbekannter Meilenstein.")
        _place(db, task_id, column, index, now)


def _place(db: Database, task_id: int, column: dict[str, Any], index: int, now: float) -> None:
    task = db.task(task_id)
    ids = [i for i in db.task_ids(column["id"]) if i != task_id]
    ids.insert(max(0, min(index, len(ids))), task_id)
    done = bool(column["done"])
    done_at = (task["done_at"] or now) if done else None
    db.place_tasks(column["id"], ids, task_id, done_at, now)
    if task["change_id"] is not None and (task["change_status"] == "done") != done:
        db.set_change_status(task["change_id"], "done" if done else "open", now if done else None)


def on_run_started(db: Database, order_key: str, version: str, now: float) -> list[int]:
    """Ein Programm des Auftrags ``order_key`` (Version ``version``) startet: Seine Aufgaben aus Spalten
    links der Programmlauf-Spalte rücken dorthin (oben). Aufgaben der Grundversion rücken bei jeder
    Version mit, Aufgaben einer Version nur bei dieser. Spalten rechts davon und „Fertig“ bleiben
    unberührt, sonst holte ein Nachlauf erledigte Aufgaben zurück. Liefert die verschobenen Aufgaben."""
    milestones = {m["id"]: m for m in db.milestones()}
    target = next((m for m in milestones.values() if m["milling"] and not m["done"]), None)
    if target is None:
        return []
    moved = []
    with db.transaction():
        for task in reversed(db.tasks()):  # von unten: die oberste landet wieder oben
            column = milestones.get(task["milestone_id"])
            match = _ARTICLE_KEY.fullmatch(task["article_key"] or "")
            if column is None or column["done"] or column["sort_order"] >= target["sort_order"] or match is None:
                continue
            task_order = match["rim"] or match["order"]
            task_version = match["version"] or ""
            if task_order == order_key and task_version in ("", version):
                _place(db, task["id"], target, 0, now)
                moved.append(task["id"])
    return moved


# --- Änderungen ------------------------------------------------------------------------------


def create_change(db: Database, payload: dict[str, Any], now: float) -> int:
    """Nächsten Änderungsstand anlegen und dazu eine Aufgabe oben in der gewählten Spalte
    (``milestone_id``, sonst die erste). In „Fertig“ gilt die Änderung gleich als umgesetzt."""
    description = _text(payload, "description", DESCRIPTION_MAX, "was geändert wird", required=True)
    worker_id = _worker_id(db, payload.get("worker_id"))
    due_date = _due_date(payload.get("due_date"))
    with db.transaction():
        column = _milestone_of(db, payload.get("milestone_id")) or first_milestone(db)
        article_key = _article_key(db, payload.get("article_key"), required=True)
        setup = _setup(db, article_key, payload.get("setup"))
        _apply_order_title(db, article_key, payload)
        change_id = db.insert_change(article_key, description, worker_id, now, setup)
        label = change_label(db.change(change_id)["number"])
        title = f"Änderung {label}: {description}"
        if len(title) > TITLE_MAX:
            title = title[: TITLE_MAX - 1] + "…"
        fields = {"title": title, "note": "", "article_key": article_key, "worker_id": worker_id,
                  "priority": 1, "due_date": due_date}
        task_id = db.insert_task(fields, column["id"], -10, now, change_id=change_id)
        _place(db, task_id, column, 0, now)
    return change_id


def update_change(db: Database, change_id: int, payload: dict[str, Any], now: float) -> None:
    """Text, Bearbeiter und Spannung ändern (``setup`` nur, wenn mitgeschickt); ``status`` (open/done)
    verschiebt die verknüpfte Aufgabe mit."""
    change = db.change(change_id)
    if change is None:
        raise PlanningError("Unbekannte Änderung.", 404)
    description = _text(payload, "description", DESCRIPTION_MAX, "was geändert wird", required=True)
    worker_id = _worker_id(db, payload.get("worker_id"))
    setup = _setup(db, change["article_key"], payload["setup"]) if "setup" in payload else change["setup"]
    status = payload.get("status", change["status"])
    if status not in STATUSES:
        raise PlanningError("Ungültiger Status.")
    with db.transaction():
        db.update_change(change_id, description, worker_id, setup)
        if status == change["status"]:
            return
        db.set_change_status(change_id, status, now if status == "done" else None)
        task = db.task_of_change(change_id)
        if task is not None:
            column = done_milestone(db) if status == "done" else first_milestone(db)
            index = len(db.task_ids(column["id"])) if status == "done" else 0
            _place(db, task["id"], column, index, now)


def delete_change(db: Database, change_id: int) -> list[str]:
    """Änderung (versehentlich angelegt) mit ihrer Aufgabe entfernen. Liefert die Bild-Dateinamen, die
    der Aufrufer löscht. Spätere Änderungen behalten ihre Nummer."""
    if db.change(change_id) is None:
        raise PlanningError("Unbekannte Änderung.", 404)
    with db.transaction():
        images = [r["image"] for r in db.change_images([change_id])]
        task = db.task_of_change(change_id)
        if task is not None:
            db.delete_task(task["id"])
        db.delete_change(change_id)  # Bilder-Einträge fallen mit (ON DELETE CASCADE)
    return images


def changes(db: Database, article_key: str | None = None, status: str | None = None) -> list[dict[str, Any]]:
    """Änderungen mit Bezeichnung (A01 …), verknüpfter Aufgabe und Bild-Dateinamen."""
    rows = db.changes(article_key, status)
    images: dict[int, list[dict[str, Any]]] = {}
    for image in db.change_images([r["id"] for r in rows]):
        images.setdefault(image["change_id"], []).append(image)
    for row in rows:
        row["label"] = change_label(row["number"])
        task = db.task_of_change(row["id"])
        row["task_id"] = task["id"] if task else None
        row["images"] = images.get(row["id"], [])
    return rows


def order_changes(db: Database, order_key: str) -> dict[str, dict[str, Any]]:
    """Je Artikel eines Auftrags (``21053``, ``21053V1`` …): letzter Stand und Zahl der offenen
    Änderungen – für die Knöpfe im Auftragsdetail."""
    result = {}
    for key, row in db.latest_changes().items():
        match = _ARTICLE_KEY.fullmatch(key)
        if match and (match["rim"] or match["order"]) == order_key:
            result[key] = {"label": change_label(row["number"]), "open": row["open"] or 0}
    return result


def open_changes(db: Database, article_key: str, setup: int | None = None) -> list[dict[str, Any]]:
    """Offene Änderungen eines Artikels (Alarm in der Live-Ansicht), die neueste zuerst. Mit ``setup``
    (Spannung des laufenden Programms) nur die für alle Spannungen und die für genau diese."""
    return [
        {"id": r["id"], "label": change_label(r["number"]), "description": r["description"], "setup": r["setup"]}
        for r in db.changes(article_key, "open")
        if setup is None or r["setup"] is None or r["setup"] == setup
    ]
