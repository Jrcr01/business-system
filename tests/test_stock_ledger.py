"""Stock as a ledger (2026-10-03): receiving goods counts them at the order's net price; matching the invoice corrects the cost to what was charged; a one-step receive-and-invoice
counts at the price charged; a typed item with no catalog item has no stock; on-hand and the weighted average follow from the rows."""
from datetime import date

import app
from conftest import fetchone, make_creditor
from engines import product_catalog as pc
from engines import stock_ledger as sl
from engines.acting_user import acting_as
from engines.po_rules import get_po, order_po, receive_goods, receive_into_batch, save_po
from engines.price_list_service import add_price


def _ordered(db, qty=4, price=1000.0):
    cid = make_creditor(db, name="Stock Supplier", terms="30")
    pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", terrain="A/T", pattern="SL369")
    with acting_as("buyer@example.com", "purchasing"):
        assert add_price(cid, name, "pc", price, date(2026, 9, 15), trusted=True)[0]
        pc.link_price_rows(cid, name, pid)
        ok, msg, po = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": name, "qty": qty}])
        assert ok, msg
        assert order_po(po)[0]
    return cid, pid, po, int(get_po(po)["lines"].iloc[0]["line_id"])


def _item(db, pid):
    return fetchone(db, "SELECT on_hand, avg_cost FROM items WHERE id = ?", (pid,))


def test_receiving_goods_counts_them_at_the_orders_net_price(db):
    cid, pid, po, line = _ordered(db)
    with acting_as("clerk@example.com", "payables"):
        assert receive_goods(cid, [{"line_id": line, "qty": 3}])[0]
    assert _item(db, pid) == (3, 1000.0)
    m = sl.moves(pid)
    assert list(m["kind"]) == ["IN"] and list(m["qty"]) == [3.0] and m.iloc[0]["created_by"] == "clerk@example.com"


def test_the_invoice_corrects_the_cost_to_the_price_charged(db):
    cid, pid, po, line = _ordered(db)
    with acting_as("clerk@example.com", "payables"):
        assert receive_goods(cid, [{"line_id": line, "qty": 4}])[0]
        sid = app.create_order_statement(cid)
        assert receive_into_batch(cid, sid, "SI-1", date(2026, 10, 9), [{"line_id": line, "qty": 4, "actual_unit_price": 1100.0}], goods_received=True)[0]
    assert _item(db, pid) == (4, 1100.0)                                                  # four pieces, now costing what was charged
    m = sl.moves(pid)
    assert list(m["kind"]) == ["IN", "COST"] and m.iloc[1]["qty"] == 0 and m.iloc[1]["avg_after"] == 1100.0


def test_receiving_and_invoicing_in_one_step_counts_at_the_price_charged(db):
    cid, pid, po, line = _ordered(db)
    with acting_as("clerk@example.com", "payables"):
        sid = app.create_order_statement(cid)
        assert receive_into_batch(cid, sid, "SI-2", date(2026, 10, 9), [{"line_id": line, "qty": 2, "actual_unit_price": 1050.0}])[0]
    assert _item(db, pid) == (2, 1050.0) and list(sl.moves(pid)["kind"]) == ["IN"]


def test_the_average_is_weighted_across_deliveries(db):
    cid, pid, po, line = _ordered(db)
    with acting_as("clerk@example.com", "payables"):
        assert receive_goods(cid, [{"line_id": line, "qty": 1}])[0]                      # 1 at 1,000
        sid = app.create_order_statement(cid)
        assert receive_into_batch(cid, sid, "SI-3", date(2026, 10, 9), [{"line_id": line, "qty": 3, "actual_unit_price": 1200.0}])[0]      # 3 at 1,200 in one step
    assert _item(db, pid) == (4, 1150.0)                                                  # (1 x 1,000 + 3 x 1,200) / 4


def test_a_typed_item_with_no_catalog_entry_has_no_stock(db):
    cid = make_creditor(db, name="Typed Supplier")
    with acting_as("buyer@example.com", "purchasing"):
        ok, msg, po = save_po(None, cid, date(2026, 10, 1), date(2026, 10, 8), [{"item_name": "Mystery part", "qty": 1, "est_unit_price": 50}])
        assert ok and order_po(po)[0]
    with acting_as("clerk@example.com", "payables"):
        assert receive_goods(cid, [{"line_id": int(get_po(po)["lines"].iloc[0]["line_id"]), "qty": 1}])[0]
    assert fetchone(db, "SELECT COUNT(*) FROM stock_ledger")[0] == 0
