"""The revenue cycle, stage A, the Sell and Affiliates screens (owner, 2026-10-11: "one invoice booklet in use"; the seller
sees cost and profit; a post-dated check makes the "sale paid"; the seller voids and the System admin reviews afterwards; an affiliate pays "at cost"):
the counter sale as it is posted from the screen, the day sheet in the notebook's order, released goods, a sale's page, customers, booklets, affiliates."""
from conftest import fetchone
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_stock import _item, _stock


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _ready(web, qty=12):
    a, name = _item()
    _stock(a, qty)
    web.post("/si-booklets/", {"action": "register", "start": "4501", "end": "4550", "holder": "Julian"})
    return a, name


def _sale(web, a, si="004501", qty="4", price="3600", **extra):
    data = {"action": "save", "si_no": si, "sale_date": "", "customer": "", "ln_item_0": a, "ln_qty_0": qty, "ln_price_0": price,
            "pay_method_0": "CASH", "pay_amount_0": str(float(qty) * float(price))}
    from engines.clock import manila_today
    data["sale_date"] = manila_today().isoformat()
    data.update(extra)
    return web.post("/sales/", data)


def test_the_counter_sale_offers_the_next_number_and_saves_in_one_step(web, db):
    _admin(web)
    a, name = _ready(web)
    page = web.get("/sales/").content.decode()                                 # recording sales is the page (owner, 2026-10-11: after close or idle time)
    assert 'name="si_no" value="004501"' in page and 'id="scanlines"' in page and "Save the sale" in page and "Release now, invoice later" in page
    r = _sale(web, a, pay_amount_0="5000", pay_method_1="CHECK", pay_amount_1="10000", pay_bank_1="BDO", pay_no_1="0048812", pay_date_1="2026-12-25",
              svc_desc_0="Mounting and balancing", svc_amt_0="600")
    assert r.status_code == 302 and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 8 and _n("SELECT total FROM sales") == 15000.0
    assert 'name="si_no" value="004502"' in web.get("/sales/?view=new").content.decode()
    sid = _n("SELECT sale_id FROM sales")
    page = web.get(f"/sales/{sid}/").content.decode()
    assert "SI 004501" in page and "Walk-in" in page and "BDO" in page and "0048812" in page and "3,640.00" in page and "Paid" in page      # cost and profit shown; a PDC is paid
    assert "SI 004501" in web.get(f"/stock/{a}/").content.decode()             # on the item's history, with its document


def test_a_refused_sale_keeps_what_was_typed(web, db):
    _admin(web)
    a, name = _ready(web)
    r = _sale(web, a, pay_amount_0="100", customer="Mang Tony")
    page = r.content.decode()
    assert r.status_code == 200 and "add up to" in page and 'value="Mang Tony"' in page and f'"id": {a}' in page and _n("SELECT COUNT(*) FROM sales") == 0
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 12


def test_a_charge_needs_a_named_customer_and_credit_is_the_system_admins(web, db):
    _admin(web)
    a, _ = _ready(web)
    r = _sale(web, a, customer="Mang Tony", pay_method_0="CHARGE")
    assert "named customer" in r.content.decode()
    web.post("/customers/", {"action": "save", "name": "RJM Trucking", "type": "FLEET", "terms": "30"})
    cust = _n("SELECT customer_id FROM customers")
    assert _sale(web, a, customer="rjm trucking", pay_method_0="CHARGE").status_code == 302          # the name typed is that customer
    assert _n("SELECT customer_id FROM sales") == cust and _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'SALE_NO_CREDIT'") == 1
    page = web.get("/customers/").content.decode()
    assert "RJM Trucking" in page and "14,400.00" in page and "Not allowed" in page
    seller = _sign_in_new("sales@slicks.test", "sales")
    seller.post("/customers/", {"action": "credit", "customer_id": cust, "on": "1"})
    assert _n("SELECT credit_allowed FROM customers") == 0                      # not the seller's to give
    web.post("/customers/", {"action": "credit", "customer_id": cust, "on": "1"})
    assert _n("SELECT credit_allowed FROM customers") == 1


def test_the_day_sheet_is_in_the_notebooks_order(web, db):
    _admin(web)
    a, name = _ready(web)
    _sale(web, a)
    _sale(web, a, si="004503", qty="1", price="2500", below_reason="old stock")
    page = web.get("/sales/?view=day").content.decode()
    head = page.split("<table")[1].split("</tr>")[0]
    assert head.index("Qty") < head.index("Item") < head.index("Profit") < head.index("Customer (if charge)") < head.index("SI no.")
    assert name in page and "16,900.00" in page and "Below cost" in page and "004502" in page and "skipped" in page      # 4502 was not typed
    assert "2,700.00" in page                                                   # 4 x 760 less 340 below cost


def test_released_goods_are_invoiced_from_their_page(web, db):
    from engines.clock import manila_today
    _admin(web)
    a, name = _ready(web)
    r = web.post("/sales/", {"action": "release", "customer": "Mang Tony", "ln_item_0": a, "ln_qty_0": "2", "ln_price_0": "", "si_no": ""})
    assert r["Location"] == "/sales/?view=released" and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 10
    page = web.get("/sales/?view=released").content.decode()
    assert "RL-0001" in page and "Mang Tony" in page and "Write the invoice" in page
    sid = _n("SELECT sale_id FROM sales")
    line = _n("SELECT line_id FROM sale_lines")
    assert "Save the invoice" in web.get(f"/sales/{sid}/").content.decode()
    web.post(f"/sales/{sid}/", {"action": "invoice", "si_no": "004501", "sale_date": manila_today().isoformat(), f"price_{line}": "3,600.00", "pay_method_0": "CASH", "pay_amount_0": "7200"})
    assert fetchone(_db(), "SELECT status, si_no, total FROM sales") == ("SAVED", 4501, 7200.0) and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 10
    assert "Nothing is out without its invoice" in web.get("/sales/?view=released").content.decode()


def test_the_seller_voids_and_returns_and_it_is_reviewed_afterwards(web, db):
    _admin(web)
    a, name = _ready(web)
    seller = _sign_in_new("sales@slicks.test", "sales")
    _sale(seller, a)
    sid, line = _n("SELECT sale_id FROM sales"), _n("SELECT line_id FROM sale_lines")
    seller.post(f"/sales/{sid}/", {"action": "return", "line_id": line, "qty": "1", "reason": "wrong size for the rim"})
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 9
    page = seller.get(f"/sales/{sid}/").content.decode()
    assert "wrong size for the rim" in page and "10,800.00" in page
    seller.post(f"/sales/{sid}/", {"action": "void", "reason": "written for the wrong customer"})              # no approval step
    assert _n("SELECT status FROM sales") == "VOID" and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 12
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'SALE_VOIDED'") == 1 and "Cancelled" in seller.get(f"/sales/{sid}/").content.decode()


def test_booklets_register_cancel_a_leaf_and_one_in_use(web, db):
    _admin(web)
    a, _ = _ready(web)
    web.post("/si-booklets/", {"action": "register", "start": "4551", "end": "4600", "holder": ""})
    page = web.get("/si-booklets/").content.decode()
    assert "SB-0001" in page and "SB-0002" in page and "In use" in page and "Waiting" in page and "Use this booklet now" in page
    web.post("/si-booklets/", {"action": "cancel_leaf", "si_no": "4501", "reason": "torn"})
    assert _n("SELECT COUNT(*) FROM si_cancelled") == 1 and 'name="si_no" value="004502"' in web.get("/sales/?view=new").content.decode()
    r = web.post("/si-booklets/", {"action": "register", "start": "4540", "end": "4560", "holder": ""}, follow=True)
    assert "overlap" in r.content.decode()


def test_affiliates_lend_borrow_and_settle_from_the_screens(web, db):
    _admin(web)
    a, name = _ready(web)
    web.post("/affiliates/", {"action": "add", "name": "Tita Baby Tire Center", "contact_person": "", "contact_number": ""})
    aff = _n("SELECT customer_id FROM customers WHERE is_affiliate = 1")
    assert 'data-mode="lend"' in web.get(f"/affiliates/{aff}/?do=lend").content.decode()
    web.post(f"/affiliates/{aff}/", {"action": "lend", "ln_item_0": a, "ln_qty_0": "6", "ln_price_0": "", "move_date": "", "taken_by": "Rico"})
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 6 and _n("SELECT COUNT(*) FROM sales") == 0
    page = web.get("/affiliates/").content.decode()
    assert "Tita Baby Tire Center" in page and "17,040.00" in page and "they owe" in page
    line = _n("SELECT line_id FROM affiliate_move_lines")
    web.post(f"/affiliates/{aff}/", {"action": "settle", f"pick_{line}": "1", f"qty_{line}": "2", "how": "RETURN"})
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 8
    web.post(f"/affiliates/{aff}/", {"action": "settle", f"pick_{line}": "1", f"qty_{line}": "1", "how": "PAY", "si_no": "004501", "pay_method_0": "CASH", "pay_amount_0": "2840"})
    assert fetchone(_db(), "SELECT si_no, total, customer_id, no_stock_out FROM sales") == (4501, 2840.0, aff, 1)      # a sale to them at our cost; the stock left with the lend
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 8
    page = web.get(f"/affiliates/{aff}/").content.decode()
    assert "AL-0001" in page and "Returned (ours)" in page and "Paid (ours)" in page and "SI 004501" in page
    assert "Lent to an affiliate" in web.get(f"/stock/{a}/").content.decode()


def test_home_and_the_menu_have_stock_and_sell(web, db):
    _admin(web)
    _ready(web)
    home = web.get("/").content.decode()
    for word in ("Items and stock", "Stock counts", "Affiliates", "Customers", "Invoice booklets", "Sales today"):
        assert word in home, word
    assert 'href="/sales/?view=day"' in home and 'href="/stock/"' in home
    payables = _sign_in_new("ap@slicks.test", "payables")
    page = payables.get("/").content.decode()
    assert 'href="/sales/"' not in page and payables.get("/sales/").status_code == 403           # not their work
    family = _sign_in_new("family@slicks.test", "owner")                                             # an owner sees it all
    assert family.get("/sales/").status_code == 200 and family.get("/stock-counts/").status_code == 200 and family.get("/affiliates/").status_code == 200


def test_the_home_cards_follow_the_order_of_the_work(web, db):
    # owner, 2026-10-11: "organize the tiles have a logical sequence" -- each row left to right as the work is done; a card that starts something
    # ("New order", "Import ...") sits just before the page it lands on; the rows follow the goods, then the money
    import re
    _admin(web)
    _ready(web)
    page = web.get("/").content.decode()
    rows = {}
    for block in page.split('<div class="home-sec"')[1:]:
        title = re.search(r'id="g-[^"]+">([^<]+)<', block).group(1).strip()
        rows[title] = [t.strip() for t in re.findall(r'<div class="t">([^<]+)</div>', block)]
    assert list(rows)[:6] == ["My home", "Buy", "Stock", "Sell", "Pay suppliers", "Bank"]
    assert rows["Buy"] == ["Import a price list", "Price lists", "New order", "Purchase orders", "Receive"]
    assert rows["Stock"] == ["Items and stock", "Stock counts", "Affiliates"]
    assert rows["Sell"] == ["Record the day&#x27;s invoices", "Sales today", "Customers", "Invoice booklets"]
    assert rows["Pay suppliers"] == ["New batch", "Order statements", "Calendar", "Days over the limit", "Daily payables"]
    assert rows["Bank"] == ["Import bank statement", "Bank and cash", "Check booklets"]
    assert rows["Other payables"] == ["New loan note", "Lenders", "Bank loans", "Credit cards"]
    assert rows["Analytics"] == ["Purchasing", "Payables", "Bank and cash", "Lenders"]
    assert rows["My home"] == ["New order", "Purchase orders", "New batch", "Order statements", "Calendar", "Daily payables", "Checks and review"]
    assert 'href="/sales/?view=scan"' not in page and "the next SI no." in page    # the scan thing is hidden until its switch is on
