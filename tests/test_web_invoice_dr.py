"""Invoice vs delivery receipt on the batch's "Add invoices from files" screen (Phase 5, owner 2026-10-08): a DR column read from a spreadsheet is shown
in its own box and saved beside the sales invoice (stored without the 'DR' prefix; the screens show 'DR' beside it)."""
import io

from conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin
from tests.test_web_d3a import _new_batch


def test_the_spreadsheet_import_saves_the_dr_beside_the_invoice(web, db):
    import engines.db_engine as d
    _admin(web)
    sid = _new_batch(web, make_creditor(db, "Goodyear Philippines", terms="30"))
    f = io.BytesIO(b"Sales Invoice No,DR No,Date,Amount\nSI-100,DR-77,2026-09-01,1000\n")
    f.name = "x.csv"
    r = web.post(f"/order-statements/{sid}/import/", {"action": "read_sheet", "file": f})
    token = r["Location"].split("staged=")[1]
    assert 'value="DR-77"' in web.get(r["Location"]).content.decode()
    web.post(f"/order-statements/{sid}/import/", {"action": "add", "staged": token, "keep_0": "1", "doc_0": "SI-100", "dr_0": "DR-77",
                                                   "date_0": "2026-09-01", "gross_0": "1000", "less_0": "0"})
    assert fetchone(d.DB_PATH, "SELECT doc_ref, dr_no FROM invoices") == ("SI-100", "77")


def test_an_invoice_needs_its_si_or_its_dr(web, db):
    # owner, 2026-10-09: "suppliers do not provide sales invoice ... it must have document reference either SI or DR"
    from tests.test_web_d0 import _admin
    from tests.test_web_d3a import _new_batch
    from conftest import fetchone, make_creditor
    import engines.db_engine as d
    _admin(web)
    sid = _new_batch(web, make_creditor(db, "Goodyear Philippines", terms="30"))
    web.post(f"/order-statements/{sid}/", {"action": "add_invoice", "doc": "", "dr": "342", "date": "2026-10-09", "gross": "5,000"})
    assert fetchone(d.DB_PATH, "SELECT doc_ref, dr_no FROM invoices WHERE statement_id = ?", (sid,)) == ("DR 342", "342")
    web.post(f"/order-statements/{sid}/", {"action": "add_invoice", "doc": "", "dr": "", "date": "2026-10-09", "gross": "1,000"})
    assert fetchone(d.DB_PATH, "SELECT COUNT(*) FROM invoices WHERE statement_id = ?", (sid,))[0] == 1          # neither: refused
    page = web.get(f"/order-statements/{sid}/").content.decode()
    assert "DR 342" in page and "SI no. or the DR no." in page
