"""Invoices on their own, paid through a payment statement (owner, 2026-10-13: "invoices can be input seperately not thru batch or order statement.
then in making checks i'll just pullup the floating invoices"; "order statement should not be appropriate name but payment statement that can be
batch payment or single payment"). New numbers are PS and continue the OS count; one statement per supplier; a draft can be deleted."""
import sqlite3

from tests.conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_d3a import _db, _inv

HERE = "/order-statements/invoices/"


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _add(web, cid, doc, gross="10,000", dr="", date="2026-10-13", **extra):
    return web.post(HERE, {"action": "add", "s": cid, "doc": doc, "dr": dr, "date": date, "gross": gross, "less": "", **extra})


def test_an_invoice_is_recorded_on_its_own_and_is_owed_at_once(web, db):
    from engines.reports_service import get_payment_schedule_all_tracks
    _admin(web)
    cid = make_creditor(db, "New Century City Marketing Corp.")
    page = web.get("/").content.decode()
    assert "/order-statements/?view=invoices" in page and "Payment statements" in page and "Order statements" not in page
    assert web.get("/order-statements/?view=invoices")["Location"] == HERE
    assert "No invoice is waiting" in web.get(HERE).content.decode()
    assert _add(web, cid, "38531", "315,371.85", dr="219386")["Location"] == HERE
    assert fetchone(_db(), "SELECT statement_id, amount, dr_no, status FROM invoices WHERE doc_ref = '38531'") == (None, 315371.85, "219386", "Unpaid")
    page = web.get(HERE).content.decode()
    assert "219386 · SI 38531" in page and "No PO" in page and "315,371.85" in page and "Make checks for the ticked" in page
    sched = get_payment_schedule_all_tracks()
    assert float(sched[sched["stage"] == "invoice"]["amount"].sum()) == 315371.85               # owed from the moment it is recorded
    # the same number again is refused; one that looks like a double (same day, same amount) is asked about first
    _add(web, cid, "38531", "1")
    assert _n("SELECT COUNT(*) FROM invoices") == 1
    web.get(HERE)
    _add(web, cid, "38532", "315,371.85")
    page = web.get(HERE).content.decode()
    assert "Check before saving" in page and "Save anyway" in page and _n("SELECT COUNT(*) FROM invoices") == 1
    _add(web, cid, "38532", "315,371.85", confirmed="1")
    assert _n("SELECT COUNT(*) FROM invoices") == 2
    web.post(HERE, {"action": "delete", "invoice_id": _inv("38532")[0], "stamp": web.get(HERE + f"?edit={_inv('38532')[0]}").content.decode().split('name="stamp" value="')[1].split('"')[0]})
    assert _n("SELECT COUNT(*) FROM invoices") == 1
    page = web.get("/order-statements/").content.decode()
    assert "1 invoice</b> not yet in a payment statement" in page and "New payment statement for" not in page     # no empty starter any more


def test_making_checks_makes_one_payment_statement_per_supplier_and_numbers_continue_as_ps(web, db):
    from engines.db_engine import promote_draft_statement
    _admin(web)
    a, b = make_creditor(db, "New Century City Marketing Corp."), make_creditor(db, "Battery Depot of Central Luzon Inc.")
    conn = sqlite3.connect(db)                                                                # a statement numbered before the rename
    conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, '2026 - OS 0031', 0, 'Issued')", (a,))
    conn.commit()
    conn.close()
    for cid, doc, gross in ((a, "A1", "10,000"), (a, "A2", "12,000"), (b, "B1", "7,000")):
        _add(web, cid, doc, gross)
    ids = {d: _inv(d)[0] for d in ("A1", "A2", "B1")}
    assert web.post(HERE, {"action": "pay"})["Location"] == HERE                              # nothing ticked: nothing made
    r = web.post(HERE, {"action": "pay", "pay": [ids["A1"]]})                                 # one supplier: straight to its checks
    one = _n("SELECT statement_id FROM invoices WHERE doc_ref = 'A1'")
    assert r["Location"] == f"/order-statements/{one}/?tab=checks"
    page = web.get(f"/order-statements/{one}/").content.decode()
    assert "Draft payment" in page and "Single payment" in page and "Delete this draft" in page
    # deleting the draft puts its invoice back on Invoices, still owed
    assert web.post(f"/order-statements/{one}/", {"action": "delete_draft"})["Location"] == "/order-statements/"
    assert fetchone(_db(), "SELECT statement_id, status FROM invoices WHERE doc_ref = 'A1'") == (None, "Unpaid")
    assert _n("SELECT COUNT(*) FROM order_statements WHERE statement_id = ?", (one,)) == 0
    # across two suppliers: one statement each
    r = web.post(HERE, {"action": "pay", "pay": [ids["A1"], ids["A2"], ids["B1"]]})
    assert r["Location"] == "/order-statements/?stage=Draft"
    sa, sb = _n("SELECT statement_id FROM invoices WHERE doc_ref = 'A2'"), _n("SELECT statement_id FROM invoices WHERE doc_ref = 'B1'")
    assert sa == _n("SELECT statement_id FROM invoices WHERE doc_ref = 'A1'") and sb != sa
    assert fetchone(_db(), "SELECT creditor_id, total_amount FROM order_statements WHERE statement_id = ?", (sa,)) == (a, 22000.0)
    page = web.get("/order-statements/").content.decode()
    assert "Batch · 2" in page and ">Single<" in page and "2026 - OS 0031" in page
    assert "No invoice is waiting" in web.get(HERE).content.decode()
    # an invoice already paid for by someone else in the meantime: all or nothing
    _add(web, a, "A3", "3,300")
    r = web.post(HERE, {"action": "pay", "pay": [_inv("A3")[0], ids["B1"]]})
    assert r["Location"] == HERE and _n("SELECT statement_id FROM invoices WHERE doc_ref = 'A3'") is None
    # its number: PS, continuing the old count; the old one keeps OS
    assert promote_draft_statement(sa, "Issued") == "2026 - PS 0032"
    assert promote_draft_statement(sb, "Issued") == "2026 - PS 0033"
    assert _n("SELECT COUNT(*) FROM order_statements WHERE statement_ref = '2026 - OS 0031'") == 1
    r = web.post(f"/order-statements/{sa}/", {"action": "delete_draft"})                      # numbered: only ever cancelled
    assert _n("SELECT COUNT(*) FROM order_statements WHERE statement_id = ?", (sa,)) == 1
    assert "Delete this draft" not in web.get(f"/order-statements/{sa}/").content.decode()


def test_a_floating_invoice_is_corrected_or_deleted_and_roles(web, db):
    _admin(web)
    cid = make_creditor(db, "AMK Auto Parts")
    _add(web, cid, "X1", "5,000")
    iid = _inv("X1")[0]
    page = web.get(HERE + f"?edit={iid}").content.decode()
    assert "Correct X1" in page
    stamp = page.split('name="stamp" value="')[1].split('"')[0]
    web.post(HERE, {"action": "save", "invoice_id": iid, "stamp": stamp, "doc": "X1", "dr": "D9", "date": "2026-10-12", "gross": "5,500", "less": ""})
    assert fetchone(_db(), "SELECT amount, dr_no, invoice_date FROM invoices WHERE invoice_id = ?", (iid,)) == (5500.0, "D9", "2026-10-12")
    web.post(HERE, {"action": "delete", "invoice_id": iid, "stamp": stamp})                   # an old stamp: someone changed it since
    assert _n("SELECT COUNT(*) FROM invoices") == 1
    stamp = web.get(HERE + f"?edit={iid}").content.decode().split('name="stamp" value="')[1].split('"')[0]
    web.post(HERE, {"action": "delete", "invoice_id": iid, "stamp": stamp})
    assert _n("SELECT COUNT(*) FROM invoices") == 0
    buyer = _sign_in_new("buyer@slicks.test", "purchasing")                                  # whoever cannot open Payment statements cannot open this
    assert buyer.get(HERE).status_code == 403
