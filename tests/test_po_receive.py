"""PO matching (B4): the supplier's invoice is matched to its purchase order and lands in a batch. The ACTUAL price prevails over the estimate; the received part
leaves the commitments (nothing is counted twice); a partial delivery keeps the rest open."""
import sqlite3
from datetime import date

import pytest

import app
from conftest import fetchone, make_creditor
from engines.po_rules import (
    cancel_po, get_po, open_commitments, order_po, po_links_for_batch, price_differences, receive_into_batch, receivable_lines, save_po,
)
from engines.price_list_service import add_price, price_on

TODAY = date(2026, 10, 19)


def _supplier(db, terms="30", name="Acme Tires"):
    cid = make_creditor(db, name=name, terms=terms)
    add_price(cid, "Tire", "pc", 100, date(2026, 9, 1))
    add_price(cid, "Tube", "pc", 20, date(2026, 9, 1))
    return cid


def _po(db, cid, lines=None, ordered=True):
    lines = lines or [{"item_name": "Tire", "unit": "pc", "qty": 10}]
    ok, msg, po = save_po(None, cid, TODAY, date(2026, 10, 25), lines, "")
    assert ok, msg
    if ordered:
        assert order_po(po)[0]
    return po


def _pick(po, item="Tire", qty=None, price=None):
    ln = get_po(po)["lines"].set_index("item_name").loc[item]
    return {"line_id": int(ln["line_id"]), "qty": float(ln["qty"] - ln["qty_received"]) if qty is None else qty, "actual_unit_price": ln["est_unit_price"] if price is None else price}


def _recv(db, cid, picks, sid=None, doc="INV-1", **kw):
    sid = sid or app.create_order_statement(cid)
    return receive_into_batch(cid, sid, doc, date(2026, 10, 26), picks, **kw), sid


def test_an_exact_match_creates_the_invoice_in_the_batch_and_closes_the_order(db):
    cid = _supplier(db)
    po = _po(db, cid)
    (ok, msg, inv_id), sid = _recv(db, cid, [_pick(po)])
    assert ok, msg
    inv = fetchone(db, "SELECT doc_ref, gross_amount, amount, due_date, status, statement_id FROM invoices WHERE invoice_id = ?", (inv_id,))
    assert inv == ("INV-1", 1000.0, 1000.0, "2026-11-26", "Unpaid", sid)                   # due = invoice date + 30 days, by the invoice (not the PO)
    po_row = get_po(po)
    assert po_row["header"]["status"] == "RECEIVED" and po_row["lines"].iloc[0]["qty_received"] == 10
    assert fetchone(db, "SELECT po_id, actual_amount, invoice_id, supplier_invoice_no FROM po_receipts") == (po, 1000.0, inv_id, "INV-1")
    assert open_commitments(TODAY, date(2027, 6, 1)).empty                                 # the commitment is gone: the invoice carries it now
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'PO_RECEIVED'")[0] == 1


def test_the_actual_price_prevails_over_the_estimate(db):
    cid = _supplier(db)
    po = _po(db, cid)
    (ok, _, inv_id), _ = _recv(db, cid, [_pick(po, price=110.0)])
    assert ok and fetchone(db, "SELECT gross_amount FROM invoices WHERE invoice_id = ?", (inv_id,))[0] == 1100.0
    ln = get_po(po)["lines"].iloc[0]
    assert ln["est_unit_price"] == 100 and ln["actual_unit_price"] == 110 and ln["actual_amount"] == 1100
    diff = price_differences().iloc[0]
    assert diff["item_name"] == "Tire" and diff["est_unit_price"] == 100 and diff["actual_unit_price"] == 110
    assert diff["difference"] == 100.0 and diff["difference_pct"] == pytest.approx(10.0)


def test_a_partial_delivery_keeps_the_rest_open_and_the_second_one_closes_it(db):
    cid = _supplier(db)
    po = _po(db, cid)
    (ok, _, first), sid = _recv(db, cid, [_pick(po, qty=4)])
    assert ok and get_po(po)["header"]["status"] == "PARTIAL"
    left = open_commitments(TODAY, date(2027, 6, 1))
    assert left["po_id"].tolist() == [po] and left["amount"].iloc[0] == pytest.approx(600.0)     # 6 of 10 still to come
    assert receivable_lines(cid)["remaining"].tolist() == [6.0]
    (ok, msg, second), _ = _recv(db, cid, [_pick(po)], sid=sid, doc="INV-2")
    assert ok, msg
    assert get_po(po)["header"]["status"] == "RECEIVED" and open_commitments(TODAY, date(2027, 6, 1)).empty
    assert fetchone(db, "SELECT COUNT(*) FROM po_receipts")[0] == 2 and first != second


def test_one_invoice_can_cover_lines_from_two_orders(db):
    cid = _supplier(db)
    a, b = _po(db, cid), _po(db, cid, lines=[{"item_name": "Tube", "unit": "pc", "qty": 5}])
    (ok, msg, inv_id), _ = _recv(db, cid, [_pick(a), _pick(b, "Tube")])
    assert ok, msg
    assert fetchone(db, "SELECT gross_amount FROM invoices WHERE invoice_id = ?", (inv_id,))[0] == 1100.0
    assert fetchone(db, "SELECT COUNT(*) FROM po_receipts WHERE invoice_id = ?", (inv_id,))[0] == 2
    assert get_po(a)["header"]["status"] == "RECEIVED" and get_po(b)["header"]["status"] == "RECEIVED"


def test_refusals_leave_everything_untouched(db):
    cid = _supplier(db)
    other = _supplier(db, name="Other Co")
    po = _po(db, cid)
    draft = _po(db, cid, ordered=False)
    cancelled = _po(db, cid)
    cancel_po(cancelled)
    sid = app.create_order_statement(cid)

    def refused(picks, sid=sid, doc="INV-X", **kw):
        ok, msg, inv = receive_into_batch(cid, sid, doc, date(2026, 10, 26), picks, **kw)
        assert not ok and inv is None, msg
        return msg

    assert "more than" in refused([_pick(po, qty=11)])                                         # beyond what is still to come
    assert refused([_pick(po, qty=0)])
    assert refused([_pick(po, price=-1)])
    assert refused([_pick(draft)])                                                              # a draft was never sent to the supplier
    assert refused([_pick(cancelled)])
    assert "supplier" in refused([_pick(po)], sid=app.create_order_statement(other)).lower()    # a batch of another supplier
    assert "invoice" in refused([_pick(po)], doc="  ").lower()
    sqlite3.connect(db).execute("UPDATE order_statements SET is_locked = 1 WHERE statement_id = ?", (sid,)).connection.commit()
    assert "closed" in refused([_pick(po)]).lower() or "locked" in refused([_pick(po)]).lower()
    sqlite3.connect(db).execute("UPDATE order_statements SET is_locked = 0 WHERE statement_id = ?", (sid,)).connection.commit()
    assert fetchone(db, "SELECT COUNT(*) FROM invoices")[0] == 0 and fetchone(db, "SELECT COUNT(*) FROM po_receipts")[0] == 0
    assert get_po(po)["lines"].iloc[0]["qty_received"] == 0 and get_po(po)["header"]["status"] == "ORDERED"


def test_a_duplicate_invoice_number_is_refused_before_anything_is_written(db):
    cid = _supplier(db)
    po = _po(db, cid)
    (ok, _, _), sid = _recv(db, cid, [_pick(po, qty=4)])
    assert ok
    ok, msg, inv = receive_into_batch(cid, sid, "INV-1", date(2026, 10, 27), [_pick(po, qty=2)])
    assert not ok and "already" in msg.lower()
    assert get_po(po)["lines"].iloc[0]["qty_received"] == 4 and fetchone(db, "SELECT COUNT(*) FROM invoices")[0] == 1


def test_a_changed_actual_price_can_update_the_price_list(db):
    cid = _supplier(db)
    po = _po(db, cid)
    (ok, _, _), _ = _recv(db, cid, [_pick(po, price=110.0)], update_prices=True)
    assert ok
    assert price_on(cid, "Tire", date(2026, 10, 26))["unit_price"] == 110.0              # the new price starts on the invoice date
    assert price_on(cid, "Tire", date(2026, 10, 1))["unit_price"] == 100.0               # earlier dates keep the old price
    po2 = _po(db, cid)
    assert get_po(po2)["lines"].iloc[0]["est_unit_price"] == 100.0                        # ordered Oct 19, before the new price started
    ok, _, po3 = save_po(None, cid, date(2026, 11, 1), date(2026, 11, 8), [{"item_name": "Tire", "unit": "pc", "qty": 1}], "")
    assert get_po(po3)["lines"].iloc[0]["est_unit_price"] == 110.0


def test_the_batch_shows_which_invoices_matched_their_order_and_by_how_much(db):
    cid = _supplier(db)
    a, b = _po(db, cid), _po(db, cid, lines=[{"item_name": "Tube", "unit": "pc", "qty": 5}])
    sid = app.create_order_statement(cid)
    (ok1, _, i1), _ = _recv(db, cid, [_pick(a)], sid=sid, doc="INV-A")
    (ok2, _, i2), _ = _recv(db, cid, [_pick(b, "Tube", price=24.0)], sid=sid, doc="INV-B")
    app.add_invoice(cid, "INV-NOPO", date(2026, 10, 26), date(2026, 11, 26), 500.0, [], sid)
    links = po_links_for_batch(sid).set_index("doc_ref")
    assert links.loc["INV-A", "po_nos"] == get_po(a)["header"]["po_no"] and links.loc["INV-A", "difference"] == 0 and links.loc["INV-A", "matches"]
    assert links.loc["INV-B", "difference"] == pytest.approx(20.0) and not links.loc["INV-B", "matches"]
    assert "INV-NOPO" not in links.index                                                    # a typed invoice with no PO is simply not listed


def test_receivable_lines_list_only_open_ordered_lines_of_that_supplier(db):
    cid, other = _supplier(db), _supplier(db, name="Other Co")
    po = _po(db, cid)
    _po(db, cid, ordered=False)
    _po(db, other)
    df = receivable_lines(cid)
    assert df["po_id"].tolist() == [po] and df["remaining"].tolist() == [10.0] and df["item_name"].tolist() == ["Tire"]
