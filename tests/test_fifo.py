"""Cost by FIFO, by delivery date (owner, 2026-10-17; mock-up docs/mockups/al_fifo_cost.html, answers 1 A and 2 A): every delivery is a batch, pieces
leave oldest first, a sale's cost is what its pieces cost; the invoice price, an undone delivery, a return, a count and a sale made before its delivery
was recorded all keep the batches and the sales right."""
from datetime import date

from conftest import fetchone
from tests.test_web_stock import _item


def _in(item_id, qty, cost, day, ref_type="purchase_order_lines", ref_id=1, est=False):
    from engines.common import write_tx
    from engines.stock_ledger import stock_in
    with write_tx() as conn:
        stock_in(conn, item_id, qty, cost, ref_type, ref_id, day, note="received, counted at the order's net price" if est else "received")


def _sale(item_id, qty, price, si):
    from engines.clock import manila_today
    from engines.sales_rules import register_booklet, save_sale
    register_booklet(4501, 4550, "Julian")
    ok, msg, sid = save_sale(si, manila_today(), None, "", [{"item_id": item_id, "qty": qty, "price": price}], [], [{"method": "CASH", "amount": qty * price}])
    assert ok, msg
    return sid


def _cost(db, sid):
    return fetchone(db, "SELECT cost_total FROM sales WHERE sale_id = ?", (sid,))[0]


def _items(db, item_id):
    return fetchone(db, "SELECT on_hand, ROUND(avg_cost, 2), ROUND(next_cost, 2) FROM items WHERE id = ?", (item_id,))


def test_a_sale_takes_the_oldest_batches_first_the_mockups_example(db):
    a, _ = _item()
    _in(a, 4, 9850.0, date(2026, 10, 4), ref_id=1)
    _in(a, 8, 10237.5, date(2026, 10, 12), ref_id=2)
    assert _items(db, a) == (12, 10108.33, 9850.0)                      # next sale costs = the oldest batch
    sid = _sale(a, 6, 12500, 4501)
    assert _cost(db, sid) == 59875.0                                     # 4 x 9,850 + 2 x 10,237.50
    assert fetchone(db, "SELECT ROUND(unit_cost, 2) FROM sale_lines WHERE sale_id = ?", (sid,))[0] == 9979.17
    assert _items(db, a) == (6, 10237.5, 10237.5)                        # what is left is all from the second delivery
    from engines.fifo import batches
    b = batches(a)
    assert [(x["came"], x["left"], x["used_up"], x["next"]) for x in b] == [(4, 0, True, False), (8, 6, False, True)]


def test_the_invoice_price_corrects_the_batch_and_the_sales_that_took_it(db):
    a, _ = _item()
    _in(a, 4, 1000.0, date(2026, 10, 4), ref_id=7, est=True)
    sid = _sale(a, 3, 1500, 4501)
    assert _cost(db, sid) == 3000.0
    from engines.common import write_tx
    from engines.stock_ledger import correct_cost
    with write_tx() as conn:
        assert correct_cost(conn, a, 4, 1000.0, 1100.0, "purchase_order_lines", 7)
    assert _cost(db, sid) == 3300.0                                      # the sale's margin follows the price actually charged
    assert _items(db, a)[1:] == (1100.0, 1100.0)


def test_a_sale_before_its_delivery_takes_the_last_price_then_the_deliverys_cost(db):
    a, _ = _item()
    _in(a, 2, 1000.0, date(2026, 10, 1), ref_id=1)
    sid = _sale(a, 3, 1500, 4501)                                        # one piece more than recorded
    assert _cost(db, sid) == 3000.0                                      # 2 x 1,000 + 1 at the last price paid (1,000)
    assert _items(db, a)[0] == -1
    _in(a, 4, 1200.0, date(2026, 10, 5), ref_id=2)                        # the delivery is recorded: that piece costs 1,200 (answer 1 A)
    assert _cost(db, sid) == 3200.0
    assert _items(db, a) == (3, 1200.0, 1200.0)


def test_returns_go_back_first_at_the_cost_they_left_at(db):
    a, _ = _item()
    _in(a, 2, 1000.0, date(2026, 10, 1), ref_id=1)
    _in(a, 2, 1300.0, date(2026, 10, 5), ref_id=2)
    sid = _sale(a, 3, 1500, 4501)                                        # 2 x 1,000 + 1 x 1,300
    assert _cost(db, sid) == 3300.0
    from engines.sales_rules import get_sale, return_item
    line = get_sale(sid)["lines"][0]["line_id"]
    assert return_item(sid, line, 1, "wrong size")[0]                    # back at 1,100 (the sale's cost each), placed first
    assert _items(db, a)[2] == 1100.0


def test_an_undone_delivery_moves_its_sold_pieces_to_the_next_batch(db):
    a, _ = _item()
    _in(a, 2, 1000.0, date(2026, 10, 1), ref_id=1)
    _in(a, 4, 1200.0, date(2026, 10, 5), ref_id=2)
    sid = _sale(a, 3, 1500, 4501)                                        # 2 x 1,000 + 1 x 1,200
    from engines.common import write_tx
    from engines.stock_ledger import stock_back
    with write_tx() as conn:
        assert stock_back(conn, a, 2, 1000.0, "purchase_order_lines", 1, note="recorded by mistake")
    assert _cost(db, sid) == 3600.0                                      # those 2 pieces now come from the 1,200 batch
    assert _items(db, a) == (1, 1200.0, 1200.0)


def test_a_count_shortage_leaves_from_the_oldest_batch_and_a_surplus_takes_the_last_price(db):
    a, _ = _item()
    _in(a, 2, 1000.0, date(2026, 10, 1), ref_id=1)
    _in(a, 2, 1300.0, date(2026, 10, 5), ref_id=2)
    from engines.common import write_tx
    from engines.stock_ledger import stock_set
    with write_tx() as conn:
        stock_set(conn, a, 3, "stock_counts", 1, note="one missing")
    assert fetchone(db, "SELECT unit_cost FROM stock_ledger WHERE item_id = ? ORDER BY move_id DESC LIMIT 1", (a,))[0] == 1000.0
    with write_tx() as conn:
        stock_set(conn, a, 4, "stock_counts", 2, note="found one")
    assert fetchone(db, "SELECT unit_cost FROM stock_ledger WHERE item_id = ? ORDER BY move_id DESC LIMIT 1", (a,))[0] == 1300.0


def test_the_opening_count_is_the_first_batch_at_the_cost_typed(db):
    a, _ = _item()
    from engines.common import write_tx
    from engines.stock_ledger import stock_set
    with write_tx() as conn:
        conn.execute("UPDATE items SET avg_cost = 9500 WHERE id = ?", (a,))          # what the count's approval does with the notebook's cost
        stock_set(conn, a, 2, "stock_counts", 1, note="opening count")
    _in(a, 4, 9850.0, date(2026, 11, 4), ref_id=2)
    sid = _sale(a, 3, 12500, 4501)
    assert _cost(db, sid) == 2 * 9500 + 9850


def test_stock_from_before_fifo_becomes_one_batch_at_its_average(db):
    a, _ = _item()
    from engines.common import write_tx
    with write_tx() as conn:                                              # an item as it was before FIFO: on hand and an average, no batches
        conn.execute("UPDATE items SET on_hand = 5, avg_cost = 2000 WHERE id = ?", (a,))
    _in(a, 5, 2600.0, date(2026, 11, 1), ref_id=3)
    sid = _sale(a, 6, 3000, 4501)
    assert _cost(db, sid) == 5 * 2000 + 2600
