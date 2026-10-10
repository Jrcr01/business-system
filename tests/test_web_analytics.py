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


# --- the relationship map, improved (owner, 2026-10-11, from the map of a batch with 12 sales invoices) ---------------------------------------------

def _batch_with(web, db, n, name="Battery Depot of Central Luzon Inc."):
    cid = make_creditor(db, name, terms="30")
    sid = _new_batch(web, cid)
    for i in range(n):
        _add_inv(web, sid, doc=f"INV-{i + 1}", gross=f"{10 + i},000", date=f"2026-09-{8 + i:02d}")       # different amounts and dates: not look-alikes
    return cid, sid


def test_a_long_step_is_one_card_that_opens_and_each_step_shows_its_total(web, db):
    _admin(web)
    cid, sid = _batch_with(web, db, 5)
    page = web.get(f"/related/statement/{sid}/").content.decode()
    assert 'class="rgroup"' in page and "5 sales invoices" in page and "60,000.00" in page          # one summary card for the five
    assert all(f"SI INV-{i}" in page for i in range(1, 6))                                          # the cards are inside it
    assert "5 · ₱60,000.00" in page                                                                # the count and total under the step's name
    from engines.doc_trail import trail
    col = next(c for c in trail("statement", sid) if c["key"] == "invoice")
    assert col["grouped"] and (col["count"], col["total"], col["done_count"]) == (5, 60_000.0, 0) and len(col["rest"]) == 5
    inv = sqlite3.connect(_db()).execute("SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-1'").fetchone()[0]
    assert 'class="rgroup"' not in web.get(f"/related/invoice/{inv}/").content.decode()             # a short step is drawn as before


def test_the_line_on_top_says_where_it_stands_and_what_is_next(web, db):
    from engines.doc_trail import trail, trail_summary
    _admin(web)
    cid, sid = _batch_with(web, db, 2)
    page = web.get(f"/related/statement/{sid}/").content.decode()
    assert 'class="rsum"' in page and "21,000.00 owed" in page and "no checks yet" in page and "Next: park checks." in page
    assert "Draft batch · Battery Depot of Central" in page                                     # the batch card names its supplier
    web.post(f"/order-statements/{sid}/", {"action": "chk_park", "d1": "2026-11-01", "a1": "21,000"})
    assert "Next: write 1 parked check." in trail_summary("statement", trail("statement", sid))
    with sqlite3.connect(_db()) as conn:
        conn.execute("UPDATE checks_issued SET check_number = '0004471' WHERE statement_id = ?", (sid,))
    s = trail_summary("statement", trail("statement", sid))
    assert "1 check ₱21,000.00" in s and "Next: wait for the bank on 1 check." in s


def test_no_po_is_allowed_and_a_document_is_coloured_by_its_due_date(web, db):
    from datetime import timedelta

    from engines.clock import manila_today
    from engines.doc_trail import trail
    _admin(web)
    cid, sid = _batch_with(web, db, 3)
    page = web.get(f"/related/statement/{sid}/").content.decode()
    assert page.count("No PO (allowed)") == 2 and "none yet" in page            # no order, no goods step: allowed; the check and bank steps are simply not there yet
    today = manila_today()
    with sqlite3.connect(_db()) as conn:
        for doc, days in (("INV-1", 30), ("INV-2", 3), ("INV-3", -2)):
            conn.execute("UPDATE invoices SET due_date = ? WHERE doc_ref = ?", ((today + timedelta(days=days)).isoformat(), doc))
    docs = {d["title"]: (d["status"], d["tone"]) for d in next(c for c in trail("statement", sid) if c["key"] == "invoice")["docs"]}
    assert docs == {"SI INV-1": ("Not due", "quiet"), "SI INV-2": ("Due soon", "soon"), "SI INV-3": ("Overdue", "late")}
    page = web.get(f"/related/statement/{sid}/").content.decode()
    assert "Overdue" in page and "Due soon" in page and "Not due" in page and "1 overdue" in page       # the line on top counts the overdue one
