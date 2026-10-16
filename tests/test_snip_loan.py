"""Snip, stage 2 (owner, 2026-10-16: "I can use it as well for bank loans when they give amortization table"): the bank's table snipped into the loan's
schedule through the same staging step as a schedule file; Due date / Amount / Interest named from the headings; each installment keeps the source mark."""
import io
import json

from conftest import make_creditor
from tests.test_web_d0 import _admin


def _db():
    import engines.db_engine as d
    return d.DB_PATH


SCHEDULE = [["No.", "Due date", "Principal", "Interest", "Amortization", "Balance"],
            ["1", "11/15/2026", "73,457.12", "21,250.00", "94,707.12", "2,926,542.88"],
            ["2", "12/15/2026", "73,977.44", "20,729.68", "94,707.12", "2,852,565.44"],
            ["3", "01/15/2027", "74,501.45", "20,205.67", "94,707.12", "2,778,063.99"]]


def _pdf(rows):
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(60, 740, "BANK OF THE PHILIPPINE ISLANDS - AMORTIZATION SCHEDULE")
    c.setFont("Helvetica", 9)
    x = [60, 100, 190, 280, 370, 460]
    y = 700
    for r in rows:
        for xi, cell in zip(x, r):
            c.drawString(xi + 3, y, cell)
        y -= 18
    c.showPage()
    c.save()
    return buf.getvalue()


def _loan(web, db):
    bank = make_creditor(db, "BPI", creditor_type="BANK_LOAN")
    r = web.post("/bank-loans/", {"action": "create", "bank": bank, "ref": "2026-0412", "principal": "3000000", "start": "2026-10-15", "maturity": "2029-10-15"})
    assert r.status_code == 302, r.content[:200]
    import sqlite3
    return sqlite3.connect(_db()).execute("SELECT bank_loan_id FROM bank_loans ORDER BY bank_loan_id DESC LIMIT 1").fetchone()[0]


def test_the_amortisation_table_lands_in_the_schedule_with_the_source_mark(web, db):
    import sqlite3

    from django.core.files.uploadedfile import SimpleUploadedFile
    from engines import snip
    _admin(web)
    blid = _loan(web, db)
    r = web.post(f"/bank-loans/{blid}/", {"action": "snip_upload", "file": SimpleUploadedFile("BPI amortization.pdf", _pdf(SCHEDULE), content_type="application/pdf")})
    assert r.status_code == 302 and "tab=snip&f=" in r["Location"]
    token = r["Location"].split("&f=")[1]
    page = web.get(f"/bank-loans/{blid}/?tab=snip&f={token}").content.decode()
    assert "Snip the bank" in page and 'id="sn_wrap"' in page and "BPI amortization.pdf" in page
    assert web.get(f"/bank-loans/{blid}/snip/{token}/page/1.jpg")["Content-Type"] == "image/jpeg"
    ws = snip.words(token, 1)
    tbl = [w for w in ws if w["t"] in ("No.", "Balance", "2,778,063.99")]
    box = {"x0": min(w["x0"] for w in tbl) - 6, "y0": min(w["y0"] for w in tbl) - 6, "x1": max(w["x1"] for w in tbl) + 6, "y1": max(w["y1"] for w in tbl) + 6}
    d = web.post(f"/bank-loans/{blid}/snip/{token}/read/", json.dumps({"page": 1, "mode": "table", "box": box}), content_type="application/json").json()
    assert d["ok"] and d["rows"] == SCHEDULE and d["header"] is True
    assert d["colmap"] == ["no", "due_date", "principal", "interest", "amount", "balance"]          # named from the headings
    r = web.post(f"/bank-loans/{blid}/", {"action": "snip_use", "token": token, "page": 1, "rows": json.dumps(d["rows"]), "colmap": json.dumps(d["colmap"]), "header": "1",
                                          **{k: str(v) for k, v in box.items()}})
    assert r.status_code == 302 and "tab=import" in r["Location"]
    staged = web.get(f"/bank-loans/{blid}/?tab=import").content.decode()
    assert "before anything is saved" in staged and "94,707.12" in staged and "21,250.00" in staged and "(snipped)" in staged
    r = web.post(f"/bank-loans/{blid}/", {"action": "import"})
    assert r.status_code == 302
    rows = sqlite3.connect(_db()).execute("SELECT due_date, amount, interest_portion, snip_id FROM bank_loan_installments WHERE bank_loan_id = ? ORDER BY due_date", (blid,)).fetchall()
    assert [(a, b, c) for a, b, c, _ in rows] == [("2026-11-15", 94707.12, 21250.0), ("2026-12-15", 94707.12, 20729.68), ("2027-01-15", 94707.12, 20205.67)]
    assert len({s for *_, s in rows}) == 1 and rows[0][3]                                           # every row keeps the snip
    sched = web.get(f"/bank-loans/{blid}/?tab=schedule").content.decode()
    assert f"/bank-loans/snip/source/{rows[0][3]}/" in sched
    src = web.get(f"/bank-loans/snip/source/{rows[0][3]}/").content.decode()
    assert "BPI amortization.pdf" in src and 'class="snip-box"' in src and f"/bank-loans/{blid}/?tab=snip" in src
    assert web.get(f"/price-lists/snip/source/{rows[0][3]}/").status_code == 404                     # a loan's snip is not a price's


def test_rows_the_snip_cannot_read_are_said_not_dropped_silently():
    from engines.snip import guess_loan_colmap, loan_rows
    rows = [["Due date", "Amortization"], ["11/15/2026", "94,707.12"], ["not a date", "94,707.12"], ["01/15/2027", "abc"], ["", ""]]
    assert guess_loan_colmap(rows) == ["due_date", "amount"]
    sched, problems = loan_rows(rows, ["due_date", "amount"], True)
    assert sched == [{"due": "2026-11-15", "amount": 94707.12, "interest": 0.0}] and len(problems) == 2
    assert loan_rows(rows, ["", "amount"], True) == ([], ["Name the Due date and the Amount columns."])
    assert guess_loan_colmap([["15-Nov-26", "94,707.12", "21,250.00"], ["15-Dec-26", "94,707.12", "20,729.68"]]) == ["due_date", "amount", "interest"]   # no headings
