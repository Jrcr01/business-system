"""Record a sale on the phone from the cash receipt, Take in for surplus and trade-ins, Sales today (owner, 2026-10-12: "The input amount is cash
receipts just workback the profit"; "catchall name surplus tire, rim, estimate cost")."""
from conftest import fetchone
from tests.test_phone import _db, _n, _on
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_stock import _item, _stock


def _booklet(web):
    web.post("/si-booklets/", {"action": "register", "start": "4501", "end": "4550", "holder": "Julian"})


def test_the_receipt_is_spread_over_the_lines_by_cost():
    from engines.pos_sale import spread
    out = spread([{"item_id": 1, "qty": 2, "cost": 1000.0}, {"item_id": 2, "qty": 1, "cost": 3000.0}], 6000.0, 1000.0)       # 5,000 for goods
    assert [(o["item_id"], o["price"]) for o in out] == [(1, "1000.00"), (2, "3000.00")]                                     # 2,000 : 3,000
    out = spread([{"item_id": 1, "qty": 3, "cost": 0}, {"item_id": 2, "qty": 1, "cost": 0}], 1000.0)                           # all free: evenly
    assert [o["price"] for o in out] == ["250.00", "250.00"]
    out = spread([{"item_id": 1, "qty": 3, "cost": 100.0}], 1000.0)
    assert out[0]["price"] == "333.33"


def test_a_sale_recorded_on_the_phone_from_the_receipt(web, db):
    _admin(web)
    _on()
    _booklet(web)
    a, name = _item()
    _stock(a, 12, cost=2840.0)
    page = web.get("/phone/sale/").content.decode()
    assert 'value="004501"' in page and "Amount received" in page and 'data-mode="out"' in page
    r = web.post("/phone/sale/", {"si_no": "004501", "sale_date": "", "customer": "", "ln_item_0": a, "ln_qty_0": "2", "ln_price_0": "", "receipt": "7,000",
                                  "method": "CASH", "svc_desc_0": "Mounting", "svc_amt_0": "400"})
    assert r.status_code == 302 and r["Location"] == "/phone/sale/", r.content[:300]
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 10
    assert fetchone(_db(), "SELECT status, total, cost_total FROM sales") == ("SAVED", 7000.0, 5680.0)
    assert fetchone(_db(), "SELECT qty, unit_price, amount FROM sale_lines WHERE item_id = ?", (a,)) == (2.0, 3300.0, 6600.0)   # 7,000 less the 400 service
    assert fetchone(_db(), "SELECT method, amount FROM sale_payments") == ("CASH", 7000.0)
    assert 'value="004502"' in web.get("/phone/sale/").content.decode()                       # the next number offered
    page = web.get("/phone/day/").content.decode()
    assert "SI 004501" in page and "7,000.00" in page and "1,320.00" in page                  # profit 7,000 - 5,680
    assert "Record a sale" in web.get("/phone/").content.decode()


def test_a_slip_of_the_day_is_invoiced_from_the_phone_without_taking_stock_twice(web, db):
    _admin(web)
    _on()
    _booklet(web)
    a, name = _item()
    _stock(a, 12, cost=2840.0)
    assert web.post("/phone/out/", {"action": "slip", "customer": "", "si_hint": "", "ln_item_0": a, "ln_qty_0": "3", "ln_price_0": ""}).status_code == 302
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 9
    sale_id = _n("SELECT sale_id FROM sales")
    page = web.get("/phone/").content.decode()
    assert "1 slip to invoice" in page
    page = web.get(f"/phone/sale/?slip={sale_id}").content.decode()
    assert "From slip" in page and f'name="release_id" value="{sale_id}"' in page
    r = web.post("/phone/sale/", {"si_no": "004501", "sale_date": "", "customer": "", "release_id": sale_id, "receipt": "10,500", "method": "TRANSFER", "reference": "GC123"})
    assert r.status_code == 302, r.content[:300]
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 9                             # not taken twice
    assert fetchone(_db(), "SELECT status, si_no, total FROM sales WHERE sale_id = ?", (sale_id,)) == ("SAVED", 4501, 10500.0)
    assert fetchone(_db(), "SELECT method, reference FROM sale_payments") == ("TRANSFER", "GC123")


def test_surplus_tires_are_taken_in_at_an_estimate_and_sold_from_the_receipt(web, db):
    _admin(web)
    _on()
    _booklet(web)
    page = web.get("/phone/stock/?take=1").content.decode()
    assert "Take in" in page and "Surplus tire" in page and "Surplus rim" in page
    from engines.stock_rules import surplus_items
    tire = next(s for s in surplus_items() if s["name"] == "Surplus tire")
    r = web.post("/phone/stock/", {"action": "take_in", "item_id": tire["id"], "qty": "2", "cost": "500", "note": "trade-in from RVC"})
    assert r.status_code == 302
    assert fetchone(_db(), "SELECT on_hand, avg_cost FROM items WHERE id = ?", (tire["id"],)) == (2, 500.0)
    assert _n("SELECT COUNT(*) FROM stock_ledger WHERE item_id = ? AND ref_type = 'take_in'", (tire["id"],)) == 1
    assert _n("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'STOCK_TAKEN_IN'") == 1
    r = web.post("/phone/stock/", {"action": "take_in", "item_id": tire["id"], "qty": "1", "cost": "0", "note": ""})
    assert "where they came from" in web.get("/phone/stock/").content.decode()               # a note is needed
    r = web.post("/phone/sale/", {"si_no": "004501", "sale_date": "", "customer": "", "ln_item_0": tire["id"], "ln_qty_0": "2", "ln_price_0": "", "receipt": "3,000", "method": "CASH"})
    assert r.status_code == 302
    assert fetchone(_db(), "SELECT total, cost_total FROM sales") == (3000.0, 1000.0)          # profit 2,000: the receipt less the estimate
    assert _n("SELECT on_hand FROM items WHERE id = ?", (tire["id"],)) == 0


def test_the_staff_phone_does_not_record_sales(web, db):
    _admin(web)
    _on()
    staff = _sign_in_new("warehouse@slicks.test", "scanner")
    page = staff.get("/phone/").content.decode()
    assert 'href="/phone/sale/"' not in page and 'href="/phone/out/"' in page
    assert staff.get("/phone/sale/").status_code == 403


def test_a_surplus_tire_sold_on_the_spot_is_taken_in_at_the_cost_typed(web, db):
    # owner, 2026-10-12: "include the surplus then let the user state the cost"
    _admin(web)
    _on()
    _booklet(web)
    from engines.stock_rules import surplus_items
    tire = next(s for s in surplus_items() if s["name"] == "Surplus tire")
    page = web.get("/phone/sale/").content.decode()
    assert "Surplus or trade-in sold" in page and f'value="{tire["id"]}"' in page
    r = web.post("/phone/sale/", {"si_no": "004501", "sale_date": "", "customer": "", "receipt": "2,500", "method": "CASH",
                                  "sp_item_0": tire["id"], "sp_qty_0": "1", "sp_cost_0": "800"})
    assert r.status_code == 302, r.content[:300]
    assert fetchone(_db(), "SELECT on_hand FROM items WHERE id = ?", (tire["id"],)) == (0,)                  # in at 800, out on the sale
    assert _n("SELECT COUNT(*) FROM stock_ledger WHERE item_id = ? AND ref_type = 'take_in'", (tire["id"],)) == 1
    assert fetchone(_db(), "SELECT total, cost_total FROM sales") == (2500.0, 800.0)                            # profit 1,700
    r = web.post("/phone/sale/", {"si_no": "004502", "sale_date": "", "customer": "", "receipt": "100", "method": "CASH"})
    assert r.status_code == 200 and "Scan or pick what was sold" in r.content.decode()                       # nothing sold: refused
