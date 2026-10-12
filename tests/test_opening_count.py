"""The opening count, add as you go (owner, 2026-10-13: "we have inventory notebook", "these are not only brand we have"): a count that starts
empty; each tire is found or added at the rack, with the cost from the notebook; approved on the laptop like any count."""
from conftest import fetchone
from tests.test_phone import _db, _n, _on
from tests.test_web_d0 import _admin, _sign_in_new
from tests.test_web_stock import _item, _stock


def _open(web) -> int:
    r = web.post("/phone/count/", {"action": "start_opening", "counted_by": "Jeane"})
    cid = _n("SELECT MAX(count_id) FROM stock_counts")
    assert r["Location"] == f"/phone/count/{cid}/"
    return cid


def test_an_item_found_and_one_added_at_the_rack_then_approved(web, db):
    _admin(web)
    _on()
    a, name = _item()
    assert "Opening count: add as you go" in web.get("/phone/count/").content.decode()
    cid = _open(web)
    page = web.get(f"/phone/count/{cid}/").content.decode()
    assert 'id="op_q"' in page and "Nothing counted yet" in page and "/stock/items.json" in page
    # found: an item the system has, never received (no cost): the cost typed from the notebook
    assert web.post(f"/phone/count/{cid}/", {"action": "add", "item_id": a, "qty": "8", "cost": "2,612.50"}).status_code == 302
    # not found: added and counted in one step
    page = web.get(f"/phone/count/{cid}/add/?q=205 55 16 mich").content.decode()
    assert "Add and count" in page and 'value="Mich"' in page
    r = web.post(f"/phone/count/{cid}/add/", {"category": "tire", "brand": "Michelin", "f_tire_size": "205/55R16", "f_tire_pattern": "Primacy 4", "qty": "6", "cost": "5200", "dot": "2425"})
    assert r["Location"] == f"/phone/count/{cid}/"
    b = _n("SELECT id FROM items WHERE brand = 'Michelin'")
    assert fetchone(_db(), "SELECT counted, cost_typed, is_new FROM stock_count_lines WHERE count_id = ? AND item_id = ?", (cid, b)) == (6.0, 5200.0, 1)
    assert _n("SELECT oldest_dot FROM items WHERE id = ?", (b,)) == "2425"
    # the same tire on a second rack: the quantity is added, not a second line
    web.post(f"/phone/count/{cid}/", {"action": "add", "item_id": b, "qty": "2"})
    assert _n("SELECT counted FROM stock_count_lines WHERE count_id = ? AND item_id = ?", (cid, b)) == 8
    page = web.get(f"/phone/count/{cid}/").content.decode()
    assert "new item" in page and name in page and "5200.00" in page
    assert _n("SELECT on_hand FROM items WHERE id = ?", (b,)) == 0                          # nothing moves until the laptop approves
    la, lb = (_n("SELECT line_id FROM stock_count_lines WHERE count_id = ? AND item_id = ?", (cid, i)) for i in (a, b))
    r = web.post(f"/phone/count/{cid}/", {"action": "finish", f"c_{la}": "8", f"k_{la}": "2612.50", f"c_{lb}": "8", f"k_{lb}": "5200", "counted_by": "Jeane"})
    assert r["Location"] == "/phone/count/"
    page = web.get(f"/stock-counts/{cid}/").content.decode()
    assert "new item" in page and "41,600.00" in page                                       # 8 x 5,200
    assert web.post(f"/stock-counts/{cid}/", {"action": "approve_all", "reason": "opening count"}).status_code == 302
    assert fetchone(_db(), "SELECT on_hand, avg_cost FROM items WHERE id = ?", (b,)) == (8.0, 5200.0)
    assert fetchone(_db(), "SELECT on_hand, avg_cost FROM items WHERE id = ?", (a,)) == (8.0, 2612.5)


def test_a_cost_left_empty_is_marked_and_typed_later_on_the_laptop(web, db):
    _admin(web)
    _on()
    cid = _open(web)
    web.post(f"/phone/count/{cid}/add/", {"category": "tire", "brand": "GT Radial", "f_tire_size": "11R22.5", "f_tire_pattern": "GT279", "qty": "2", "cost": ""})
    b = _n("SELECT id FROM items WHERE brand = 'GT Radial'")
    lid = _n("SELECT line_id FROM stock_count_lines WHERE item_id = ?", (b,))
    assert "no cost yet" in web.get(f"/phone/count/{cid}/").content.decode()
    web.post(f"/phone/count/{cid}/", {"action": "finish", f"c_{lid}": "2", f"k_{lid}": "", "counted_by": "Jeane"})
    assert "no cost" in web.get(f"/stock-counts/{cid}/").content.decode()
    web.post(f"/stock-counts/{cid}/", {"action": "approve_all", "reason": "opening count"})                # approved without a cost: flagged, never blocked
    assert fetchone(_db(), "SELECT on_hand, avg_cost FROM items WHERE id = ?", (b,)) == (2.0, 0.0)
    assert "no cost" in web.get(f"/stock-counts/{cid}/").content.decode()
    web.post(f"/stock-counts/{cid}/", {"action": "cost", "line_id": lid, "cost": "14,500"})
    assert _n("SELECT avg_cost FROM items WHERE id = ?", (b,)) == 14500
    assert _n("SELECT COUNT(*) FROM stock_ledger WHERE item_id = ? AND kind = 'COST'", (b,)) == 1
    assert '<span class="pill warn">no cost</span>' not in web.get(f"/stock-counts/{cid}/").content.decode()


def test_the_item_with_its_own_cost_keeps_it_and_the_same_item_is_not_made_twice(web, db):
    _admin(web)
    _on()
    a, name = _item()
    _stock(a, 3)                                                                             # received before: it has a cost of its own
    own = _n("SELECT avg_cost FROM items WHERE id = ?", (a,))
    cid = _open(web)
    web.post(f"/phone/count/{cid}/", {"action": "add", "item_id": a, "qty": "5", "cost": "1"})
    assert _n("SELECT cost_typed FROM stock_count_lines WHERE item_id = ?", (a,)) is None
    brand, size, pattern = fetchone(_db(), "SELECT brand, size_model, json_extract(specs, '$.pattern') FROM items WHERE id = ?", (a,))
    before = _n("SELECT COUNT(*) FROM items")
    web.post(f"/phone/count/{cid}/add/", {"category": "tire", "brand": brand.upper(), "f_tire_size": size, "f_tire_pattern": pattern, "qty": "1"})
    assert _n("SELECT COUNT(*) FROM items") == before                                        # the same tire: counted, not made again
    assert _n("SELECT counted FROM stock_count_lines WHERE item_id = ?", (a,)) == 6
    lid = _n("SELECT line_id FROM stock_count_lines WHERE item_id = ?", (a,))
    web.post(f"/phone/count/{cid}/", {"action": "finish", f"c_{lid}": "6", "counted_by": "Jeane"})
    web.post(f"/stock-counts/{cid}/", {"action": "approve_all", "reason": "opening count"})
    assert fetchone(_db(), "SELECT on_hand, avg_cost FROM items WHERE id = ?", (a,)) == (6.0, own)
    # what is needed is asked for; items are added only to an opening count
    r = web.post(f"/phone/count/{cid}/add/", {"category": "tire", "brand": "Giti", "f_tire_size": "", "qty": "1"})
    assert r.status_code in (200, 302) and _n("SELECT COUNT(*) FROM items WHERE brand = 'Giti'") == 0


def test_the_staff_phone_counts_but_does_not_add_items_or_see_costs(web, db):
    _admin(web)
    _on()
    a, name = _item()
    cid = _open(web)
    web.post(f"/phone/count/{cid}/", {"action": "add", "item_id": a, "qty": "1", "cost": "999"})
    staff = _sign_in_new("warehouse@slicks.test", "scanner")
    page = staff.get(f"/phone/count/{cid}/").content.decode()
    assert "mayAdd = false" in page and 'name="k_' not in page and "999" not in page
    assert staff.get(f"/phone/count/{cid}/add/")["Location"] == f"/phone/count/{cid}/"
    staff.post(f"/phone/count/{cid}/add/", {"category": "tire", "brand": "Giti", "f_tire_size": "185/65R15", "f_tire_pattern": "X", "qty": "1"})
    assert _n("SELECT COUNT(*) FROM items WHERE brand = 'Giti'") == 0
    assert staff.post(f"/phone/count/{cid}/", {"action": "add", "item_id": a, "qty": "2", "cost": "5"}).status_code == 302
    assert fetchone(_db(), "SELECT counted, cost_typed FROM stock_count_lines WHERE item_id = ?", (a,)) == (3.0, 999.0)
