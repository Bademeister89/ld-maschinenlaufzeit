"""Artikelverwaltung: Abgleich mit den Aufträgen, Rohling und Kosten, Preise, Excel-Export."""

import io
import math
import re
import sqlite3
import zipfile
from datetime import timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter

from app import articles
from app.config import MachineConfig, Settings
from app.db import SCHEMA_VERSION, Database
from app.main import create_app

from .conftest import feed, snap
from .xlsx_formula import Evaluator

TZ = ZoneInfo("Europe/Berlin")
P11 = "TNC:\\X\\26-21055-01-01.H"
P21 = "TNC:\\X\\26-21055-02-01.H"
V1 = "TNC:\\X\\26-21055V1-01-01.H"


def run_part(col, t0, program, run_s):
    feed(col, (t0, snap("IDLE", program)), (t0 + 10, snap("STARTED", program)), (t0 + 10 + run_s, snap("FINISHED", program)))
    return t0 + 10 + run_s


@pytest.fixture
def client(tmp_path):
    settings = Settings(
        machines=(MachineConfig("m1", "DMU 70", "10.0.0.1"), MachineConfig("m2", "DMU 105", "10.0.0.2")),
        db_path=tmp_path / "a.db",
        simulate=True,
    )
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as c:
        yield c


def rate(client, machine_id, value):
    m = next(x for x in client.get("/api/config").json()["machines"] if x["id"] == machine_id)
    r = client.put(f"/api/config/machines/{machine_id}", json={**m, "hourly_rate": value})
    assert r.status_code == 200, r.text
    return r.json()


def article(client, key):
    return next(a for a in client.get("/api/articles").json()["articles"] if a["key"] == key)


# --- Nummern, Volumen ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [("21060", ("21060", "", "order")), (" 21060v1 ", ("21060", "V1", "order")), ("4711V12", ("4711", "V12", "order")),
     ("10101018", ("10101018", "", "rim"))],
)
def test_parse_key(text, expected):
    assert articles.parse_key(text) == expected


@pytest.mark.parametrize("text", ["", "123", "21060X", "21060V", "2106001-01", "20101018"])
def test_parse_key_rejects(text):
    with pytest.raises(articles.ArticleError):
        articles.parse_key(text)


def test_volume_and_blank_text():
    assert articles.volume_l("block", 120, 80, 40) == pytest.approx(0.384)
    assert articles.volume_l("round", 80, 120, None) == pytest.approx(math.pi * 40**2 * 120 / 1e6)
    assert articles.volume_l("block", 120, 80, None) is None
    assert articles.blank_text({"name": "Alu 7075"}, "block", 120, 80, 40.5) == "Alu 7075 · Block 120 × 80 × 40,5 mm"
    assert articles.blank_text(None, "round", 80, 120, None) == "Rund Ø 80 × 120 mm"


# --- Abgleich -------------------------------------------------------------------------------------


def test_sync_creates_one_article_per_version_and_manual_articles_create_the_order(client):
    ctx = client.app.state.ctx
    col = ctx.collectors["m1"]
    t = run_part(col, 1000, P11, 600)
    t = run_part(col, t + 10, V1, 300)
    client.put("/api/orders/21055/plans", json={"program": "26-21055V2-01-01", "time": "1"})  # V2 nur geplant
    keys = [a["key"] for a in client.get("/api/articles").json()["articles"]]
    assert keys == ["21055", "21055V1", "21055V2"]
    assert articles.sync(ctx.db) == 0  # idempotent

    r = client.post("/api/articles", json={"key": "21060v1"})
    assert r.status_code == 201 and r.json()["order_created"] is True
    assert ctx.db.order("21060") is not None
    a = article(client, "21060V1")
    assert (a["manual"], a["parts"], a["part_s"]) == (True, 0, None)
    assert client.post("/api/articles", json={"key": "21060V1"}).status_code == 400  # gibt es schon
    assert client.post("/api/articles", json={"key": "abc"}).status_code == 400
    assert client.post("/api/articles", json={"key": "10101018"}).json()["kind"] == "rim"

    # Auftrag löschen: Artikel weg; Stammdaten entfernen: Abgleich legt ihn neu an (ohne Preise)
    client.put("/api/articles/21055V1", json={"price_vk": "99"})
    assert client.delete("/api/articles/21055V1").status_code == 204
    assert article(client, "21055V1")["price_vk"] is None
    assert client.delete("/api/orders/21060").status_code == 200
    assert "21060V1" not in [a["key"] for a in client.get("/api/articles").json()["articles"]]


def test_versions_build_on_the_shared_first_setup(client):
    """21054: Spannung 1 gemeinsam (Grundversion), Spannung 2 je Version (V1, V2). Artikel sind nur die
    Endartikel V1 und V2 – mit der Laufzeit von Spannung 1; die Grundversion hat keine fertigen Teile."""
    ctx = client.app.state.ctx
    col = ctx.collectors["m1"]
    t = 1000
    for _ in range(3):
        t = run_part(col, t + 10, "TNC:\\X\\21-21054-01-01.H", 1200)  # Vorstufe: 20 min
    t = run_part(col, t + 10, "TNC:\\X\\21-21054v1-02-01.H", 3600)
    t = run_part(col, t + 10, "TNC:\\X\\21-21054v2-02-01.H", 1800)
    rate(client, "m1", "60")
    rows = {a["key"]: a for a in client.get("/api/articles").json()["articles"]}
    assert sorted(rows) == ["21054V1", "21054V2"]  # Grundversion nur Vorstufe
    assert (rows["21054V1"]["part_s"], rows["21054V1"]["inherited"]) == (pytest.approx(4800), [1])
    assert rows["21054V2"]["part_s"] == pytest.approx(3000)
    assert rows["21054V1"]["mill_cost"] == pytest.approx(80)  # 1 h 20 min × 60 €/h
    assert (rows["21054V1"]["parts"], rows["21054V2"]["parts"]) == (1, 1)

    detail = client.get("/api/orders/21054").json()
    blocks = {v["version"]: v for v in detail["versions"]}
    assert (blocks[""]["pre_stage"], blocks[""]["parts"]) == (True, 0)
    assert (blocks["V1"]["inherited"], blocks["V1"]["part_run_s"]) == ([1], pytest.approx(4800))
    assert detail["totals"]["parts"] == 2
    [row] = [o for o in client.get("/api/orders").json()["orders"] if o["key"] == "21054"]
    assert row["parts"] == 2  # nicht 3 Vorstufen + 2


def test_base_with_its_own_last_setup_stays_an_article(client):
    """Hat die Grundversion selbst eine Spannung 2, ist sie ein eigener Endartikel."""
    ctx = client.app.state.ctx
    col = ctx.collectors["m1"]
    t = run_part(col, 1000, P11, 600)
    t = run_part(col, t + 10, P21, 600)
    run_part(col, t + 10, "TNC:\\X\\26-21055V1-02-01.H", 900)
    rows = {a["key"]: a for a in client.get("/api/articles").json()["articles"]}
    assert sorted(rows) == ["21055", "21055V1"]
    assert (rows["21055"]["parts"], rows["21055V1"]["part_s"]) == (1, pytest.approx(1500))


def test_article_image_comes_from_its_setup(client):
    """Jede Version zeigt das Bild ihrer letzten Spannung, sonst das der übernommenen Spannung 1,
    sonst das Auftragsbild."""
    ctx = client.app.state.ctx
    col = ctx.collectors["m1"]
    t = run_part(col, 1000, "TNC:\\X\\21-21054-01-01.H", 600)
    t = run_part(col, t + 10, "TNC:\\X\\21-21054v1-02-01.H", 600)
    run_part(col, t + 10, "TNC:\\X\\21-21054v2-02-01.H", 600)
    jpeg = b"\xff\xd8\xff"
    ctx.order_images.save("21054", jpeg + b"auftrag", jpeg + b"auftrag-t")
    ctx.order_images.save_setup("21054", "V1", 2, jpeg + b"v1", jpeg + b"v1-t")
    ctx.order_images.save_setup("21054", "", 1, jpeg + b"sp1", jpeg + b"sp1-t")
    rows = {a["key"]: a for a in client.get("/api/articles").json()["articles"]}
    assert "/setups/2/image?version=V1&size=thumb" in rows["21054V1"]["thumb_url"]
    assert "/setups/1/image?size=thumb" in rows["21054V2"]["thumb_url"]  # V2 ohne eigenes Bild: Spannung 1
    with zipfile.ZipFile(io.BytesIO(client.get("/api/articles/export.xlsx").content)) as z:
        media = sorted(z.read(n) for n in z.namelist() if n.startswith("xl/media/"))
    assert media == sorted([jpeg + b"v1-t", jpeg + b"sp1-t"])


def test_reset_average_ignores_earlier_runs_until_new_ones(client):
    """Einfahren: Abbrüche und Neustarts verfälschen die Ø-Zeit. Zurücksetzen – die Läufe bleiben, zählen
    aber nicht mehr; bis zum nächsten Lauf gilt die Planzeit."""
    ctx = client.app.state.ctx
    col = ctx.collectors["m1"]
    t = run_part(col, 1000, P11, 18)  # Fehlstart „fertig“
    t = run_part(col, t + 10, P11, 1600)
    client.put("/api/orders/21055/plans", json={"program": "26-21055-01-01", "time": "1"})
    row = lambda: client.get("/api/orders/21055").json()["setups"][0]["programs"][0]  # noqa: E731
    assert row()["avg_run_s"] == pytest.approx(809)

    r = client.post("/api/orders/21055/programs/26-21055-01-01/reset")
    assert r.status_code == 200
    p = row()
    assert (p["avg_run_s"], p["runs"], p["avg_reset_at"]) == (None, 2, pytest.approx(r.json()["reset_at"]))
    a = article(client, "21055")
    assert (a["part_s"], a["part_estimated"]) == (3600, True)  # Planzeit
    assert [x for x in client.get("/api/stats", params={"from": 0}).json()["programs"] if x["avg_run_s"]] == []
    # Prognose: keine Referenzläufe mehr → Planzeit statt der alten Läufe
    assert (col._forecaster.typical_run_s(P11), col._forecaster.plan_s(P11)) == (None, 3600)

    # Neuer Lauf nach dem Zurücksetzen zählt
    import time as _time
    now = _time.time()
    run_part(col, now + 10, P11, 1500)
    assert row()["avg_run_s"] == pytest.approx(1500)
    # Aufheben: alle Läufe zählen wieder
    assert client.delete("/api/orders/21055/programs/26-21055-01-01/reset").status_code == 204
    assert row()["avg_run_s"] == pytest.approx((18 + 1600 + 1500) / 3)
    assert client.delete("/api/orders/21055/programs/26-21055-01-01/reset").status_code == 404
    assert client.post("/api/orders/21055/programs/26-99999-01-01/reset").status_code == 404


# --- Kosten --------------------------------------------------------------------------------------


def test_costs_material_milling_and_margin(client):
    ctx = client.app.state.ctx
    t = run_part(ctx.collectors["m1"], 1000, P11, 3600)  # Spannung 1: 1 h an der DMU 70
    run_part(ctx.collectors["m2"], t + 10, P21, 1800)  # Spannung 2: 0,5 h an der DMU 105
    rate(client, "m1", "90")
    rate(client, "m2", "120,50")
    mat = client.post("/api/config/materials", json={"name": "Alu 7075", "density": "2,81", "price_per_kg": "5,20"}).json()
    r = client.put("/api/articles/21055", json={
        "material_id": mat["id"], "shape": "block", "dim_a": "120", "dim_b": "80", "dim_c": "40",
        "price_ek": "100", "price_vk": "250,00",
    })
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["blank"] == "Alu 7075 · Block 120 × 80 × 40 mm"
    # 0,384 l × 2,81 kg/l = 1,079 kg × 5,20 €/kg
    assert (a["volume_l"], a["density"], a["price_per_kg"]) == (pytest.approx(0.384), 2.81, 5.2)
    assert (a["weight_kg"], a["material_cost"]) == (pytest.approx(1.07904), 5.61)
    assert a["part_s"] == pytest.approx(5400)
    assert a["mill_cost"] == pytest.approx(90 + 60.25)
    assert (a["cost"], a["cost_complete"]) == (pytest.approx(155.86), True)
    assert (a["margin"], a["margin_pct"]) == (pytest.approx(94.14), 37.7)
    assert (a["parts"], a["mill_estimated"], a["part_estimated"]) == (1, False, False)
    assert a["last_production"] is not None

    # Preis je kg geändert → Materialpreis sofort neu; ohne Dichte kein Gewicht und kein Preis
    client.put(f"/api/config/materials/{mat['id']}", json={"name": "Alu 7075", "density": "2,81", "price_per_kg": "10"})
    assert article(client, "21055")["material_cost"] == pytest.approx(10.79)
    client.put(f"/api/config/materials/{mat['id']}", json={"name": "Alu 7075", "price_per_kg": "10"})
    assert (article(client, "21055")["weight_kg"], article(client, "21055")["material_cost"]) == (None, None)
    # Material entfernt: Maße bleiben, Herstellkosten nur noch „mind.“
    assert client.delete(f"/api/config/materials/{mat['id']}").status_code == 204
    a = article(client, "21055")
    assert (a["material"], a["shape"], a["cost_complete"], a["margin"]) == (None, "block", False, None)


def test_milling_rate_weighted_and_plan_fallback(client):
    ctx = client.app.state.ctx
    t = run_part(ctx.collectors["m1"], 1000, P11, 3000)
    run_part(ctx.collectors["m2"], t + 10, P11, 1000)  # dasselbe Programm an der anderen Maschine
    client.put("/api/orders/21055/plans", json={"program": "26-21055-02-01", "time": "1"})  # nie gelaufen
    a = article(client, "21055")
    assert a["mill_cost"] is None and a["part_s"] == pytest.approx(2000 + 3600) and a["part_estimated"]
    rate(client, "m1", "100")
    rate(client, "m2", "60")
    a = article(client, "21055")
    # Ø 2000 s × (100·3000 + 60·1000)/4000 = 90 €/h → 50 €; Planzeit 1 h × Durchschnitt 80 €/h (geschätzt)
    assert a["mill_cost"] == pytest.approx(50 + 80)
    assert a["mill_estimated"] is True


def test_article_validation(client):
    run_part(client.app.state.ctx.collectors["m1"], 1000, P11, 60)
    for payload in ({"dim_a": "-1"}, {"dim_a": "0"}, {"dim_a": "9999"}, {"price_vk": "abc"}, {"price_ek": "-5"},
                    {"shape": "kugel"}, {"material_id": 99}, {"note": "x" * 501}):
        assert client.put("/api/articles/21055", json=payload).status_code == 400, payload
    r = client.put("/api/articles/21055", json={"shape": "round", "dim_a": "80,5", "dim_b": "120", "dim_c": "5", "price_vk": "1.234,50"})
    assert (r.json()["dim_a"], r.json()["dim_c"], r.json()["price_vk"]) == (80.5, None, 1234.5)
    assert client.put("/api/articles/99999", json={}).status_code == 404
    assert client.post("/api/config/materials", json={"name": ""}).status_code == 400
    for bad in ({"name": "Stahl", "density": "0"}, {"name": "Stahl", "density": "30"}, {"name": "Stahl", "price_per_kg": "x"}):
        assert client.post("/api/config/materials", json=bad).status_code == 400
    client.post("/api/config/materials", json={"name": "Stahl", "density": "7,85"})
    assert client.post("/api/config/materials", json={"name": "stahl"}).status_code == 409
    m = next(x for x in client.get("/api/config").json()["machines"] if x["id"] == "m1")
    assert client.put("/api/config/machines/m1", json={**m, "hourly_rate": "viel"}).status_code == 400


# --- Excel-Export ---------------------------------------------------------------------------------


def test_excel_export(client):
    ctx = client.app.state.ctx
    t = run_part(ctx.collectors["m1"], 1000, P11, 3600)
    run_part(ctx.collectors["m1"], t + 10, V1, 1800)
    rate(client, "m1", "90")
    mat = client.post("/api/config/materials", json={"name": "Alu 7075", "density": "2,81", "price_per_kg": "5,20"}).json()
    client.put("/api/articles/21055", json={"material_id": mat["id"], "shape": "block", "dim_a": "100", "dim_b": "100", "dim_c": "100", "price_vk": "200"})
    r = client.get("/api/articles/export.xlsx")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert r.headers["content-disposition"].startswith('attachment; filename="artikel_')
    wb = load_workbook(io.BytesIO(r.content))
    assert wb.sheetnames == ["Artikel", "Materialien", "Erläuterung"]
    ws = wb["Artikel"]
    assert [ws[f"{c}1"].value for c in "AFP"] == ["Artikel", "Rohling und Material", "Fertigung"]  # Gruppen
    head = [c.value for c in ws[2]]
    assert head[:5] == ["Bild", "Artikel", "Bezeichnung", "Art", "Status"]
    col = {name.split("\n")[0]: get_column_letter(i) for i, name in enumerate(head, start=1)}
    ev = Evaluator(wb)
    val = lambda name, row=3: ev.cell("Artikel", f"{col[name]}{row}")  # noqa: E731

    assert val("Artikel") == "21055"
    assert ws[f"{col['Materialpreis']}3"].value.startswith("=")  # Formel, kein fester Wert
    # Formeln ergeben dasselbe wie die App: 1 l Alu 7075 = 2,81 kg × 5,20 €/kg
    assert (val("Volumen (l)"), val("Gewicht (kg)"), val("Materialpreis")) == (pytest.approx(1), pytest.approx(2.81), 14.61)
    assert val("Laufzeit je Teil") == timedelta(hours=1)
    assert (val("Stundensatz (€/h)"), val("Preis Fräsen"), val("Herstellkosten")) == (90, 90, pytest.approx(104.61))
    assert (val("Marge"), val("Marge %")) == (pytest.approx(95.39), pytest.approx(0.47695))
    # In der Datei geändert → rechnet neu: Rund Ø 100 × 100 statt Block, VK 300
    ws[f"{col['Form']}3"] = "Rund"
    ws[f"{col['Preis VK']}3"] = 300
    assert val("Volumen (l)") == pytest.approx(math.pi * 50**2 * 100 / 1e6)
    assert val("Materialpreis") == pytest.approx(round(math.pi * 50**2 * 100 / 1e6 * 2.81 * 5.2, 2))  # 11,48 €
    assert val("Marge") == pytest.approx(300 - (val("Materialpreis") + 90))
    # Material und Preis je kg im Blatt „Materialien“ ändern
    wb["Materialien"]["C2"] = 10
    assert val("€ je kg") == 10
    ws[f"{col['Material']}3"] = "Gibt es nicht"
    assert (val("Dichte (g/cm³)"), val("Materialpreis"), val("Marge")) == ("", "", "")

    # Version ohne Rohling: keine Materialkosten, Herstellkosten nur Fräsen
    assert (val("Artikel", 4), val("Materialpreis", 4), val("Herstellkosten", 4)) == ("21055V1", "", 45)
    assert ws.cell(row=5, column=2).value == "Summe"
    assert ev.cell("Artikel", f"{col['Stück produziert']}5") == 2
    assert ws.freeze_panes == "C3" and ws.auto_filter.ref.startswith("A2:")
    assert ws[f"{col['Materialpreis']}3"].number_format == '#,##0.00 "€"'
    assert ws[f"{col['Laufzeit je Teil']}3"].number_format == "[h]:mm"
    assert len(ws.data_validations.dataValidation) == 2  # Auswahllisten Form und Material
    assert [c.value for c in wb["Materialien"][2]] == ["Alu 7075", 2.81, 10, 1]


def test_excel_export_embeds_the_order_image(client):
    ctx = client.app.state.ctx
    run_part(ctx.collectors["m1"], 1000, P11, 60)
    thumb = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffc0001108004800600301110002110103110100ffd9")
    ctx.order_images.save("21055", thumb, thumb)
    # openpyxl liest Bilder nur mit Pillow wieder ein – deshalb direkt in die Datei schauen
    with zipfile.ZipFile(io.BytesIO(client.get("/api/articles/export.xlsx").content)) as z:
        assert z.read("xl/media/image1.jpeg") == thumb  # unverändert eingebettet
        drawing = z.read("xl/drawings/drawing1.xml").decode()
    assert re.search(r"<(\w+:)?col>0</(\w+:)?col>", drawing) and re.search(r"<(\w+:)?row>2</(\w+:)?row>", drawing)  # A3
    assert articles._jpeg_size(thumb) == (96, 72)


# --- Migration ----------------------------------------------------------------------------------


def test_update_from_schema_16_adds_articles_and_hourly_rate(tmp_path):
    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "INSERT INTO meta VALUES ('schema_version', '16');"
        "CREATE TABLE machines (id TEXT PRIMARY KEY, name TEXT NOT NULL, host TEXT NOT NULL, port INTEGER NOT NULL);"
        "INSERT INTO machines VALUES ('m1', 'DMU 70', '10.0.0.1', 19000);"
    )
    con.close()
    db = Database(path)
    assert db.get_meta("schema_version") == str(SCHEMA_VERSION)
    assert db.machine("m1")["hourly_rate"] is None
    assert db.articles() == [] and db.materials() == []
    db.close()
