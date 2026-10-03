"""Stage D3b of the Django rebuild (Payables bulk tools, docs/mockups/d3_payables.html screen 6): invoices from a spreadsheet or from photos/PDFs into one batch,
many batches from one Excel workbook, many batches printed as one PDF, and the vendor SOA matcher. Each reads first; nothing is saved until the final button."""
import io

from tests.conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_d3a import _add_inv, _new_batch


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _file(data: bytes, name: str):
    f = io.BytesIO(data)
    f.name = name
    return f


def test_invoices_from_a_spreadsheet_are_checked_then_added(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines", terms="30")
    sid = _new_batch(web, cid)
    csv = b"Sales invoice no,Date,Amount,Less %\nSI-501,2026-10-01,1000,\nSI-502,2026-10-02,2500,5\n"
    r = web.post(f"/order-statements/{sid}/import/", {"action": "read_sheet", "file": _file(csv, "si.csv")})
    assert "staged=" in r["Location"]
    page = web.get(r["Location"]).content.decode()
    assert "2 line(s) read" in page and 'value="SI-502"' in page
    assert fetchone(_db(), "SELECT COUNT(*) FROM invoices WHERE statement_id = ?", (sid,))[0] == 0          # nothing saved by reading
    token = r["Location"].split("staged=")[1]
    form = {"action": "add", "staged": token, "promote": "1"}
    for i, (doc, day, gross, less) in enumerate([("SI-501", "2026-10-01", "1000", "0"), ("SI-502", "2026-10-02", "2500", "5")]):
        form.update({f"keep_{i}": "1", f"doc_{i}": doc, f"date_{i}": day, f"gross_{i}": gross, f"less_{i}": less})
    web.post(f"/order-statements/{sid}/import/", form)
    assert fetchone(_db(), "SELECT COUNT(*), SUM(amount) FROM invoices WHERE statement_id = ?", (sid,)) == (2, 3375.0)
    assert " - OS " in fetchone(_db(), "SELECT statement_ref FROM order_statements WHERE statement_id = ?", (sid,))[0]


def test_a_double_entry_asks_and_keeps_the_corrections(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="SI-900", gross="1000", date="2026-10-01")
    r = web.post(f"/order-statements/{sid}/import/", {"action": "read_sheet", "file": _file(b"Invoice no,Date,Amount\nSI-900,2026-10-01,1000\n", "x.csv")})
    token = r["Location"].split("staged=")[1]
    r = web.post(f"/order-statements/{sid}/import/", {"action": "add", "staged": token, "keep_0": "1", "doc_0": "SI-900", "date_0": "2026-10-01", "gross_0": "1000", "less_0": ""},
                 follow=True)
    assert b"Is this a double entry?" in r.content or b"already exists for this supplier" in r.content
    assert fetchone(_db(), "SELECT COUNT(*) FROM invoices WHERE doc_ref = 'SI-900'")[0] == 1          # the hard stop holds


def test_invoices_from_a_pdf_are_read_offline(web, db):
    from reportlab.pdfgen import canvas
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setFont("Helvetica", 14)
    c.drawString(72, 760, "SALES INVOICE No. 004521")
    c.drawString(72, 730, "Date: 10/01/2026")
    c.drawString(72, 700, "Total Amount Due: 12,345.00")
    c.save()
    r = web.post(f"/order-statements/{sid}/import/", {"action": "read_ocr", "files": _file(buf.getvalue(), "si_004521.pdf")})
    page = web.get(r["Location"]).content.decode()
    assert "si_004521.pdf" in page and ("line(s) read" in page)


def test_many_batches_from_one_workbook(web, db):
    from openpyxl import Workbook
    _admin(web)
    a, b = make_creditor(db, "Goodyear Philippines"), make_creditor(db, "Motolite")
    wb = Workbook()
    ws = wb.active
    ws.title = "Goodyear Philippines"
    ws.append(["Doc Ref", "Date", "Amount", "Less %"])
    ws.append(["SI-1", "2026-10-01", 1000, 0])
    ws2 = wb.create_sheet("Motolite Oct")
    ws2.append(["Doc Ref", "Date", "Amount", "Less %"])
    ws2.append(["SI-2", "2026-10-01", 500, 0])
    ws2.append(["SI-3", "2026-10-02", 700, 0])
    buf = io.BytesIO()
    wb.save(buf)
    r = web.post("/order-statements/import-many/", {"action": "read", "file": _file(buf.getvalue(), "entry.xlsx")})
    page = web.get(r["Location"]).content.decode()
    assert "Goodyear Philippines" in page and "Motolite Oct" in page
    token = r["Location"].split("staged=")[1]
    r = web.post("/order-statements/import-many/", {"action": "create", "staged": token, "sup_0": str(a), "sup_1": str(b), "promote": "1"})
    done = web.get(r["Location"]).content.decode()
    assert "Done: the new batches are below." in done
    assert fetchone(_db(), "SELECT COUNT(*) FROM order_statements WHERE creditor_id = ? AND statement_ref LIKE '% - OS %'", (b,))[0] == 1
    assert fetchone(_db(), "SELECT COUNT(*) FROM invoices WHERE creditor_id = ?", (b,))[0] == 2


def test_print_many_as_one_pdf(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    s1, s2 = _new_batch(web, cid), _new_batch(web, cid)
    _add_inv(web, s1, doc="SI-A")
    _add_inv(web, s2, doc="SI-B")
    page = web.get("/order-statements/print-many/?scope=all").content.decode()
    assert f'value="{s1}"' in page and f'value="{s2}"' in page
    r = web.get(f"/order-statements/print-many/?go=1&scope=all&ids={s1}&ids={s2}&type=a4&copies=supplier&summary=1&history=1&unprinted=1")
    assert r.status_code == 200 and r.content[:4] == b"%PDF"
    t = web.get(f"/order-statements/print-many/?go=1&scope=all&ids={s1}&type=thermal")
    assert t.status_code == 200 and t.content[:4] == b"%PDF"


def test_vendor_soa_matcher(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="SI-77", gross="1000")
    soa = b"Invoice Number,Amount\nSI-77,1000\nSI-78,500\n"
    page = web.post("/order-statements/soa/", {"s": cid, "file": _file(soa, "soa.csv")}).content.decode()
    assert "Matched" in page and "Missing in AP" in page and "SI-78" in page


def test_roles(web, db):
    _admin(web)
    buyer = _sign_in_new("buyer@slicks.test", "purchasing")
    for url in ("/order-statements/import-many/", "/order-statements/print-many/", "/order-statements/soa/"):
        assert buyer.get(url).status_code == 403, url
