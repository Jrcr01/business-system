"""Stage D3a of the Django rebuild (Payables, docs/mockups/d3_payables.html screens 1-5): the batches, one batch with its invoices and credit memos (two people
never overwrite each other), its OS number, reopening and cancelling, its statements as PDF, Add data and Payables due."""
from tests.conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin, _sign_in_new


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _new_batch(web, cid):
    r = web.post("/order-statements/new/", {"s": cid})
    assert r.status_code == 302
    return int(r["Location"].split("/")[2])


def _add_inv(web, sid, doc="INV-1", gross="10,000", date="2026-09-08", less="", follow=False, **extra):
    return web.post(f"/order-statements/{sid}/", {"action": "add_invoice", "doc": doc, "date": date, "gross": gross, "less": less, **extra}, follow=follow)


def _inv(doc):
    return fetchone(_db(), "SELECT invoice_id, gross_amount, amount, due_date, status FROM invoices WHERE doc_ref = ?", (doc,))


def _stamp_of(web, sid, invoice_id):
    from payables.views import _invoices
    return next(r["stamp"] for r in _invoices(sid) if r["invoice_id"] == invoice_id)


def test_a_new_batch_and_its_invoices_with_the_due_date_from_the_terms(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines", terms="120")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, gross="10,000", less="5")
    inv_id, gross, net, due, status = _inv("INV-1")
    assert (gross, net, due[:10]) == (10000.0, 9500.0, "2027-01-08")           # 120 days = 4 whole months: the same day of the month
    page = web.get(f"/order-statements/{sid}/").content.decode()
    assert "INV-1" in page and "9,500.00" in page and "Draft batch" in page and "Give it its OS number" in page
    listing = web.get("/order-statements/").content.decode()
    assert "Goodyear Philippines" in listing and "9,500.00" in listing


def test_a_number_the_supplier_has_is_refused_and_a_near_double_asks_first(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="INV-77")
    r = _add_inv(web, sid, doc="INV-77", follow=True)
    assert b"Is this a double entry?" in r.content or b"already exists" in r.content
    assert fetchone(_db(), "SELECT COUNT(*) FROM invoices WHERE doc_ref = 'INV-77'")[0] == 1
    r = _add_inv(web, sid, doc="INV-77", confirmed="1", follow=True)
    assert b"already exists for this supplier" in r.content                     # the hard stop holds even when confirmed
    assert fetchone(_db(), "SELECT COUNT(*) FROM invoices WHERE doc_ref = 'INV-77'")[0] == 1


def test_edit_and_delete_an_invoice(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="INV-5", gross="1000", date="2026-10-01")
    inv_id = _inv("INV-5")[0]
    assert b'name="stamp"' in web.get(f"/order-statements/{sid}/?edit_inv={inv_id}").content
    web.post(f"/order-statements/{sid}/", {"action": "save_invoice", "invoice_id": inv_id, "stamp": _stamp_of(web, sid, inv_id), "doc": "INV-5A", "date": "2026-10-05",
                                           "gross": "1,200.00", "less": "0"})
    assert _inv("INV-5A")[1:4] == (1200.0, 1200.0, "2026-11-05")          # 30 days = 1 whole month: the same day
    web.post(f"/order-statements/{sid}/", {"action": "delete_invoice", "invoice_id": inv_id, "stamp": _stamp_of(web, sid, inv_id)})
    assert fetchone(_db(), "SELECT COUNT(*) FROM invoices WHERE invoice_id = ?", (inv_id,))[0] == 0


def test_two_people_editing_the_same_invoice(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="INV-9", gross="1000")
    inv_id = _inv("INV-9")[0]
    seen = _stamp_of(web, sid, inv_id)
    other = _sign_in_new("ap@slicks.test", "payables")
    other.post(f"/order-statements/{sid}/", {"action": "save_invoice", "invoice_id": inv_id, "stamp": seen, "doc": "INV-9", "date": "2026-09-08", "gross": "2000", "less": "0"})
    r = web.post(f"/order-statements/{sid}/", {"action": "save_invoice", "invoice_id": inv_id, "stamp": seen, "doc": "INV-9", "date": "2026-09-08", "gross": "3000", "less": "0"},
                 follow=True)
    assert b"Someone else changed or removed this invoice" in r.content
    assert _inv("INV-9")[1] == 2000.0


def test_credit_memos(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, gross="10000")
    web.post(f"/order-statements/{sid}/", {"action": "add_memo", "ref": "CM-1", "date": "2026-10-01", "amount": "1,000", "reason": "returned"})
    assert fetchone(_db(), "SELECT net_total FROM v_batch_net_totals WHERE statement_id = ?", (sid,))[0] == 9000.0
    from payables.views import _memos
    memo = _memos(sid)[0]
    web.post(f"/order-statements/{sid}/", {"action": "save_memo", "memo_id": memo["credit_memo_id"], "stamp": memo["stamp"], "ref": "CM-1", "date": "2026-10-01",
                                           "amount": "500", "reason": "returned"})
    assert fetchone(_db(), "SELECT amount FROM credit_memos WHERE credit_memo_id = ?", (memo["credit_memo_id"],))[0] == 500.0
    page = web.get(f"/order-statements/{sid}/?tab=memos").content.decode()
    assert "CM-1" in page and "returned" in page


def test_os_number_reopen_and_cancel(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    _add_inv(web, sid)
    web.post(f"/order-statements/{sid}/", {"action": "number"})
    ref = fetchone(_db(), "SELECT statement_ref, status FROM order_statements WHERE statement_id = ?", (sid,))
    assert ref[1] == "Issued" and " - OS " in ref[0]
    with __import__("sqlite3").connect(_db()) as c:                             # as if its checks covered it
        c.execute("UPDATE order_statements SET is_locked = 1 WHERE statement_id = ?", (sid,))
    page = web.get(f"/order-statements/{sid}/").content.decode()
    assert "Edit this batch" in page and "Add invoice" not in page
    web.post(f"/order-statements/{sid}/", {"action": "reopen", "reason": "wrong amount"})
    assert fetchone(_db(), "SELECT is_locked FROM order_statements WHERE statement_id = ?", (sid,))[0] == 0
    web.post(f"/order-statements/{sid}/", {"action": "cancel"})
    assert fetchone(_db(), "SELECT status FROM order_statements WHERE statement_id = ?", (sid,))[0] == "Cancelled"
    assert _inv("INV-1")[4] == "Cancelled"


def test_payables_cannot_reopen_or_cancel(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    _add_inv(web, sid)
    ap = _sign_in_new("ap@slicks.test", "payables")
    page = ap.get(f"/order-statements/{sid}/").content.decode()
    assert "Cancel batch" not in page
    ap.post(f"/order-statements/{sid}/", {"action": "cancel"})
    assert fetchone(_db(), "SELECT status FROM order_statements WHERE statement_id = ?", (sid,))[0] != "Cancelled"


def test_statements_as_pdf(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    _add_inv(web, sid)
    for url in (f"/order-statements/{sid}/a4.pdf?copies=supplier&history=1", f"/order-statements/{sid}/thermal.pdf"):
        r = web.get(url)
        assert r.status_code == 200 and r.content[:4] == b"%PDF", url


def test_add_data_is_gone(web, db):
    # owner, 2026-10-09: every card on Add data was a shortcut to a page with its own menu entry
    _admin(web)
    assert web.get("/add-data/").status_code == 404 and "Add data" not in web.get("/").content.decode()

def test_the_calendar_replaces_payables_due(web, db):
    # owner, 2026-10-09: "drop payables due and make the calendar a tile"
    import sqlite3
    from datetime import timedelta
    from engines.clock import manila_today
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, gross="10,000")
    day = manila_today() + timedelta(days=3)
    web.post(f"/order-statements/{sid}/", {"action": "chk_park", "d1": str(day), "a1": "10,000"})
    with sqlite3.connect(_db()) as conn:
        conn.execute("UPDATE checks_issued SET check_number = '0001' WHERE statement_id = ?", (sid,))
    page = web.get(f"/calendar/?m={day:%Y-%m}&day={day}").content.decode()
    assert "Calendar" in page and "Goodyear Philippines" in page and "10,000.00" in page
    web.post("/calendar/", {"day": str(day), "kind": "holiday", "label": "Test holiday", "m": f"{day:%Y-%m}", "action": "mark"})
    assert "Test holiday" in web.get(f"/calendar/?m={day:%Y-%m}&day={day}").content.decode()
    assert web.get("/payables-due/").status_code == 404
    home = web.get("/").content.decode()
    assert 'href="/calendar/"' in home and "Payables due" not in home


def test_match_invoice_opens_with_the_batch_chosen(web, db):
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    a = _new_batch(web, cid)
    b = _new_batch(web, cid)
    page = web.get(f"/purchase-orders/match/?s={cid}&batch={a}").content.decode()
    assert page.index(f'value="{a}"') < page.index(f'value="{b}"') if f'value="{b}"' in page else True


def test_roles(web, db):
    _admin(web)
    buyer = _sign_in_new("buyer@slicks.test", "purchasing")
    assert buyer.get("/order-statements/").status_code == 403
    assert buyer.get("/calendar/").status_code == 403
    ap = _sign_in_new("ap@slicks.test", "payables")
    assert ap.get("/calendar/").status_code == 200 and ap.get("/order-statements/").status_code == 200


def test_an_edited_invoice_can_be_deleted_and_a_matched_one_is_refused_plainly(db):
    """Engine fix, 2026-10-03: deleting an invoice that had been edited crashed (its correction history pointed at it); an invoice matched to a purchase order is
    refused with a plain message, because the order's invoiced quantities point at it."""
    from datetime import date
    from engines.po_rules import order_po, receive_into_batch, save_po
    from engines.statement_rules import add_invoice, create_order_statement, delete_invoice, edit_invoice
    cid = make_creditor(db, "Goodyear Philippines")
    sid = create_order_statement(cid)
    add_invoice(cid, "INV-E", date(2026, 10, 1), date(2026, 10, 31), 100.0, [], sid)
    inv = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-E'")[0]
    assert edit_invoice(inv, "INV-E", date(2026, 10, 1), date(2026, 10, 31), 150.0, 0)[0]
    ok, msg = delete_invoice(inv)
    assert ok, msg
    assert fetchone(db, "SELECT COUNT(*) FROM invoice_corrections WHERE invoice_id = ?", (inv,))[0] == 0
    ok, msg, po_id = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": "Eagle F1", "qty": 2, "est_unit_price": 100.0}])
    assert order_po(po_id)[0]
    line = fetchone(db, "SELECT line_id FROM purchase_order_lines WHERE po_id = ?", (po_id,))[0]
    ok, msg, matched = receive_into_batch(cid, sid, "INV-PO", date(2026, 10, 3), [{"line_id": line, "qty": 2, "actual_unit_price": 100.0}])
    assert ok, msg
    ok, msg = delete_invoice(matched)
    assert not ok and "purchase order" in msg


def test_the_sales_invoice_is_the_reference_with_the_dr_beside_it(web, db):
    """Owner, 2026-10-03: the supplier's SALES INVOICE number is the reference; the delivery receipt number is kept beside it; an invoice with no purchase order is
    allowed for now but tagged No PO."""
    _admin(web)
    cid = make_creditor(db, "Goodyear Philippines")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="SI-1001", dr="DR-77")
    assert fetchone(_db(), "SELECT dr_no FROM invoices WHERE doc_ref = 'SI-1001'")[0] == "77"
    page = web.get(f"/order-statements/{sid}/").content.decode()
    assert "Reference (SI or DR)" in page and "DR no." in page and ">77<" in page
    assert "No PO</span>" in page and "1 of 1 sales invoice(s) typed in with no purchase order" in page
    inv_id = _inv("SI-1001")[0]
    web.post(f"/order-statements/{sid}/", {"action": "save_invoice", "invoice_id": inv_id, "stamp": _stamp_of(web, sid, inv_id), "doc": "SI-1001", "date": "2026-09-08",
                                           "gross": "10000", "less": "0", "dr": "D.R. 78"})
    assert fetchone(_db(), "SELECT dr_no FROM invoices WHERE invoice_id = ?", (inv_id,))[0] == "78"


def test_the_dr_the_goods_came_with_goes_on_the_matched_invoice(db):
    from datetime import date
    from engines.po_rules import order_po, receive_goods, receive_into_batch, save_po
    from engines.statement_rules import create_order_statement
    cid = make_creditor(db, "Goodyear Philippines")
    ok, msg, po_id = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": "Eagle F1", "qty": 2, "est_unit_price": 100.0}])
    assert order_po(po_id)[0]
    line = fetchone(db, "SELECT line_id FROM purchase_order_lines WHERE po_id = ?", (po_id,))[0]
    assert receive_goods(cid, [{"line_id": line, "qty": 2}], date(2026, 10, 3), "4512", "delivery receipt")[0]
    sid = create_order_statement(cid)
    ok, msg, inv = receive_into_batch(cid, sid, "SI-9", date(2026, 10, 3), [{"line_id": line, "qty": 2, "actual_unit_price": 100.0}], goods_received=True)
    assert ok, msg
    assert fetchone(db, "SELECT doc_ref, dr_no FROM invoices WHERE invoice_id = ?", (inv,)) == ("SI-9", "4512")


def test_the_printed_statement_says_sales_invoice_and_shows_the_dr(db):
    from datetime import date
    import fitz
    from engines.batch_statement_service import generate_batch_statement_pdf
    from engines.statement_rules import add_invoice, create_order_statement
    cid = make_creditor(db, "Goodyear Philippines")
    sid = create_order_statement(cid)
    add_invoice(cid, "SI-55", date(2026, 10, 1), date(2027, 2, 1), 500.0, [], sid, dr_no="DR 12")
    text = "".join(p.get_text() for p in fitz.open(stream=generate_batch_statement_pdf(sid), filetype="pdf"))
    assert "Sales Invoice" in text and "SI-55 (DR 12)" in text


def _ordered_and_received(db, web=None, qty=2):
    from datetime import date
    from engines.po_rules import order_po, receive_goods, save_po
    cid = make_creditor(db, "Goodyear Philippines")
    ok, msg, po_id = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": "Eagle F1", "qty": qty, "est_unit_price": 100.0}])
    assert order_po(po_id)[0]
    line = fetchone(db, "SELECT line_id FROM purchase_order_lines WHERE po_id = ?", (po_id,))[0]
    assert receive_goods(cid, [{"line_id": line, "qty": qty}], date(2026, 10, 3), "", "delivery receipt")[0]
    return cid, line


def test_a_matched_invoice_can_wait_for_a_batch_and_be_put_in_one_later(web, db):
    """Owner, 2026-10-03: 'put the invoice in' is optional, so which invoices go in which batch is decided when the order statement is prepared."""
    _admin(web)
    cid, line = _ordered_and_received(db)
    web.post("/purchase-orders/match/", {"s": cid, "action": "match", f"pick_{line}": "1", f"qty_{line}": "2", f"price_{line}": "100", "doc": "SI-W1",
                                         "inv_date": "2026-10-03", "batch": ""})
    inv_id, statement_id = fetchone(_db(), "SELECT invoice_id, statement_id FROM invoices WHERE doc_ref = 'SI-W1'")
    assert statement_id is None
    listing = web.get("/order-statements/").content.decode()
    assert "Sales invoices waiting for a batch" in listing and "Goodyear Philippines" in listing
    sid = _new_batch(web, cid)
    page = web.get(f"/order-statements/{sid}/").content.decode()
    assert "Waiting for a batch: 1 sales invoice(s)" in page and f'name="pick_{inv_id}"' in page
    web.post(f"/order-statements/{sid}/", {"action": "put_in_batch", f"pick_{inv_id}": "1"})
    assert fetchone(_db(), "SELECT statement_id FROM invoices WHERE invoice_id = ?", (inv_id,))[0] == sid
    assert fetchone(_db(), "SELECT net_total FROM v_batch_net_totals WHERE statement_id = ?", (sid,))[0] == 200.0
    assert "Sales invoices waiting for a batch" not in web.get("/order-statements/").content.decode()
    web.post(f"/order-statements/{sid}/", {"action": "take_out", "invoice_id": inv_id, "stamp": _stamp_of(web, sid, inv_id)})
    assert fetchone(_db(), "SELECT statement_id FROM invoices WHERE invoice_id = ?", (inv_id,))[0] is None
    assert fetchone(_db(), "SELECT net_total FROM v_batch_net_totals WHERE statement_id = ?", (sid,))[0] == 0.0


def test_the_match_form_offers_no_batch_first_and_keeps_the_batch_it_came_from(web, db):
    _admin(web)
    cid, line = _ordered_and_received(db)
    sid = _new_batch(web, cid)
    plain = web.get(f"/purchase-orders/match/?s={cid}").content.decode()
    assert "Not in a batch yet" in plain and " selected>" not in plain.split('name="batch"')[1].split("</select>")[0]
    chosen = web.get(f"/purchase-orders/match/?s={cid}&batch={sid}").content.decode()
    assert f'value="{sid}" selected' in chosen


def test_two_people_batching_the_same_invoice_at_once(db):
    import threading
    from datetime import date
    from engines.po_rules import receive_into_batch
    from engines.statement_rules import create_order_statement, put_in_batch
    cid, line = _ordered_and_received(db)
    ok, msg, inv = receive_into_batch(cid, None, "SI-W2", date(2026, 10, 3), [{"line_id": line, "qty": 2, "actual_unit_price": 100.0}], goods_received=True)
    assert ok and "waiting for a batch" in msg
    a, b = create_order_statement(cid), create_order_statement(cid)
    start, results = threading.Barrier(2), {}

    def run(sid):
        start.wait()
        results[sid] = put_in_batch(sid, [inv])
    threads = [threading.Thread(target=run, args=(s,)) for s in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(r[0] for r in results.values()) == [False, True]
    assert fetchone(db, "SELECT statement_id FROM invoices WHERE invoice_id = ?", (inv,))[0] in (a, b)
