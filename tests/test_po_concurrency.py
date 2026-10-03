"""Two people at the same moment on the same purchase order (2026-10-03, D2b): receiving the same delivery, matching the same goods to an invoice, saving the same
draft. Exactly one wins; the other gets a plain message; nothing is counted twice and no half-made invoice is left."""
import threading
from datetime import date

from tests.conftest import fetchone, make_creditor, make_statement


def _ordered_po(db, qty=10):
    from engines.po_rules import order_po, save_po
    cid = make_creditor(db, "Goodyear Philippines")
    ok, msg, po_id = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": "Eagle F1", "qty": qty, "unit": "pc", "est_unit_price": 100.0}])
    assert ok, msg
    ok, msg = order_po(po_id)
    assert ok, msg
    line_id = fetchone(db, "SELECT line_id FROM purchase_order_lines WHERE po_id = ?", (po_id,))[0]
    return cid, po_id, line_id


def _at_once(fn, n=2):
    start, results = threading.Barrier(n), [None] * n

    def run(i):
        start.wait()
        results[i] = fn(i)
    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_two_people_receiving_the_same_delivery_count_it_once(db):
    from engines.po_rules import receive_goods
    cid, po_id, line_id = _ordered_po(db)
    results = _at_once(lambda i: receive_goods(cid, [{"line_id": line_id, "qty": 10}], date(2026, 10, 3)))
    assert sorted(r[0] for r in results) == [False, True]
    assert fetchone(db, "SELECT qty_received FROM purchase_order_lines WHERE line_id = ?", (line_id,))[0] == 10
    assert fetchone(db, "SELECT COUNT(*) FROM po_receipts WHERE po_id = ?", (po_id,))[0] == 1
    assert fetchone(db, "SELECT status FROM purchase_orders WHERE po_id = ?", (po_id,))[0] == "RECEIVED"


def test_two_people_matching_the_same_goods_make_one_invoice(db):
    from engines.po_rules import receive_goods, receive_into_batch
    cid, po_id, line_id = _ordered_po(db)
    assert receive_goods(cid, [{"line_id": line_id, "qty": 10}], date(2026, 10, 3))[0]
    sid = make_statement(db, cid)
    results = _at_once(lambda i: receive_into_batch(cid, sid, f"INV-{i}", date(2026, 10, 3), [{"line_id": line_id, "qty": 10, "actual_unit_price": 100.0}],
                                                    goods_received=True))
    assert sorted(r[0] for r in results) == [False, True]
    assert fetchone(db, "SELECT COUNT(*) FROM invoices WHERE creditor_id = ? AND status != 'Cancelled'", (cid,))[0] == 1          # the loser's invoice was taken back out
    assert fetchone(db, "SELECT qty_invoiced FROM purchase_order_lines WHERE line_id = ?", (line_id,))[0] == 10


def test_the_same_invoice_number_at_the_same_moment_is_refused_plainly(db):
    from engines.po_rules import receive_goods, receive_into_batch
    from engines.po_rules import save_po, order_po
    cid, po_id, line_id = _ordered_po(db, qty=20)
    assert receive_goods(cid, [{"line_id": line_id, "qty": 20}], date(2026, 10, 3))[0]
    sid = make_statement(db, cid)
    results = _at_once(lambda i: receive_into_batch(cid, sid, "INV-77", date(2026, 10, 3), [{"line_id": line_id, "qty": 10, "actual_unit_price": 100.0}],
                                                    goods_received=True))
    oks = [r for r in results if r[0]]
    assert len(oks) == 1
    loser = [r for r in results if not r[0]][0]
    assert "INV-77" in loser[1] or "Someone else" in loser[1]                 # a message, never a crash
    assert fetchone(db, "SELECT COUNT(*) FROM invoices WHERE doc_ref = 'INV-77'")[0] == 1


def test_two_people_saving_the_same_draft(db):
    from engines.po_rules import CHANGED, po_stamp, save_po
    cid = make_creditor(db, "Motolite")
    ok, msg, po_id = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": "Gold 3SM", "qty": 1, "est_unit_price": 5000.0}])
    assert ok
    seen = po_stamp(po_id)                                                    # both opened the draft
    lines_a = [{"item_name": "Gold 3SM", "qty": 2, "est_unit_price": 5000.0}]
    lines_b = [{"item_name": "Gold 3SM", "qty": 9, "est_unit_price": 5000.0}]
    assert save_po(po_id, cid, date(2026, 10, 1), date(2026, 10, 8), lines_a, expected_stamp=seen)[0]
    ok, msg, _ = save_po(po_id, cid, date(2026, 10, 1), date(2026, 10, 8), lines_b, expected_stamp=seen)
    assert not ok and msg == CHANGED
    assert fetchone(db, "SELECT qty FROM purchase_order_lines WHERE po_id = ?", (po_id,))[0] == 2
