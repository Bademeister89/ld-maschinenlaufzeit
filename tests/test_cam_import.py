"""CAM-Planzeiten: Tebis-Doku (PDF) einlesen, Planzeit von Hand, Prognose und Palettenliste."""

import pytest
from fastapi.testclient import TestClient

from app import cam_import, orders
from app.cam_import import CamImportError, parse_cam_doc, parse_plan_time, pdf_text, read_cam_pdf
from app.config import MachineConfig, Settings
from app.main import create_app

from .conftest import feed, snap

# Text einer Tebis-Doku, wie pypdf ihn liefert (Spannung 2 von Auftrag 21053, gekürzt; die Fußzeile
# mit der Firmenanschrift ist weggelassen). Die Planzeit steht in der Zeile nach „Abgearbeitet ? (“.
DOKU_SP_2 = """21.09.2026
02 DMU 70 Erowa 11:10 Uhr
Seite 1 von 2
X
CAD-Datei: 21053 abdeckung rechts 1 cvo.cad
Kommission: Teil-Bezeichnung:
Gesamtlaufzeit: 03:03:60 \ufffdnderungsdatum:
Dateipfad: x:/21 (motor)/21053 abdeckung rechts 1 cvo/002 cam/
Aufspannplan

21.09.2026
02 DMU 70 Erowa 11:10 Uhr
Seite 2 von 2
Programmablauf
Aufmass Werkzeug T. Nr. Bearb.zeit Kommentar
26-21053-02-01 Abgearbeitet ?  (
 ) 03:00:15
0,30 mm T019 SF 40R2 GL150 - ASA16 38-38 L100 19 00:04:24
0,10 mm T___ KF D 4 AL 30 \ufffd SRU06 12-18 L080 600 00:37:11
26-21053-02-02 Abgearbeitet ?  (
 ) 00:00:46
In DL Tiefer setzten
0,00 mm T___ KF D 4 AL 30 \ufffd SRU06 12-18 L080 600 00:00:46
26-21053-02-03 Abgearbeitet ?  (
 ) 00:02:59
0,10 mm
-2,00 mm
T605 SC D125R0 AL 45 - ASA16 32-32 L100 605 00:02:59
"""
DOKU_V2 = """21.09.2026
02 DMU 70 Erowa 11:10 Uhr
CAD-Datei: 21053 abdeckung rechts 1 cvo.cad
Gesamtlaufzeit: 02:23:36
Programmablauf
21-21053v2-02-01 Abgearbeitet ?  (
 ) 02:21:26
Vorrichtung Abgearbeitet ?  (
 ) 00:01:00
"""


def make_pdf(lines: list[str]) -> bytes:
    """Kleinste PDF mit einer Seite Text (Helvetica) – zum Testen ohne echte Tebis-Datei."""
    def esc(text: str) -> str:
        return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops = ["BT", "/F1 10 Tf", "14 TL", "40 800 Td"] + [f"({esc(line)}) Tj T*" for line in lines] + ["ET"]
    stream = "\n".join(ops).encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


# --- Auslesen ------------------------------------------------------------------------------------


def test_parse_tebis_doku():
    doc = parse_cam_doc(DOKU_SP_2)
    assert (doc.setup, doc.machine, doc.cad_name, doc.title) == (2, "DMU 70 Erowa", "21053 abdeckung rechts 1 cvo", "Abdeckung rechts 1 cvo")
    assert doc.total_s == 3 * 3600 + 3 * 60 + 60  # Tebis schreibt 03:03:60
    assert doc.programs == [("26-21053-02-01", 10815.0), ("26-21053-02-02", 46.0), ("26-21053-02-03", 179.0)]
    assert doc.ignored == []


def test_parse_versions_and_unknown_programs():
    doc = parse_cam_doc(DOKU_V2)
    assert doc.programs == [("21-21053v2-02-01", 8486.0)]
    assert doc.ignored == ["Vorrichtung"]  # kein Auftrags- oder Felgenprogramm


def test_document_without_programs_is_refused():
    with pytest.raises(CamImportError, match="kein Programm"):
        parse_cam_doc("Irgendein Text\nohne Programmablauf\n")


def test_pdf_text_of_a_real_pdf():
    data = make_pdf(["02 DMU 70 Erowa 11:10 Uhr", "26-21053-02-01 Abgearbeitet ? ( ) 03:00:15"])
    assert "26-21053-02-01 Abgearbeitet" in pdf_text(data)
    assert read_cam_pdf(data).programs == [("26-21053-02-01", 10815.0)]


def test_non_pdf_is_refused():
    with pytest.raises(CamImportError, match="keine PDF"):
        pdf_text(b"PK\x03\x04 kein pdf")
    with pytest.raises(CamImportError, match="nicht lesen"):
        pdf_text(b"%PDF-1.4 kaputt")


@pytest.mark.parametrize(("text", "seconds"), [("4,5", 16200), ("4.5", 16200), ("4:30", 16200), ("2:21:26", 8486), ("3 h", 10800)])
def test_parse_plan_time(text, seconds):
    assert parse_plan_time(text) == seconds


@pytest.mark.parametrize("text", ["", "abc", "0", "-1", "1:99:99x"])
def test_parse_plan_time_rejects(text):
    with pytest.raises(ValueError):
        parse_plan_time(text)


# --- API -----------------------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path):
    settings = Settings(machines=(MachineConfig("m1", "DMG 1", "10.0.0.1"),), db_path=tmp_path / "c.db", simulate=True)
    app = create_app(settings, run_collectors=False)
    with TestClient(app) as c:
        yield c


def upload(client, lines, name="doku_sp_2.pdf"):
    return client.post(
        "/api/orders/import-cam",
        content=make_pdf(lines),
        headers={"Content-Type": "application/pdf", "X-File-Name": name},
    )


SP2_LINES = [
    "02 DMU 70 Erowa 11:10 Uhr",
    "CAD-Datei: 21053 abdeckung rechts 1 cvo.cad",
    "26-21053-02-01 Abgearbeitet ? ( ) 03:00:15",
    "26-21053-02-02 Abgearbeitet ? ( ) 00:00:46",
]


def test_import_creates_order_and_plans(client):
    r = upload(client, SP2_LINES)
    assert r.status_code == 200
    body = r.json()
    assert (body["file"], body["setup"], body["machine"]) == ("doku_sp_2.pdf", 2, "DMU 70 Erowa")
    [order] = body["orders"]
    assert (order["key"], order["created"], order["title_set"], len(order["programs"])) == ("21053", True, True, 2)
    detail = client.get("/api/orders/21053").json()
    assert detail["order"]["title"] == "Abdeckung rechts 1 cvo"
    [setup] = detail["setups"]
    rows = [(p["name"], p["plan_s"], p["plan_source"], p["runs"]) for p in setup["programs"]]
    assert rows == [("26-21053-02-01", 10815, "pdf", 0), ("26-21053-02-02", 46, "pdf", 0)]
    assert (setup["plan_s"], setup["plan_complete"], detail["totals"]["plan_part_s"]) == (10861, True, 10861)

    # Zweiter Import: Bezeichnung bleibt, Planzeiten werden aktualisiert
    client.put("/api/orders/21053", json={"title": "Abdeckung rechts CVO 1"})
    r = upload(client, [*SP2_LINES[:2], "26-21053-02-01 Abgearbeitet ? ( ) 02:50:00"])
    assert r.json()["orders"][0]["created"] is False and r.json()["orders"][0]["title_set"] is False
    detail = client.get("/api/orders/21053").json()
    assert detail["order"]["title"] == "Abdeckung rechts CVO 1"
    assert detail["setups"][0]["programs"][0]["plan_s"] == 10200


def test_import_refuses_other_files(client):
    r = client.post("/api/orders/import-cam", content=b"kein pdf", headers={"X-File-Name": "liste.xlsx"})
    assert r.status_code == 400 and "liste.xlsx" in r.json()["detail"]


def test_plan_by_hand(client):
    upload(client, SP2_LINES)
    r = client.put("/api/orders/21053/plans", json={"program": "21-21053v2-02-01", "time": "4,5"})
    assert r.status_code == 200 and r.json()["planned_s"] == 16200
    versions = {v["version"]: v for v in client.get("/api/orders/21053").json()["versions"]}
    [program] = versions["V2"]["setups"][0]["programs"]
    assert (program["name"], program["plan_s"], program["plan_source"]) == ("21-21053v2-02-01", 16200, "manual")
    assert client.put("/api/orders/21053/plans", json={"program": "26-4711-01-01", "time": "1"}).status_code == 400
    assert client.put("/api/orders/21053/plans", json={"program": "26-21053-02-01", "time": "bald"}).status_code == 400
    assert client.put("/api/orders/99999/plans", json={"program": "26-99999-01-01", "time": "1"}).status_code == 404
    client.put("/api/orders/21053/plans", json={"program": "21-21053v2-02-01", "time": ""})  # entfernen
    assert "V2" not in {v["version"] for v in client.get("/api/orders/21053").json()["versions"]}


# --- Prognose und Palettenliste --------------------------------------------------------------------

PROGRAM = "TNC:\\Programme\\21053\\21-21053v2-02-01.h"


def test_forecast_uses_the_plan_for_the_first_run(db, make_collector):
    db.ensure_order("21053", 2021, "21053", 0)
    db.set_plan("21-21053V2-02-01", "21-21053v2-02-01", "21053", 4.5 * 3600, "manual", 0)
    c = make_collector()
    feed(c, (0, snap("IDLE", PROGRAM)), (10, snap("STARTED", PROGRAM)), (3610, snap("STARTED", PROGRAM)))
    f = c.live()["forecast"]
    assert (f["method"], f["typical_run_s"], f["remaining_s"]) == ("plan", 16200, 16200 - 3600)


def test_test_start_far_below_the_plan_is_no_reference(db, make_collector):
    """DMU 70, 7.10.: 21-21053v2-02-01 nach 27 s abgebrochen und als „fertig“ gezählt."""
    db.ensure_order("21053", 2021, "21053", 0)
    c = make_collector()
    feed(c, (0, snap("IDLE", PROGRAM)), (10, snap("STARTED", PROGRAM)), (37, snap("FINISHED", PROGRAM)))
    db.set_plan("21-21053V2-02-01", "21-21053v2-02-01", "21053", 4.5 * 3600, "manual", 100)
    c.forget_plans()
    feed(c, (100, snap("IDLE", PROGRAM)), (110, snap("STARTED", PROGRAM)), (710, snap("STARTED", PROGRAM)))
    assert c.live()["forecast"]["method"] == "plan"  # nicht „history“ mit 27 s
    assert c._forecaster.typical_run_s(PROGRAM) is None


def test_plan_fills_the_pallet_list(db, make_collector):
    from .test_pallet import MACRO, TABLE, pal, program_calls, table

    c = make_collector()
    rows = [("PAL", "6"), ("PGM", "21-21053v2-02-01.h"), ("PAL", "7"), ("PGM", "21-21053v2-02-01.h")]
    text = table(rows)
    db.save_program_file("m1", TABLE, len(text), 1.0, None, None, 0, program_calls(TABLE, text), text)
    db.set_plan("21-21053V2-02-01", "21-21053v2-02-01", "21053", 4.5 * 3600, "pdf", 0)
    feed(c, (0, pal("IDLE", None)), (10, pal("STARTED", MACRO)))
    entries = c.live()["pallet"]["entries"]
    assert [(e["expected_s"], e["source"]) for e in entries] == [(16200, "plan"), (16200, "plan")]


def test_plan_names_ignore_the_folder():
    assert cam_import.call_name("TNC:\\X\\21-21053v2-02-01.h") == "21-21053V2-02-01"
    assert orders.parse_program("21-21053v2-02-01").key == "21053"
