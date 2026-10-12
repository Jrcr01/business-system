"""The revenue cycle, stage A, the stock base (owner, 2026-10-11: "i am planning to start the revenue cycle ... with inventory management"; "print barcode
for each tire then upon sale we just scan it"; low stock "per item"; labels "for everything"; the label size not settled, so it is chosen at printing):
what is on hand and everything that happened to an item, the low-stock level per item, the System admin's adjustment, tire age from the DOT typed at
receiving, labels, and what a scan means. Stock is never typed over; it may read below zero (flagged, never blocked)."""
from datetime import date, timedelta

from conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin, _sign_in_new


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def _item(brand="Westlake", size="205/55R16", pattern="SA37"):
    from engines.product_catalog import get_or_create_product
    pid, full, _ = get_or_create_product("Tires", brand, size=size, pattern=pattern)
    return pid, full


def _stock(item_id, qty, cost=2840.0, dot=None):
    """Goods on the rack: a stock-in row, as receiving writes it."""
    from engines.common import write_tx
    from engines.stock_ledger import stock_in
    from engines.stock_rules import note_dot
    with write_tx() as conn:
        stock_in(conn, item_id, qty, cost, "opening", item_id, note="opening stock")
        note_dot(conn, item_id, dot)


def test_the_stock_list_shows_what_is_on_hand_and_its_value(web, db):
    _admin(web)
    a, name = _item()
    b, _ = _item("Apollo", "8.25-16", "AMAR DELUXE")
    _stock(a, 12)
    _stock(b, 2, 7310.25)
    page = web.get("/stock/").content.decode()
    assert name in page and "34,080.00" in page and "14,620.50" in page and "48,700.50" in page      # 12 x 2,840 + 2 x 7,310.25
    low = web.get("/stock/?show=low").content.decode()
    assert "Apollo" in low and name not in low                                                      # 2 on hand, low at 2
    assert name in web.get("/stock/?q=2055516+westlake").content.decode()                           # any words, digits run together
    xl = web.get("/stock/?xlsx=1")
    assert xl.status_code == 200 and "spreadsheetml" in xl["Content-Type"]


def test_a_scanned_label_opens_its_item_and_a_makers_barcode_is_learned_once(web, db):
    from engines.stock_rules import find_by_code
    _admin(web)
    a, name = _item()
    code = _n("SELECT code FROM items WHERE id = ?", (a,))
    assert web.get(f"/stock/?q={code.lower()}")["Location"] == f"/stock/{a}/"                       # our label carries the item's code
    assert find_by_code("6927116112345") is None
    web.post(f"/stock/{a}/", {"action": "barcode", "barcode": "6927116112345"})
    assert find_by_code("6927116112345")["id"] == a and web.get("/stock/?q=6927116112345")["Location"] == f"/stock/{a}/"
    b, _ = _item("Apollo", "8.25-16", "AMAR DELUXE")
    r = web.post(f"/stock/{b}/", {"action": "barcode", "barcode": "6927116112345"}, follow=True)
    assert "already means" in r.content.decode() and find_by_code("6927116112345")["id"] == a       # one barcode, one item
    items = web.get("/stock/items.json").json()["items"]
    mine = next(i for i in items if i["id"] == a)
    assert code in mine["codes"] and "6927116112345" in mine["codes"] and "2055516" in mine["find"]


def test_the_low_stock_level_is_per_item(web, db):
    _admin(web)
    a, name = _item()
    _stock(a, 5)
    assert name not in web.get("/stock/?show=low").content.decode()
    web.post(f"/stock/{a}/", {"action": "level", "level": "6"})
    assert _n("SELECT reorder_level FROM items WHERE id = ?", (a,)) == 6
    assert name in web.get("/stock/?show=low").content.decode()


def test_an_adjustment_is_the_system_admins_with_a_reason_and_adds_a_line(web, db):
    _admin(web)
    a, name = _item()
    _stock(a, 12)
    r = web.post(f"/stock/{a}/", {"action": "adjust", "counted": "11", "reason": ""}, follow=True)
    assert "Say why" in r.content.decode() and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 12
    web.post(f"/stock/{a}/", {"action": "adjust", "counted": "11", "reason": "found damaged, scrapped"})
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 11
    assert fetchone(_db(), "SELECT kind, qty, on_hand_after FROM stock_ledger WHERE item_id = ? ORDER BY move_id DESC LIMIT 1", (a,)) == ("ADJ", -1.0, 11.0)
    assert _n("SELECT COUNT(*) FROM review_flags WHERE kind = 'STOCK_ADJUSTED'") == 1
    page = web.get(f"/stock/{a}/").content.decode()
    assert "Adjusted" in page and "found damaged, scrapped" in page and "opening stock" in page    # history is added to, never edited
    family = _sign_in_new("family@slicks.test", "owner")                                            # an owner sees the item but may not adjust
    assert "Correct the quantity" not in family.get(f"/stock/{a}/").content.decode()
    family.post(f"/stock/{a}/", {"action": "adjust", "counted": "3", "reason": "try"})
    assert _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == 11


def test_tire_age_comes_from_the_dot_typed_at_receiving(web, db):
    from engines.clock import manila_today
    from engines.po_rules import confirm_request, request_po, save_po
    from engines.price_list_service import add_price
    from engines.stock_rules import clean_dot, dot_age, dot_text
    assert clean_dot("24/2025") == "2425" and clean_dot("2425") == "2425" and clean_dot("9925") is None and dot_text("2425") == "24/2025"
    assert 0.9 < dot_age("0125", date(2026, 1, 5)) < 1.1
    _admin(web)
    cid = make_creditor(db, "Acme Tires", terms="30")
    a, name = _item()
    add_price(cid, name, "pc", 2840, date(2026, 9, 1))
    ok, _, po = save_po(None, cid, manila_today(), manila_today() + timedelta(days=5), [{"item_name": name, "unit": "pc", "qty": 6}], "")
    assert ok and request_po(po)[0] and confirm_request(po)[0]
    lid = _n("SELECT line_id FROM purchase_order_lines WHERE po_id = ?", (po,))
    assert f'name="dot_{lid}"' in web.get(f"/purchase-orders/receive/?s={cid}&po={po}").content.decode()
    web.post("/purchase-orders/receive/", {"s": cid, "action": "arrive", f"pick_{lid}": "1", f"qty_{lid}": "6", f"price_{lid}": "2840", f"dot_{lid}": "49/2022",
                                           "doc": "", "dr": "DR-1", "inv_date": "2026-10-11", "batch": ""})
    item_id = _n("SELECT item_id FROM purchase_order_lines WHERE line_id = ?", (lid,))
    assert _n("SELECT dot FROM po_receipt_lines") == "4922" and _n("SELECT oldest_dot FROM items WHERE id = ?", (item_id,)) == "4922"
    page = web.get("/stock/?show=old").content.decode()
    assert name in page and "49/2022" in page and "Old stock" in page                               # older than 3 years
    rid = _n("SELECT receipt_id FROM po_receipts")
    assert f"/stock/labels/?receipt={rid}" in web.get(f"/purchase-orders/{po}/").content.decode()
    pdf = web.get(f"/stock/labels/?receipt={rid}&size=sheet24")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"


def test_labels_print_in_the_size_chosen_and_the_choice_is_remembered(web, db):
    _admin(web)
    a, _ = _item()
    _stock(a, 3)
    page = web.get(f"/stock/{a}/").content.decode()
    assert "Print labels" in page and "50 × 30 mm" in page and "A4 sheet of 24" in page
    pdf = web.get(f"/stock/labels/?item={a}&n=3&size=100x50")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert 'value="100x50" selected' in web.get(f"/stock/{a}/").content.decode()                   # the size last used is offered next time
    assert web.get("/stock/labels/?item=99999").status_code == 404


def test_stock_below_zero_is_allowed_and_flagged(db):
    from engines.common import write_tx
    from engines.stock_ledger import stock_out
    a, name = _item()
    _stock(a, 1)
    with write_tx() as conn:
        cost = stock_out(conn, a, 2, "sales", 1, note="sold on SI 000001")
    assert cost == 2840.0 and _n("SELECT on_hand FROM items WHERE id = ?", (a,)) == -1
    assert "reads -1" in _n("SELECT detail FROM review_flags WHERE kind = 'STOCK_NEGATIVE'")


def test_who_sees_stock_and_who_changes_it(web, db):
    _admin(web)
    a, _ = _item()
    payables = _sign_in_new("ap@slicks.test", "payables")
    assert payables.get("/stock/").status_code == 403                                               # not their page at all
    seller = _sign_in_new("sales@slicks.test", "sales")
    assert seller.get("/stock/").status_code == 200
    seller.post(f"/stock/{a}/", {"action": "level", "level": "9"})
    assert _n("SELECT reorder_level FROM items WHERE id = ?", (a,)) == 9


def test_labels_for_a_whole_brand_in_one_print_out(web, db):
    # owner, 2026-10-12: "better to have it ready" -> labels for a whole brand
    _admin(web)
    a, name = _item()
    b, _ = _item(size="195/65R15", pattern="RP18")
    _stock(a, 3)
    page = web.get("/stock/").content.decode()
    assert "Print labels for a whole brand" in page and '<option value="Westlake">' in page
    pdf = web.get("/stock/labels/?brand=westlake&per=onhand&size=sheet24")
    assert pdf.status_code == 200 and pdf["Content-Type"] == "application/pdf" and pdf.content[:4] == b"%PDF"       # 3 labels: only the item in stock
    each = web.get("/stock/labels/?brand=Westlake&per=each&n=2&size=sheet24")
    assert each.status_code == 200 and len(each.content) > len(pdf.content) * 0.9                                   # 4 labels: both items, 2 each
    none = web.get("/stock/labels/?brand=Nobody&per=each&n=1")
    assert none.status_code == 404 and b"no item of that brand" in none.content
    many = web.get("/stock/labels/?brand=Westlake&category=battery&per=onhand")
    assert many.status_code == 404
