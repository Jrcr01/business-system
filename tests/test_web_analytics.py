"""Phase 8 (owner, 2026-10-09; SAP Business One style): the Related documents panel (order -> receipt -> invoice -> batch -> check -> bank line, up and down from
the document, never sideways) and the Analytics section (Payables, Lenders, Bank and cash, Purchasing), shown to people whose Reports switch is on."""
import sqlite3

from conftest import make_creditor
from tests.test_web_d0 import _admin
from tests.test_web_d3a import _add_inv, _new_batch


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def test_a_batch_shows_its_chain_from_invoice_to_bank_line(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="INV-77", gross="10,000")
    _add_inv(web, sid, doc="INV-78", gross="5,000")
    web.post(f"/order-statements/{sid}/", {"action": "chk_park", "d1": "2026-11-01", "a1": "15,000"})
    with sqlite3.connect(_db()) as conn:
        chk = conn.execute("SELECT check_id FROM checks_issued WHERE statement_id = ?", (sid,)).fetchone()[0]
        imp = conn.execute("INSERT INTO bank_statement_imports (bank_account_id, file_name, file_hash) VALUES (1, 's.csv', 'h')").lastrowid
        conn.execute("UPDATE checks_issued SET check_number = '0004471' WHERE check_id = ?", (chk,))
        conn.execute("""INSERT INTO bank_transactions (import_id, txn_date, posting_date, description, debit, credit, kind, line_key, matched_check_id, match_status)
                        VALUES (?, '2026-11-02', '2026-11-02', 'CHECK 0004471', 15000, 0, 'CHECK', 'k1', ?, 'matched')""", (imp, chk))
    page = web.get(f"/order-statements/{sid}/").content.decode()
    assert f'data-rmap="/related/statement/{sid}/"' in page and 'class="rmap"' not in page         # a button, not laid out on the page
    page = web.get(f"/related/statement/{sid}/").content.decode()            # the pop-up's content, fetched by the button
    assert "SI INV-77" in page and "SI INV-78" in page and "Check 0004471" in page and "Posted: 11/02/2026" in page

    from engines.doc_trail import trail
    inv = sqlite3.connect(_db()).execute("SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-77'").fetchone()[0]
    cols = {c["label"]: [d["title"] for d in c["docs"]] for c in trail("invoice", inv)}
    assert cols["This sales invoice"] == ["SI INV-77"]                   # never sideways: INV-78 is not on INV-77's chain
    assert cols["Check"] == ["Check 0004471"] and cols["Purchase order"] == []


def test_a_loan_note_shows_its_payments_and_checks(web, db):
    _admin(web)
    cid = make_creditor(db, "Richie", creditor_type="LENDER")
    web.post("/lenders/", {"lender": cid, "issue_date": "2026-11-01", "principal": "100,000", "rate": "3", "d1": "2026-12-01", "p1": "100,000"})
    lnid = sqlite3.connect(_db()).execute("SELECT MAX(loan_note_id) FROM loan_notes").fetchone()[0]
    web.post(f"/lenders/{lnid}/", {"action": "park_all"})
    assert f'data-rmap="/related/loan/{lnid}/"' in web.get(f"/lenders/{lnid}/").content.decode()
    page = web.get(f"/related/loan/{lnid}/").content.decode()
    assert "Payment 12/01/2026" in page and "103,000.00" in page
    assert web.get(f"/related/nothing/{lnid}/").status_code == 404 and "103,000.00" in page


def test_analytics_tabs_draw_and_follow_the_reports_switch(web, db):
    _admin(web)
    make_creditor(db, "Goodyear Philippines")
    for tab in ("payables", "lenders", "bank", "purchasing"):
        page = web.get(f"/analytics/?tab={tab}&period=year").content.decode()
        assert "could not be drawn" not in page, tab
        assert 'class="kpi"' in page
    home = web.get("/").content.decode()
    assert 'id="g-analytics"' in home and home.count("/analytics/?tab=") >= 8     # Analytics: its section on Home (4 cards) and the top bar's 4 entries
    from engines.permissions import PAGE_ACTION
    assert PAGE_ACTION["analytics"] == "reports.use"                     # the same switch as Reports
