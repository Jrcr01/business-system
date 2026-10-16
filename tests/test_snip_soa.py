"""Snip, stage 3 (owner, 2026-10-16): the supplier's statement of account snipped on the Vendor SOA matcher: the SI / document no. and the amount named
from the headings or their content, the statement's total checked against the sum of the lines, the same match as a file; the document deleted after.
The old whole-page reader for PDFs and pictures is retired: such a file is pointed to the snip."""
import io
import json

from conftest import make_creditor
from tests.test_web_d0 import _admin
from tests.test_web_d3a import _add_inv, _new_batch

SOA = [["Date", "SI No.", "Amount", "Balance"],
       ["08/03/2026", "244911", "25,079.20", "25,079.20"],
       ["08/04/2026", "244983", "6,174.00", "31,253.20"],
       ["09/03/2026", "245943", "103,573.59", "134,826.79"]]


def _pdf(rows, total="134,826.79"):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(60, 740, "SUNNY TIRE SUPPLY - STATEMENT OF ACCOUNT")
    c.setFont("Helvetica", 10)
    x = [60, 160, 280, 400]
    y = 700
    for r in rows:
        for xi, cell in zip(x, r):
            c.drawString(xi + 3, y, cell)
        y -= 18
    c.setFont("Helvetica-Bold", 10)
    c.drawString(280, y - 10, "TOTAL DUE")
    c.drawString(400, y - 10, total)
    c.showPage()
    c.save()
    return buf.getvalue()


def test_the_statement_is_snipped_matched_and_the_total_checked(web, db):
    from django.core.files.uploadedfile import SimpleUploadedFile
    from engines import snip
    _admin(web)
    cid = make_creditor(db, "Sunny Tire Supply", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="244911", gross="25,079.20")
    _add_inv(web, sid, doc="244983", gross="6,200")                              # differs from the statement
    _add_inv(web, sid, doc="245000", gross="1,000")                              # not on the statement
    r = web.post("/order-statements/soa/", {"s": cid, "action": "snip_upload", "file": SimpleUploadedFile("Sunny SOA Sept.pdf", _pdf(SOA), content_type="application/pdf")})
    assert r.status_code == 302 and "&f=" in r["Location"]
    token = r["Location"].split("&f=")[1]
    page = web.get(f"/order-statements/soa/?s={cid}&f={token}").content.decode()
    assert "Snip from the statement" in page and 'id="sn_wrap"' in page and "Total on the statement" in page
    ws = snip.words(token, 1)
    tbl = [w for w in ws if w["t"] in ("Date", "Balance", "245943")]
    box = {"x0": min(w["x0"] for w in tbl) - 6, "y0": min(w["y0"] for w in tbl) - 6, "x1": max(w["x1"] for w in ws if w["t"] == "134,826.79") + 6, "y1": max(w["y1"] for w in tbl) + 6}
    d = web.post(f"/order-statements/soa/snip/{token}/read/", json.dumps({"page": 1, "mode": "table", "box": box}), content_type="application/json").json()
    assert d["ok"] and d["rows"] == SOA and d["header"] is True and d["colmap"] == ["date", "ref", "amount", "balance"]
    low = max(w["y1"] for w in ws if w["t"] == "245943")
    tot = [w for w in ws if w["t"] in ("TOTAL", "DUE", "134,826.79") and w["y0"] > low]
    t = web.post(f"/order-statements/soa/snip/{token}/read/", json.dumps({"page": 1, "mode": "text", "box": {"x0": min(w["x0"] for w in tot) - 4, "y0": min(w["y0"] for w in tot) - 4,
                                                                                                                "x1": max(w["x1"] for w in tot) + 4, "y1": max(w["y1"] for w in tot) + 4}}), content_type="application/json").json()
    assert t["ok"] and t["text"] == "TOTAL DUE 134,826.79"
    r = web.post("/order-statements/soa/", {"s": cid, "action": "snip_match", "token": token, "page": 1, "rows": json.dumps(d["rows"]), "colmap": json.dumps(d["colmap"]),
                                            "header": "1", "total": "134,826.79"})
    page = r.content.decode()
    assert r.status_code == 200 and "equals the sum of the snipped lines" in page
    assert "244911" in page and "245943" in page and "244983" in page and "245000" in page
    assert ">1<" in page and "Matched" in page and "Amount mismatches" in page and "Omitted from the SOA" in page and "Missing in AP" in page
    assert snip.file_info(token) is None                                          # the statement is deleted once matched
    # a wrong total is said
    r = web.post("/order-statements/soa/", {"s": cid, "action": "snip_upload", "file": SimpleUploadedFile("again.pdf", _pdf(SOA), content_type="application/pdf")})
    token = r["Location"].split("&f=")[1]
    page = web.post("/order-statements/soa/", {"s": cid, "action": "snip_match", "token": token, "page": 1, "rows": json.dumps(SOA), "colmap": json.dumps(["date", "ref", "amount", "balance"]),
                                               "header": "1", "total": "130,000"}).content.decode()
    assert "differs from the sum of the snipped lines" in page


def test_the_old_whole_page_reader_is_retired_and_files_still_match(web, db):
    from tests.test_web_d3b import _file
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="SI-77", gross="1000")
    page = web.post("/order-statements/soa/", {"s": cid, "file": _file(b"Invoice Number,Amount\nSI-77,1000\nSI-78,500\n", "soa.csv")}).content.decode()
    assert "Matched" in page and "SI-78" in page                                 # the CSV path, as before
    page = web.post("/order-statements/soa/", {"s": cid, "file": _file(_pdf(SOA), "soa.pdf")}).content.decode()
    assert "Snip from the statement" in page and "drag a box over the table" in page   # a PDF through the file form: pointed to the snip


def test_soa_rows_and_the_column_guesses():
    from engines.snip import guess_soa_colmap, soa_rows
    assert guess_soa_colmap([["08/03/2026", "244911", "25,079.20", "25,079.20"], ["08/04/2026", "244983", "6,174.00", "31,253.20"]]) == ["date", "ref", "amount", "balance"]
    assert guess_soa_colmap([["Invoice No", "Debit"], ["SI-77", "1,000.00"]]) == ["ref", "amount"]
    lines, problems = soa_rows(SOA + [["", "", "134,826.79", ""], ["09/05/2026", "245999", "abc", ""]], ["date", "ref", "amount", "balance"], True)
    assert [l["ref"] for l in lines] == ["244911", "244983", "245943", "245999"] and lines[0]["amount"] == 25079.2 and lines[-1]["amount"] == 0.0
    assert len(problems) == 2 and "total line" in problems[0] and "counted as 0" in problems[1]
    assert soa_rows(SOA, ["date", "", "amount", ""], True) == ([], ["Name the SI / document no. and the Amount columns."])
