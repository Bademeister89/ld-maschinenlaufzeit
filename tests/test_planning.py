"""Planung (Board mit Meilensteinen) und Änderungsliste (A01, A02 … je Artikel) mit Alarm auf der Live-Karte."""

import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import orders, planning
from app.config import MachineConfig, Settings
from app.db import SCHEMA_VERSION, Database
from app.main import create_app
from app.planning import PlanningError

from .conftest import feed, snap

TODAY = date(2026, 10, 12)
FULL = b"\xff\xd8\xff\xe0" + b"C" * 2000
THUMB = b"\xff\xd8\xff\xe0" + b"c" * 200


@pytest.fixture
def pdb(db):
    for key, order, version in (("21053", "21053", ""), ("21053V1", "21053", "V1"), ("13238", "13238", "")):
        db.ensure_article(key, order, version, 0)
    return db


def columns(db):
    return {m["name"]: m["id"] for m in db.milestones()}


def titles(db, name):
    board = planning.board(db, TODAY)
    return [t["title"] for m in board["milestones"] if m["name"] == name for t in m["tasks"]]


# --- Meilensteine -----------------------------------------------------------------------------


def test_milestones_are_seeded_once(tmp_path):
    path = tmp_path / "p.db"
    db = Database(path)
    assert [(m["name"], m["done"]) for m in db.milestones()] == [
        ("Offen", 0), ("Design", 0), ("CAD", 0), ("CAM", 0), ("Fräsen", 0), ("Fertig", 1)
    ]
    planning.delete_milestone(db, columns(db)["Design"])
    db.close()
    db = Database(path)  # gelöschter Meilenstein kommt beim nächsten Start nicht zurück
    assert "Design" not in columns(db)
    db.close()


def test_milestone_rules(pdb):
    ids = columns(pdb)
    with pytest.raises(PlanningError, match="Erledigt"):
        planning.delete_milestone(pdb, ids["Fertig"])
    with pytest.raises(PlanningError, match="muss „Erledigt“"):
        planning.update_milestone(pdb, ids["Fertig"], {"name": "Fertig", "done": False})
    planning.create_task(pdb, {"title": "Halter"}, 0)
    with pytest.raises(PlanningError, match="noch Aufgaben"):
        planning.delete_milestone(pdb, ids["Offen"])
    # neuer Meilenstein vor „Fertig“; ein anderer als „Fertig“ markiert: genau einer bleibt es
    added = planning.add_milestone(pdb, {"name": "Messen"})
    assert [m["name"] for m in pdb.milestones()][-2:] == ["Messen", "Fertig"]
    planning.update_milestone(pdb, added["id"], {"name": "Messen", "done": True})
    assert [m["name"] for m in pdb.milestones() if m["done"]] == ["Messen"]
    with pytest.raises(PlanningError, match="schon in der Liste"):
        planning.add_milestone(pdb, {"name": "cad"})


# --- Aufgaben ---------------------------------------------------------------------------------


def test_new_tasks_go_on_top_of_the_first_column(pdb):
    planning.create_task(pdb, {"title": "Erste"}, 0)
    planning.create_task(pdb, {"title": "Zweite", "article_key": "21053v1"}, 0)
    assert titles(pdb, "Offen") == ["Zweite", "Erste"]
    task = pdb.tasks()[0]
    assert (task["article_key"], task["priority"]) == ("21053V1", 2)


def test_move_task_renumbers_and_keeps_order(pdb):
    ids = columns(pdb)
    a, b, c = (planning.create_task(pdb, {"title": t}, 0) for t in "abc")  # Reihenfolge c, b, a
    planning.move_task(pdb, a, ids["Offen"], 0, 1)
    assert titles(pdb, "Offen") == ["a", "c", "b"]
    planning.move_task(pdb, c, ids["CAD"], 5, 2)  # Stelle hinter dem Ende → ans Ende
    assert (titles(pdb, "Offen"), titles(pdb, "CAD")) == (["a", "b"], ["c"])
    assert [pdb.task(i)["position"] for i in (a, c)] == [0, 0]  # Zielspalte neu durchnummeriert
    planning.move_task(pdb, b, ids["Fertig"], 0, 3)
    assert pdb.task(b)["done_at"] == 3
    planning.move_task(pdb, b, ids["CAM"], 0, 4)
    assert pdb.task(b)["done_at"] is None


def test_task_validation(pdb):
    for payload, message in (
        ({"title": " "}, "Titel"),
        ({"title": "x", "article_key": "21O55"}, "keine Artikelnummer"),
        ({"title": "x", "due_date": "31.12.2026"}, "Termin"),
        ({"title": "x", "priority": 7}, "Priorität"),
        ({"title": "x", "worker_id": 42}, "Bearbeiter"),
    ):
        with pytest.raises(PlanningError, match=message):
            planning.create_task(pdb, payload, 0)


def test_overdue_only_when_not_done(pdb):
    ids = columns(pdb)
    late = planning.create_task(pdb, {"title": "spät", "due_date": "2026-10-01"}, 0)
    planning.create_task(pdb, {"title": "heute", "due_date": "2026-10-12"}, 0)
    tasks = {t["title"]: t for m in planning.board(pdb, TODAY)["milestones"] for t in m["tasks"]}
    assert (tasks["spät"]["overdue"], tasks["heute"]["overdue"]) == (True, False)
    planning.move_task(pdb, late, ids["Fertig"], 0, 1)
    tasks = {t["title"]: t for m in planning.board(pdb, TODAY)["milestones"] for t in m["tasks"]}
    assert tasks["spät"]["overdue"] is False


def test_removed_worker_leaves_task_without_worker(pdb):
    worker = pdb.add_worker("Niki", 0)
    task = planning.create_task(pdb, {"title": "x", "worker_id": worker}, 0)
    assert pdb.task(task)["worker"] == "Niki"
    pdb.delete_worker(worker)
    assert pdb.task(task)["worker_id"] is None


# --- Änderungen -------------------------------------------------------------------------------


def test_changes_count_per_article(pdb):
    for key in ("21053", "21053", "21053V1", "21053"):
        planning.create_change(pdb, {"article_key": key, "description": "Bohrung"}, 0)
    labels = {(r["article_key"], r["label"]) for r in planning.changes(pdb)}
    assert labels == {("21053", "A01"), ("21053", "A02"), ("21053", "A03"), ("21053V1", "A01")}
    with pytest.raises(PlanningError, match="was geändert"):
        planning.create_change(pdb, {"article_key": "21053", "description": ""}, 0)
    with pytest.raises(PlanningError, match="Artikel"):
        planning.create_change(pdb, {"description": "x"}, 0)


def test_change_and_task_stay_in_step(pdb):
    ids = columns(pdb)
    change_id = planning.create_change(pdb, {"article_key": "21053V1", "description": "Bohrung 8,5 → 10"}, 0)
    [task] = pdb.tasks()
    assert (task["title"], task["priority"], task["milestone_id"], task["change_id"]) == (
        "Änderung A01: Bohrung 8,5 → 10", 1, ids["Offen"], change_id
    )
    planning.move_task(pdb, task["id"], ids["CAM"], 0, 1)
    assert pdb.change(change_id)["status"] == "open"
    planning.move_task(pdb, task["id"], ids["Fertig"], 0, 2)
    assert (pdb.change(change_id)["status"], pdb.change(change_id)["done_at"]) == ("done", 2)
    planning.move_task(pdb, task["id"], ids["CAM"], 0, 3)
    assert pdb.change(change_id)["status"] == "open"
    # Von Hand umgesetzt → Karte in „Fertig“; wieder geöffnet → oben in die erste Spalte
    planning.update_change(pdb, change_id, {"description": "Bohrung 10", "status": "done"}, 4)
    assert pdb.task(task["id"])["milestone_id"] == ids["Fertig"]
    planning.update_change(pdb, change_id, {"description": "Bohrung 10", "status": "open"}, 5)
    assert (pdb.task(task["id"])["milestone_id"], pdb.change(change_id)["description"]) == (ids["Offen"], "Bohrung 10")


def test_change_in_order_creates_missing_article(db):
    """Änderung direkt im Auftrag, bevor der stündliche Abgleich den Artikel angelegt hat."""
    db.ensure_order("21060", 2026, "21060", 0)
    db.ensure_order("10101018", 0, "10101018", 0, kind="rim")
    planning.create_change(db, {"article_key": "21060v1", "description": "Fase"}, 0)
    planning.create_change(db, {"article_key": "10101018", "description": "Tasche"}, 0)
    assert (db.article("21060V1")["version"], db.article("10101018")["order_key"]) == ("V1", "10101018")
    for key in ("10101018V1", "abc"):  # Felgen haben keine Versionen; keine Nummer
        with pytest.raises(PlanningError, match="keine Artikelnummer"):
            planning.create_change(db, {"article_key": key, "description": "x"}, 0)
    assert planning.order_changes(db, "21060") == {"21060V1": {"label": "A01", "open": 1}}


def test_new_order_number_in_task_creates_the_order(db, make_collector):
    """Auftragsnummern werden fortlaufend vergeben: Eine Aufgabe mit neuer Nummer legt Auftrag und
    Artikel an; laufen später die Programme, buchen sie auf genau diesen Auftrag."""
    c = make_collector()
    planning.create_task(db, {"title": "Schwinge Gen 3 konstruieren", "article_key": "13240"}, 100)
    planning.create_change(db, {"article_key": "13240v1", "description": "Lager 6205"}, 100)
    assert [(o["key"], o["kind"]) for o in db.orders()] == [("13240", "order")]
    assert {a["key"] for a in db.articles()} == {"13240", "13240V1"}
    program = "TNC:\\AUFTRAG\\26-13240-01-01.H"
    feed(c, (200, snap("IDLE", program)), (210, snap("STARTED", program)), (500, snap("FINISHED", program)))
    [row] = orders.list_orders(db)
    assert (row["key"], row["running_s"]) == ("13240", 290)


def test_running_program_moves_tasks_to_milling(pdb, make_collector):
    """Startet ein Programm des Auftrags, rücken seine Aufgaben aus den Spalten davor nach „Fräsen“."""
    c = make_collector()
    ids = columns(pdb)
    base = planning.create_task(pdb, {"title": "Grundversion", "article_key": "21053"}, 0)
    v1 = planning.create_task(pdb, {"title": "Version 1", "article_key": "21053V1"}, 0)
    other = planning.create_task(pdb, {"title": "anderer Auftrag", "article_key": "13238"}, 0)
    free = planning.create_task(pdb, {"title": "ohne Artikel"}, 0)
    later = planning.create_task(pdb, {"title": "schon fertig", "article_key": "21053", "milestone_id": ids["Fertig"]}, 0)
    planning.move_task(pdb, v1, ids["CAM"], 0, 0)
    run(c, "TNC:\\AUFTRAG\\26-21053-01-01.H", 100)  # Grundversion: nur Aufgaben der Grundversion
    assert {pdb.task(i)["milestone_id"] for i in (base,)} == {ids["Fräsen"]}
    assert [pdb.task(i)["milestone_id"] for i in (v1, other, free, later)] == [ids["CAM"], ids["Offen"], ids["Offen"], ids["Fertig"]]
    feed(c, (200, snap("FINISHED", "TNC:\\AUFTRAG\\26-21053-01-01.H")))
    run(c, "TNC:\\AUFTRAG\\26-21053V1-02-01.H", 300)
    assert pdb.task(v1)["milestone_id"] == ids["Fräsen"]
    assert titles(pdb, "Fräsen") == ["Version 1", "Grundversion"]
    assert [e["payload"]["tasks"] for e in pdb.events_of_type("tasks_moved")] == [[base], [v1]]


def test_milling_column_can_be_changed_or_switched_off(pdb, make_collector):
    ids = columns(pdb)
    assert [m["name"] for m in pdb.milestones() if m["milling"]] == ["Fräsen"]
    planning.update_milestone(pdb, ids["CAM"], {"name": "CAM", "milling": True})
    assert [m["name"] for m in pdb.milestones() if m["milling"]] == ["CAM"]
    with pytest.raises(PlanningError, match="Erledigt"):
        planning.update_milestone(pdb, ids["Fertig"], {"name": "Fertig", "milling": True})
    planning.update_milestone(pdb, ids["CAM"], {"name": "CAM", "milling": False})
    task = planning.create_task(pdb, {"title": "x", "article_key": "21053"}, 0)
    run(make_collector(), "TNC:\\AUFTRAG\\26-21053-01-01.H", 100)
    assert pdb.task(task)["milestone_id"] == ids["Offen"]  # ohne Programmlauf-Spalte bleibt alles


def test_order_name_from_task_and_change(db):
    """Auftragsname: bei neuer Nummer gleich übernommen, später änderbar; gilt für alle Versionen."""
    task = planning.create_task(db, {"title": "konstruieren", "article_key": "14025", "order_title": " Gabelbrücke oben "}, 0)
    assert (db.order("14025")["title"], db.task(task)["order_title"]) == ("Gabelbrücke oben", "Gabelbrücke oben")
    planning.update_task(db, task, {"title": "konstruieren", "article_key": "14025"}, 1)  # ohne Feld: bleibt
    assert db.order("14025")["title"] == "Gabelbrücke oben"
    planning.create_change(db, {"article_key": "14025V1", "description": "Fase", "order_title": "Gabelbrücke"}, 2)
    assert db.order("14025")["title"] == "Gabelbrücke"
    assert {t["article_key"]: t["order_title"] for t in db.tasks()} == {"14025": "Gabelbrücke", "14025V1": "Gabelbrücke"}
    with pytest.raises(PlanningError, match="höchstens 120"):
        planning.create_task(db, {"title": "x", "article_key": "14026", "order_title": "x" * 121}, 0)
    assert db.order("14026") is None


def test_change_goes_to_chosen_milestone(pdb):
    ids = columns(pdb)
    planning.create_change(pdb, {"article_key": "21053", "description": "CAM anpassen", "milestone_id": ids["CAM"]}, 0)
    assert titles(pdb, "CAM") == ["Änderung A01: CAM anpassen"]
    change = planning.create_change(pdb, {"article_key": "21053", "description": "schon erledigt", "milestone_id": ids["Fertig"]}, 5)
    assert (pdb.change(change)["status"], pdb.change(change)["done_at"]) == ("done", 5)
    with pytest.raises(PlanningError, match="Meilenstein"):
        planning.create_change(pdb, {"article_key": "21053", "description": "x", "milestone_id": 999}, 0)
    assert len(pdb.changes("21053")) == 2


def test_failed_task_does_not_create_an_order(db):
    with pytest.raises(PlanningError, match="Termin"):
        planning.create_task(db, {"title": "x", "article_key": "13241", "due_date": "morgen"}, 0)
    with pytest.raises(PlanningError, match="Meilenstein"):
        planning.create_task(db, {"title": "x", "article_key": "13241", "milestone_id": 999}, 0)
    assert (db.orders(), db.articles()) == ([], [])


def test_delete_change_removes_its_task(pdb):
    change_id = planning.create_change(pdb, {"article_key": "21053", "description": "x"}, 0)
    planning.delete_change(pdb, change_id)
    assert (pdb.tasks(), pdb.changes()) == ([], [])


# --- Live-Alarm -------------------------------------------------------------------------------


def run(c, program, t=0):
    feed(c, (t, snap("IDLE", program)), (t + 10, snap("STARTED", program)))


def test_live_card_warns_about_open_change_of_this_version(pdb, make_collector):
    c = make_collector()
    planning.create_change(pdb, {"article_key": "21053", "description": "Grundversion"}, 0)
    run(c, "TNC:\\AUFTRAG\\26-21053V1-01-01.H")
    assert c.live()["order"]["changes"] == []  # Änderung der Grundversion betrifft V1 nicht
    change_id = planning.create_change(pdb, {"article_key": "21053V1", "description": "Bohrung"}, 0)
    assert c.live()["order"]["changes"] == [{"id": change_id, "label": "A01", "description": "Bohrung", "setup": None}]
    planning.update_change(pdb, change_id, {"description": "Bohrung", "status": "done"}, 1)
    assert c.live()["order"]["changes"] == []


def plan(db, *names):
    """Programme mit Planzeit (ohne Lauf): Sie bestimmen die Spannungen des Artikels mit."""
    for name in names:
        db.set_plan(name, name, orders.parse_program(name).key, 600, "manual", 0)


def test_change_setup_choices(pdb):
    """Zur Wahl stehen die eigenen Spannungen ohne Vorrichtung; die gemeinsame Spannung 1 gehört zur
    Grundversion."""
    plan(pdb, "26-21053-01-01", "26-21053-08-01", "26-21053V1-02-01", "26-21053V1-03-01")
    assert planning.article_setups(pdb, "21053V1") == {
        "setups": [2, 3], "inherited": [1], "base": "21053", "kind": "order", "label": "Aufspannung",
    }
    assert planning.article_setups(pdb, "21053")["setups"] == [1]
    with pytest.raises(PlanningError, match="Grundversion 21053"):
        planning.create_change(pdb, {"article_key": "21053V1", "description": "x", "setup": 1}, 0)
    with pytest.raises(PlanningError, match="Ungültige Spannung"):
        planning.create_change(pdb, {"article_key": "21053", "description": "Vorrichtung", "setup": 8}, 0)
    change_id = planning.create_change(pdb, {"article_key": "21053V1", "description": "Bohrung", "setup": "2"}, 0)
    assert pdb.change(change_id)["setup"] == 2
    [task] = pdb.tasks()
    assert task["change_setup"] == 2
    # Bearbeiten ohne setup lässt die Spannung, mit leerem Wert gilt die Änderung für alle
    planning.update_change(pdb, change_id, {"description": "Bohrung 10"}, 1)
    assert pdb.change(change_id)["setup"] == 2
    planning.update_change(pdb, change_id, {"description": "Bohrung 10", "setup": ""}, 2)
    assert pdb.change(change_id)["setup"] is None


def test_live_alarm_only_for_the_setup_of_the_change(pdb, make_collector):
    c = make_collector()
    plan(pdb, "26-21053V1-02-01", "26-21053V1-03-01")
    on_setup = planning.create_change(pdb, {"article_key": "21053V1", "description": "Bohrung", "setup": 3}, 0)
    run(c, "TNC:\\AUFTRAG\\26-21053V1-02-01.H")
    assert c.live()["order"]["changes"] == []  # andere Spannung: kein Alarm
    run(c, "TNC:\\AUFTRAG\\26-21053V1-03-01.H", 100)
    assert [x["id"] for x in c.live()["order"]["changes"]] == [on_setup]
    everywhere = planning.create_change(pdb, {"article_key": "21053V1", "description": "Fase"}, 0)
    assert [x["id"] for x in c.live()["order"]["changes"]] == [everywhere, on_setup]
    run(c, "TNC:\\AUFTRAG\\26-21053V1-02-01.H", 200)
    assert [x["id"] for x in c.live()["order"]["changes"]] == [everywhere]  # ohne Spannung: überall


def test_live_card_warns_for_legacy_program(pdb, make_collector):
    c = make_collector()
    planning.create_change(pdb, {"article_key": "13238", "description": "Fase"}, 0)
    run(c, "TNC:\\AUFTRAG\\1323801.h")
    assert [x["label"] for x in c.live()["order"]["changes"]] == ["A01"]


# --- API --------------------------------------------------------------------------------------


@pytest.fixture
def app_client(tmp_path):
    settings = Settings(machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"),), db_path=tmp_path / "s.db", simulate=True)
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as client:
        app.state.ctx.db.ensure_order("21053", 2026, "21053", 0)
        app.state.ctx.db.ensure_article("21053", "21053", "", 0)
        yield app, client


def test_api_board_tasks_and_changes(app_client):
    app, client = app_client
    worker = client.post("/api/config/workers", json={"name": "Niki"}).json()
    assert client.post("/api/config/workers", json={"name": "niki"}).status_code == 409
    task = client.post("/api/planning/tasks", json={"title": "Halter", "worker_id": worker["id"]}).json()
    change = client.post("/api/planning/changes", json={"article_key": "21053", "description": "Fase"}).json()
    assert (change["label"], change["status"], change["images"]) == ("A01", "open", [])
    board = client.get("/api/planning/board").json()
    first = board["milestones"][0]
    assert [t["title"] for t in first["tasks"]] == ["Änderung A01: Fase", "Halter"]
    assert first["tasks"][0]["change"] == "A01"
    done = board["milestones"][-1]
    moved = client.post(f"/api/planning/tasks/{change['task_id']}/move", json={"milestone_id": done["id"], "index": 0})
    assert moved.status_code == 200
    assert client.get("/api/planning/changes?article=21053").json()["changes"][0]["status"] == "done"
    assert client.get("/api/articles").json()["articles"][0]["change"] == {"label": "A01", "open": 0}
    updated = client.put(f"/api/planning/tasks/{task['id']}", json={"title": "Halter neu", "due_date": "2026-11-01"})
    assert (updated.json()["title"], updated.json()["due_date"]) == ("Halter neu", "2026-11-01")
    assert client.post("/api/planning/tasks", json={"title": ""}).status_code == 400
    assert client.delete(f"/api/planning/tasks/{task['id']}").status_code == 204
    assert client.delete(f"/api/planning/tasks/{task['id']}").status_code == 404


def test_order_detail_lists_changes_per_article(app_client):
    app, client = app_client
    collector = app.state.ctx.collectors["m1"]
    feed(collector, (1000, snap("IDLE", "TNC:\\AUFTRAG\\26-21053V1-01-01.H")), (1010, snap("FINISHED", "TNC:\\AUFTRAG\\26-21053V1-01-01.H")))
    assert client.get("/api/orders/21053").json()["changes"] == {}
    assert client.post("/api/planning/changes", json={"article_key": "21053V1", "description": "Fase"}).status_code == 201
    client.post("/api/planning/changes", json={"article_key": "21053", "description": "Bohrung"})
    assert client.get("/api/orders/21053").json()["changes"] == {
        "21053": {"label": "A01", "open": 1}, "21053V1": {"label": "A01", "open": 1}
    }


def test_api_changes_and_new_article_with_name(app_client):
    _, client = app_client
    fresh = client.get("/api/planning/changes", params={"article": "14030"}).json()
    assert (fresh["exists"], fresh["order_title"], [m["name"] for m in fresh["milestones"]][0]) == (False, None, "Offen")
    client.post("/api/planning/changes", json={"article_key": "14030", "description": "Fase", "order_title": "Hebel lang"})
    known = client.get("/api/planning/changes", params={"article": "14030"}).json()
    assert (known["exists"], known["order_title"]) == (True, "Hebel lang")
    r = client.post("/api/articles", json={"key": "14031", "title": "Hebel kurz"})
    assert r.status_code == 201 and client.get("/api/orders/14031").json()["order"]["title"] == "Hebel kurz"


def test_api_milestone_config(app_client):
    _, client = app_client
    milestones = client.get("/api/config/milestones").json()["milestones"]
    done = milestones[-1]
    assert client.delete(f"/api/config/milestones/{done['id']}").status_code == 409
    ids = [m["id"] for m in reversed(milestones)]
    assert client.post("/api/config/milestones/order", json={"ids": ids}).status_code == 204
    assert client.get("/api/config/milestones").json()["milestones"][0]["name"] == "Fertig"
    assert client.post("/api/config/milestones/order", json={"ids": ids[1:]}).status_code == 409


def test_api_change_images(app_client):
    app, client = app_client
    folder = app.state.ctx.settings.change_images_dir
    change = client.post("/api/planning/changes", json={"article_key": "21053", "description": "Fase"}).json()
    url = f"/api/planning/changes/{change['id']}/images"
    headers = {"Content-Type": "application/octet-stream", "X-Image-Length": str(len(FULL))}
    for _ in range(2):
        response = client.put(url, content=FULL + THUMB, headers=headers)
        assert response.status_code == 201
    images = response.json()["images"]
    assert len(images) == 2 and len(list(folder.iterdir())) == 4
    assert client.get(images[0]["image_url"]).content == FULL
    assert client.get(images[0]["thumb_url"]).content == THUMB
    assert client.delete(f"/api/planning/images/{images[0]['id']}").status_code == 204
    assert len(list(folder.iterdir())) == 2
    assert client.put(url, content=b"kein bild", headers={"X-Image-Length": "3"}).status_code == 400
    assert client.delete(f"/api/planning/changes/{change['id']}").status_code == 204
    assert list(folder.iterdir()) == []


def test_live_api_shows_change(app_client):
    app, client = app_client
    client.post("/api/planning/changes", json={"article_key": "21053", "description": "Fase"})
    collector = app.state.ctx.collectors["m1"]
    run(collector, "TNC:\\AUFTRAG\\26-21053-01-01.H", 1000)
    order = client.get("/api/machines").json()["machines"][0]["order"]
    assert [c["label"] for c in order["changes"]] == ["A01"]


def test_update_from_schema_19(tmp_path):
    """Eine Datenbank von Version 1.31 bekommt die Spalte setup; vorhandene Änderungen gelten für alle
    Spannungen."""
    path = tmp_path / "old.db"
    db = Database(path)
    db.ensure_article("21053", "21053", "", 0)
    planning.create_change(db, {"article_key": "21053", "description": "Fase"}, 0)
    db.close()
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE article_changes DROP COLUMN setup")
    con.execute("UPDATE meta SET value = '19' WHERE key = 'schema_version'")
    con.commit()
    con.close()
    db = Database(path)
    assert db.get_meta("schema_version") == str(SCHEMA_VERSION)
    [change] = db.changes("21053")
    assert (change["number"], change["setup"]) == (1, None)
    assert [x["label"] for x in planning.open_changes(db, "21053", 2)] == ["A01"]
    db.close()


def test_update_from_schema_18(tmp_path):
    """Eine Datenbank von Version 1.28 bekommt die neuen Tabellen und die Meilensteine."""
    path = tmp_path / "old.db"
    Database(path).close()
    con = sqlite3.connect(path)
    for table in ("tasks", "change_images", "article_changes", "workers", "milestones"):
        con.execute(f"DROP TABLE {table}")
    con.execute("DELETE FROM meta WHERE key = 'milestones_seeded'")
    con.execute("UPDATE meta SET value = '18' WHERE key = 'schema_version'")
    con.commit()
    con.close()
    db = Database(path)
    assert db.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert len(db.milestones()) == 6
    db.close()
