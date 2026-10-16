"""Snip from a document (owner, 2026-10-16: like DataSnipper, inside Price lists): a PDF kept beside the database; a box on a page read exactly from the
text layer (a ruled table, or words by position); the columns named from the category's fields; the rows checked and imported through the Excel
import's own path; the document deleted after use. OCR (a picture) is skipped where the OCR program is not installed."""
import io
import json
import shutil

import pytest

from tests.test_web_d0 import _admin
from tests.test_web_d2b import _supplier_with_price


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _pdf(rows, ruled=True, title="WESTLAKE TBR PRICE LIST"):
    """A one-page PDF with a table, drawn with reportlab (real text, like the suppliers' files)."""
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(60, 740, title)
    c.setFont("Helvetica", 10)
    x = [60, 260, 400]
    y = 700
    for r in rows:
        for xi, cell in zip(x, r):
            c.drawString(xi + 4, y, cell)
        if ruled:
            c.line(56, y - 5, 480, y - 5)
        y -= 20
    if ruled:
        c.line(56, 715, 480, 715)
        for xi in (56, 256, 396, 480):
            c.line(xi, 715, xi, y + 15)
    c.showPage()
    c.save()
    return buf.getvalue()


ROWS = [["Size", "Pattern", "Price"], ["315/80R22.5-20PR Lug", "MD777", "16,800"], ["295/80R22.5-18PR Mix", "AZ676", "14,700"], ["11R22.5-16PR Rib", "CR960A", "13,200"]]


def _open(web, cid, ruled=True):
    from django.core.files.uploadedfile import SimpleUploadedFile
    r = web.post("/price-lists/snip/upload/", {"s": cid, "file": SimpleUploadedFile("Westlake - TBR.pdf", _pdf(ROWS, ruled), content_type="application/pdf")})
    assert r.status_code == 302 and "&f=" in r["Location"], r.get("Location")
    return r["Location"].split("&f=")[1].split("&")[0]


def _box(token, web):
    from engines import snip
    ws = snip.words(token, 1)
    tbl = [w for w in ws if w["t"] in ("Size", "16,800", "13,200")]
    return {"x0": min(w["x0"] for w in tbl) - 8, "y0": min(w["y0"] for w in tbl) - 8, "x1": max(w["x1"] for w in tbl) + 8, "y1": max(w["y1"] for w in tbl) + 8}


@pytest.mark.parametrize("ruled", [True, False])
def test_a_box_on_a_text_pdf_gives_the_table_exactly_with_its_columns_named(web, db, ruled):
    from engines import snip
    _admin(web)
    cid = _supplier_with_price(web, db, "New Century City Marketing Corp.")
    token = _open(web, cid, ruled)
    info = snip.file_info(token)
    assert info["pages"] == 1 and info["has_text"] and info["path"].exists()
    page = web.get(f"/price-lists/?s={cid}&tab=snip&f={token}").content.decode()
    assert "Snip from a document" in page and 'id="sn_wrap"' in page and "real text: exact" in page
    import sqlite3
    with sqlite3.connect(_db()) as conn:                                             # a day-old document never imported is swept when the next one is opened
        conn.execute("UPDATE snip_files SET created_at = '2026-01-01 00:00:00' WHERE token = ?", (token,))
    token2 = _open(web, cid, ruled)
    assert snip.file_info(token) is None and snip.file_info(token2) is not None
    token = token2
    info = snip.file_info(token)
    assert web.get(f"/price-lists/snip/{token}/page/1.jpg")["Content-Type"] == "image/jpeg"
    box = _box(token, web)
    r = web.post(f"/price-lists/snip/{token}/read/", json.dumps({"page": 1, "mode": "table", "box": box, "category": "Tires"}), content_type="application/json").json()
    assert r["ok"] and r["how"] in (("ruled", "positions") if ruled else ("positions",)), r
    assert r["rows"] == ROWS and r["header"] is True and r["colmap"] == ["size", "pattern", "list_price"]
    assert not any(any(d) for d in r["doubt"])                                   # a text PDF: nothing doubtful
    two = [w for w in snip.words(token, 1) if w["t"] in ("14,700", "13,200")]
    t = web.post(f"/price-lists/snip/{token}/read/", json.dumps({"page": 1, "mode": "text", "box": {"x0": min(w["x0"] for w in two) - 4, "y0": min(w["y0"] for w in two) - 4,
                                                                                                        "x1": max(w["x1"] for w in two) + 4, "y1": max(w["y1"] for w in two) + 4}}), content_type="application/json").json()
    assert t["ok"] and t["text"] == "14,700 13,200"


def test_the_rows_go_through_the_import_check_and_the_document_is_deleted_after(web, db):
    from engines import snip
    from engines.common import get_conn
    _admin(web)
    cid = _supplier_with_price(web, db, "New Century City Marketing Corp.")
    token = _open(web, cid)
    box = _box(token, web)
    r = web.post(f"/price-lists/snip/{token}/check/", {"s": cid, "page": 1, "rows": json.dumps(ROWS), "colmap": json.dumps(["size", "pattern", "list_price"]), "header": "1",
                                                      "category": "Tires", "brand": "Westlake", "start": "2026-09-15", **{k: str(v) for k, v in box.items()}})
    assert r.status_code == 302 and "tab=import" in r["Location"] and "snip=" in r["Location"]
    check = web.get(r["Location"]).content.decode()
    assert "The file, before anything is saved" in check and "Westlake" in check and "<b>3</b> new" in check     # the Excel import's own check screen
    file_token = r["Location"].split("file=")[1].split("&")[0]
    snip_id = int(r["Location"].split("snip=")[1].split("&")[0])
    r2 = web.post("/price-lists/", {"s": cid, "tab": "import", "action": "import_file", "file": file_token, "start": "2026-09-15", "snip": str(snip_id)})
    assert r2.status_code == 302
    with get_conn() as conn:
        saved = conn.execute("SELECT item_name, unit_price FROM supplier_price_list WHERE creditor_id = ? AND effective_date = '2026-09-15' ORDER BY item_name", (cid,)).fetchall()
    assert len(saved) == 3 and {float(v) for _, v in saved} == {16800.0, 14700.0, 13200.0} and all("Westlake" in n for n, _ in saved)
    assert snip.file_info(token) is None and not list(snip.folder().glob(f"{token}*"))          # the document is gone once its rows are imported (owner: "no need to store")
    assert snip.snip_info(snip_id) is None


def test_a_printed_size_cell_is_split_into_size_ply_terrain_and_the_rest():
    from engines.snip import split_size_cell
    assert split_size_cell("315/80R22.5-20PR Lug") == {"size": "315/80R22.5", "ply": "20PR", "terrain": "Lug", "rest": ""}
    assert split_size_cell("12.00R20-18ply MIX") == {"size": "12.00R20", "ply": "18PR", "terrain": "Mix", "rest": ""}
    assert split_size_cell("12.00-20-18 Lug Mining") == {"size": "12.00-20", "ply": "18PR", "terrain": "Lug", "rest": "Mining"}
    assert split_size_cell("12.00R20-22 Mix HEAVY DUTY NEW") == {"size": "12.00R20", "ply": "22PR", "terrain": "Mix", "rest": "HEAVY DUTY NEW"}
    assert split_size_cell("7.50R16-14 Rib TUBELESS") == {"size": "7.50R16", "ply": "14PR", "terrain": "Rib", "rest": "TUBELESS"}
    assert split_size_cell("23.5-25-20PR (TUBETYPE)") == {"size": "23.5-25", "ply": "20PR", "terrain": "", "rest": "(TUBETYPE)"}
    assert split_size_cell("LT255/55R19-10PR") == {"size": "LT255/55R19", "ply": "10PR", "terrain": "", "rest": ""}
    assert split_size_cell("Drifting Tires")["size"] == "" and split_size_cell("Drifting Tires")["rest"] == "Drifting Tires"


def test_a_picture_is_read_by_ocr_where_the_program_exists_and_a_bad_file_is_refused(web, db):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from engines import snip
    _admin(web)
    cid = _supplier_with_price(web, db, "New Century City Marketing Corp.")
    r = web.post("/price-lists/snip/upload/", {"s": cid, "file": SimpleUploadedFile("notes.txt", b"hello", content_type="text/plain")}, follow=True)
    assert b"Choose a PDF or a picture" in r.content
    r = web.post("/price-lists/snip/upload/", {"s": cid, "file": SimpleUploadedFile("broken.pdf", b"%PDF-1.4 nothing", content_type="application/pdf")}, follow=True)
    assert b"could not be opened" in r.content
    token = _open(web, cid)
    from PIL import Image
    img = Image.open(snip.page_image(token, 1))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    r = web.post("/price-lists/snip/upload/", {"s": cid, "file": SimpleUploadedFile("photo.jpg", buf.getvalue(), content_type="image/jpeg")})
    tok2 = r["Location"].split("&f=")[1]
    assert not snip.file_info(tok2)["has_text"] and snip.words(tok2, 1) == []
    assert "picture: OCR" in web.get(f"/price-lists/?s={cid}&tab=snip&f={tok2}").content.decode()
    if not shutil.which("tesseract"):
        pytest.skip("Tesseract is not installed here (it is on the server)")
    W, H = snip.page_size(tok2, 1)
    r = snip.read_box(tok2, 1, (40, 60, W - 40, H / 2))
    assert r["how"] == "ocr" and len(r["rows"]) >= 3 and any("MD777" in c for row in r["rows"] for c in row)


def test_the_lines_of_a_ruled_table_in_a_picture_are_found():
    from PIL import Image, ImageDraw
    from engines.snip import _clean_cell, table_lines
    img = Image.new("L", (600, 400), 255)
    d = ImageDraw.Draw(img)
    for y in (50, 90, 130, 170, 210):
        d.line((40, y, 560, y), fill=0, width=2)
    for x in (40, 200, 380, 560):
        d.line((x, 50, x, 210), fill=0, width=2)
    hl, vl = table_lines(img, (20, 20, 580, 240))
    assert [round(v) for v in hl] == [50, 90, 130, 170, 210] and [round(v) for v in vl] == [40, 200, 380, 560]
    assert table_lines(img, (300, 300, 500, 380)) == ([], [])
    assert _clean_cell("| 5,720,") == "5,720" and _clean_cell("|BluEarth ES32 |") == "BluEarth ES32" and _clean_cell("—_ —-") == "" and _clean_cell(". 5,220") == "5,220"


def test_a_slow_read_runs_in_the_background_and_the_page_checks_back(web, db, monkeypatch):
    # 2026-10-16: OCR of a photographed page takes a minute or two on the server's one CPU; the request answers at once and the page asks again
    import time

    from engines import snip
    _admin(web)
    cid = _supplier_with_price(web, db, "New Century City Marketing Corp.")
    token = _open(web, cid)
    box = _box(token, web)
    monkeypatch.setattr(snip, "needs_job", lambda t: True)                       # treat the text PDF as if it needed OCR
    r = web.post(f"/price-lists/snip/{token}/read/", json.dumps({"page": 1, "mode": "table", "box": box, "category": "Tires"}), content_type="application/json").json()
    assert r["ok"] and r["pending"] and r["job"]
    for _ in range(50):
        d = web.post(f"/price-lists/snip/{token}/read/", json.dumps({"job": r["job"], "category": "Tires"}), content_type="application/json").json()
        if not d.get("pending"):
            break
        time.sleep(0.2)
    assert d["ok"] and d["rows"] == ROWS and d["colmap"] == ["size", "pattern", "list_price"]
    assert web.post(f"/price-lists/snip/{token}/read/", json.dumps({"job": "0" * 16}), content_type="application/json").status_code == 404
    lost = snip._jobs() / "abcdef0123456789.json"
    lost.write_text(json.dumps({"state": "running", "started": time.time() - 3600}), encoding="utf-8")
    d = web.post(f"/price-lists/snip/{token}/read/", json.dumps({"job": "abcdef0123456789"}), content_type="application/json").json()
    assert not d["ok"] and "given up" in d["msg"]
