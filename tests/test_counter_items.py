"""Recording sales before the price lists and booklets are all in (owner, 2026-10-18): a product not in the list added at the counter; the profit typed
works the cost back when an item has none ("profit only no %"); invoice numbers typed before their booklet fall into its sequence when it is registered."""
import json

from conftest import fetchone
from tests.test_web_d0 import _admin


def test_a_product_added_at_the_counter_goes_on_the_sale_with_its_cost_from_the_profit(web, db):
    from engines.clock import manila_today
    from engines.sales_rules import save_sale
    _admin(web)
    r = json.loads(web.post("/stock/add-item/", {"kind": "Tires", "brand": "Goodyear", "size": "205/65R15", "pattern": "Assurance"}).content)
    assert r["ok"] and r["item"]["name"].startswith("Goodyear") and r["item"]["cost"] == 0
    again = json.loads(web.post("/stock/add-item/", {"kind": "Tires", "brand": "Goodyear", "size": "205/65R15", "pattern": "Assurance"}).content)
    assert again["item"]["id"] == r["item"]["id"] and "already" in again["msg"]                   # never made twice
    assert fetchone(db, "SELECT COUNT(*) FROM review_flags WHERE kind = 'ITEM_ADDED_AT_COUNTER'")[0] == 1
    iid = r["item"]["id"]
    bad = save_sale(5001, manila_today(), None, "Walk-in", [{"item_id": iid, "qty": 4, "price": 3450, "margin": "15%"}], [], [{"method": "CASH", "amount": 13800}])
    assert not bad[0] and "pesos" in bad[1]                                                         # profit in pesos only
    ok, msg, sid = save_sale(5001, manila_today(), None, "Walk-in", [{"item_id": iid, "qty": 4, "price": 3450, "margin": "450"}], [], [{"method": "CASH", "amount": 13800}])
    assert ok, msg
    assert fetchone(db, "SELECT unit_cost FROM sale_lines WHERE sale_id = ?", (sid,))[0] == 3000.0  # 3,450 less 450 profit
    assert fetchone(db, "SELECT cost_total FROM sales WHERE sale_id = ?", (sid,))[0] == 12000.0
    assert fetchone(db, "SELECT COUNT(*) FROM review_flags WHERE kind = 'COST_FROM_MARGIN'")[0] == 1


def test_invoices_typed_before_their_booklet_fall_into_its_sequence(web, db):
    from engines.clock import manila_today
    from engines.sales_rules import register_booklet, save_sale
    from tests.test_web_stock import _item
    _admin(web)
    a, _ = _item("Sunny", "205/55R16", "NP226")
    for n in (7001, 7003):
        ok, msg, _ = save_sale(n, manila_today(), None, "Walk-in", [{"item_id": a, "qty": 1, "price": 2600}], [], [{"method": "CASH", "amount": 2600}])
        assert ok, msg
    open_notes = "SELECT detail FROM review_flags WHERE kind = 'SI_SKIPPED' AND reviewed_at IS NULL ORDER BY flag_id"
    import sqlite3
    with sqlite3.connect(db) as conn:
        assert len(conn.execute(open_notes).fetchall()) == 2                                       # "not in any registered booklet"
    ok, msg = register_booklet(7001, 7050, "Julian")
    assert ok and "2 sale(s) already typed" in msg
    with sqlite3.connect(db) as conn:
        left = [r[0] for r in conn.execute(open_notes).fetchall()]
    assert len(left) == 1 and "007002" in left[0] and "skipped" in left[0]                          # only the real gap remains


def test_without_a_booklet_the_next_number_is_the_last_plus_one(web, db):
    from engines.clock import manila_today
    from engines.sales_rules import next_si_no, save_sale
    from tests.test_web_stock import _item
    _admin(web)
    assert next_si_no() is None
    a, _ = _item("Sunny", "205/55R16", "NP226")
    assert save_sale(8120, manila_today(), None, "Walk-in", [{"item_id": a, "qty": 1, "price": 2600}], [], [{"method": "CASH", "amount": 2600}])[0]
    assert next_si_no() == 8121
    assert 'value="008121"' in web.get("/sales/?view=new").content.decode()


def test_the_phone_sale_takes_an_own_price_and_profit_for_an_item_with_no_cost(web, db):
    from engines import pos_sale
    from engines.clock import manila_today
    from engines.stock_rules import add_counter_item
    from tests.test_web_stock import _item, _stock
    _admin(web)
    ok, msg, new = add_counter_item("Tires", "Goodyear", "205/65R15", "Assurance")
    assert ok, msg
    a, _ = _item("Sunny", "185/55R16", "NP226")
    _stock(a, 4, 2000.0)
    ok, msg, sid = pos_sale.record(9001, manila_today().isoformat(), None, "Walk-in",
                                   [{"item_id": new["id"], "qty": 4, "price": "3450", "margin": "450"}, {"item_id": a, "qty": 2, "price": "", "margin": ""}],
                                   "18800", [], {"method": "CASH"})
    assert ok, msg
    rows = {r[0]: (r[1], r[2]) for r in __import__("sqlite3").connect(db).execute("SELECT item_id, unit_price, unit_cost FROM sale_lines WHERE sale_id = ?", (sid,))}
    assert rows[new["id"]] == (3450.0, 3000.0)                       # its own price; cost worked back from the profit
    assert rows[a][0] == 2500.0                                      # the rest of the receipt: (18,800 - 13,800) / 2
    bad = pos_sale.record(9002, manila_today().isoformat(), None, "Walk-in", [{"item_id": new["id"], "qty": 1, "price": "3450", "margin": "450"},
                                                                          {"item_id": a, "qty": 1, "price": "", "margin": ""}], "3000", [], {"method": "CASH"})
    assert not bad[0] and "more than the amount received" in bad[1]
    from tests.test_phone import _on
    from tests.test_phone_approval import PHONE_UA
    _on()
    r = web.get("/phone/sale/", HTTP_USER_AGENT=PHONE_UA, follow=True)
    page = r.content.decode()
    assert r.status_code == 200, r.redirect_chain
    assert 'data-phonesale="1"' in page and 'id="sl_new"' in page


def test_the_phone_sale_prices_each_item_and_a_lower_amount_received_is_a_discount(web, db):
    from engines import pos_sale
    from engines.clock import manila_today
    from tests.test_web_stock import _item, _stock
    _admin(web)
    a, _ = _item("Sunny", "185/55R16", "NP226")
    b, _ = _item("Westlake", "205/55R16", "RP26")
    _stock(a, 4, 2000.0)
    _stock(b, 4, 2500.0)
    ok, msg, sid = pos_sale.record(9101, manila_today().isoformat(), None, "Walk-in",
                                   [{"item_id": a, "qty": 2, "price": "2600", "margin": ""}, {"item_id": b, "qty": 1, "price": "3000", "margin": ""}],
                                   "8200", [], {"method": "CASH"})
    assert ok, msg
    import sqlite3
    rows = dict(sqlite3.connect(db).execute("SELECT item_id, unit_price FROM sale_lines WHERE sale_id = ? AND item_id IS NOT NULL", (sid,)).fetchall())
    assert rows == {a: 2600.0, b: 3000.0}                                                              # each at the price typed
    ok, msg, sid2 = pos_sale.record(9102, manila_today().isoformat(), None, "Walk-in",
                                    [{"item_id": a, "qty": 2, "price": "2600", "margin": ""}], "5000", [], {"method": "CASH"})
    assert ok, msg                                                                                     # 5,200 typed over to 5,000: a discount
    assert sqlite3.connect(db).execute("SELECT total FROM sales WHERE sale_id = ?", (sid2,)).fetchone()[0] == 5000.0
