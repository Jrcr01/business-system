"""The purchase-order item picker (owner, 2026-10-08): the supplier's items with list price, discount and net for searching and filtering in the browser, and
several ticked items added in one save, on a draft and on New order."""
from engines.common import write_tx
from tests.conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_d2b import _stamp, _start


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _supplier(db):
    from engines import product_catalog as pc
    from engines.price_list_service import add_price
    cid = make_creditor(db, "Westlake Tires")
    for pattern, size, price in (("RP18", "205/55R16", 3100), ("SA07", "205/55R16", 3450), ("SL369", "285/50R20", 7600)):
        pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size=size, pattern=pattern, terrain="A/T" if pattern == "SL369" else "")
        add_price(cid, name, "pc", price, __import__("datetime").date(2026, 1, 1))
        pc.link_price_rows(cid, name, pid)
    with write_tx() as conn:
        conn.execute("INSERT INTO supplier_discounts (creditor_id, brand, chain, effective_date) VALUES (?, '', '5,5', '2026-01-01')", (cid,))
    return cid


def test_picker_items_carry_size_rim_terrain_and_the_net_price(web, db):
    from engines.price_list_service import picker_items
    _admin(web)
    cid = _supplier(db)
    items = {i["name"]: i for i in picker_items(cid)}
    rp18 = items["Westlake 205/55R16 RP18"]
    assert rp18["size"] == "205/55R16" and rp18["rim"] == "16" and rp18["pattern"] == "RP18" and rp18["list_price"] == 3100.0
    assert rp18["chain"] == "5,5" and rp18["net"] == 2797.75 and "2055516" in rp18["find"]
    assert items["Westlake 285/50R20 (A/T) SL369"]["terrain"] == "A/T"
    with write_tx() as conn:
        conn.execute("UPDATE items SET is_active = 0 WHERE name = 'Westlake 205/55R16 SA07'")
    assert "Westlake 205/55R16 SA07" not in {i["name"] for i in picker_items(cid)}            # a discontinued item is not offered
    got = web.get(f"/purchase-orders/items/?s={cid}&on=2026-10-01").json()["items"]
    assert len(got) == 2


def test_the_item_list_is_only_for_people_who_may_see_orders(web, db):
    _admin(web)
    cid = _supplier(db)
    mother = _sign_in_new("mother@slicks.test", "releaser")
    assert mother.get(f"/purchase-orders/items/?s={cid}").status_code == 403


def test_several_ticked_items_are_added_in_one_save(web, db):
    _admin(web)
    cid = _supplier(db)
    web.post("/price-lists/", {"s": cid, "action": "add_price", "item": "Eagle F1", "unit": "pc", "price": "1000", "start": "2026-01-01", "code": ""})
    po_id = _start(web, cid)
    r = web.post(f"/purchase-orders/{po_id}/edit/", {"action": "add_many", "stamp": _stamp(po_id), "order_date": "2026-10-01", "delivery": "2026-10-08",
                                                    "pick_name_0": "Westlake 205/55R16 RP18", "pick_qty_0": "8", "pick_unit_0": "pc",
                                                    "pick_name_1": "Westlake 285/50R20 (A/T) SL369", "pick_qty_1": "4", "pick_unit_1": "pc",
                                                    "pick_name_2": "Westlake 205/55R16 SA07", "pick_qty_2": "0"})
    assert r.status_code == 302
    rows = dict(__import__("sqlite3").connect(_db()).execute("SELECT item_name, est_unit_price FROM purchase_order_lines WHERE po_id = ?", (po_id,)).fetchall())
    assert rows["Westlake 205/55R16 RP18"] == 2797.75 and rows["Westlake 285/50R20 (A/T) SL369"] == 6859.0 and len(rows) == 3      # list less 5, 5; qty 0 skipped


def test_a_changed_draft_refuses_the_picked_items(web, db):
    _admin(web)
    cid = _supplier(db)
    web.post("/price-lists/", {"s": cid, "action": "add_price", "item": "Eagle F1", "unit": "pc", "price": "1000", "start": "2026-01-01", "code": ""})
    po_id = _start(web, cid)
    old = _stamp(po_id)
    web.post(f"/purchase-orders/{po_id}/edit/", {"action": "update", "stamp": old, "qty_0": "3", "list_price_0": "1000", "chain_0": "", "unit_0": "pc"})
    r = web.post(f"/purchase-orders/{po_id}/edit/", {"action": "add_many", "stamp": old, "pick_name_0": "Westlake 205/55R16 RP18", "pick_qty_0": "2"}, follow=True)
    assert b"Someone else changed this order" in r.content
    assert fetchone(_db(), "SELECT COUNT(*) FROM purchase_order_lines WHERE po_id = ?", (po_id,))[0] == 1


def test_new_order_starts_a_draft_with_the_picked_items(web, db):
    _admin(web)
    cid = _supplier(db)
    page = web.get(f"/purchase-orders/new/?s={cid}").content.decode()
    assert 'id="picker"' in page and "Type an item by hand" in page
    r = web.post("/purchase-orders/new/", {"s": cid, "action": "start", "order_date": "2026-10-01", "delivery": "2026-10-08", "item": "", "qty": "1",
                                           "pick_name_0": "Westlake 205/55R16 RP18", "pick_qty_0": "2", "pick_name_1": "Westlake 205/55R16 SA07", "pick_qty_1": "1"})
    assert r.status_code == 302 and r["Location"].endswith("/edit/")
    po_id = int(r["Location"].split("/")[2])
    assert fetchone(_db(), "SELECT COUNT(*), SUM(qty) FROM purchase_order_lines WHERE po_id = ?", (po_id,)) == (2, 3.0)
