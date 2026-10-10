"""Split a purchase order into parts (owner, 2026-10-10: "suggest a partition of purchase order"; separate POs, suggested then edited, always offered
on a draft): the parts always add up to the order, later parts land on pay days under the daily target, and each part is a normal draft PO."""
from datetime import date, timedelta

from conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _draft(db, lines):
    from engines.clock import manila_today
    from engines.po_rules import save_po
    from engines.price_list_service import add_price
    cid = make_creditor(db, "Acme Tires", terms="30")
    for name, price in {"Tire A": 50_000, "Tire B": 30_000, "Tube": 1_000}.items():
        add_price(cid, name, "pc", price, date(2026, 9, 1))
    ok, msg, po = save_po(None, cid, manila_today(), manila_today() + timedelta(days=5), lines, "")
    assert ok, msg
    return cid, po


def test_the_suggestion_keeps_every_quantity_and_spreads_the_pay_days(db):
    from engines.po_rules import get_po, suggest_split
    cid, po = _draft(db, [{"item_name": "Tire A", "unit": "pc", "qty": 6}, {"item_name": "Tire B", "unit": "pc", "qty": 5}])   # ₱450,000
    s = suggest_split(po, 2)
    assert s["total"] == 450_000 and len(s["parts"]) == 2
    lines = get_po(po)["lines"]
    for ln in lines.itertuples():
        assert sum(p["qty"].get(int(ln.line_id), 0) for p in s["parts"]) == ln.qty      # nothing left out, nothing doubled
    assert round(sum(p["amount"] for p in s["parts"]), 2) == 450_000
    assert s["parts"][1]["delivery"] > s["parts"][0]["delivery"]
    assert all(p["day_total"] <= 350_000.005 for p in s["parts"])                        # an empty calendar: both fit under the target


def test_one_big_line_is_split_by_quantity(db):
    from engines.po_rules import suggest_split
    cid, po = _draft(db, [{"item_name": "Tire A", "unit": "pc", "qty": 9}])               # one line, ₱450,000
    s = suggest_split(po, 2)
    qtys = sorted(sum(p["qty"].values()) for p in s["parts"])
    assert sum(qtys) == 9 and all(q >= 1 for q in qtys) and len(qtys) == 2


def test_saving_makes_part_one_and_new_drafts_and_refuses_a_wrong_total(db):
    from engines.po_rules import get_po, po_stamp, save_split
    cid, po = _draft(db, [{"item_name": "Tire A", "unit": "pc", "qty": 6}, {"item_name": "Tire B", "unit": "pc", "qty": 5}])
    lines = {r.item_name: int(r.line_id) for r in get_po(po)["lines"].itertuples()}
    a, b = lines["Tire A"], lines["Tire B"]
    d1, d2 = date.today() + timedelta(days=5), date.today() + timedelta(days=12)
    ok, msg, _ = save_split(po, [{"delivery": d1, "qty": {a: 6}}, {"delivery": d2, "qty": {b: 4}}], po_stamp(po))
    assert not ok and "Tire B" in msg                                                    # one Tire B left out: refused
    ok, msg, ids = save_split(po, [{"delivery": d1, "qty": {a: 6}}, {"delivery": d2, "qty": {b: 5}}], po_stamp(po))
    assert ok, msg
    assert ids[0] == po and len(ids) == 2
    one, two = get_po(ids[0]), get_po(ids[1])
    assert one["total"] == 300_000 and two["total"] == 150_000
    assert "Part 1 of 2" in one["header"]["notes"] and "Part 2 of 2, split from" in two["header"]["notes"]
    assert two["header"]["status"] == "DRAFT"
    assert fetchone(_db(), "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'PO_SPLIT'")[0] == 1


def test_a_changed_order_is_not_split(db):
    from engines.po_rules import CHANGED, get_po, save_split
    cid, po = _draft(db, [{"item_name": "Tire A", "unit": "pc", "qty": 2}, {"item_name": "Tube", "unit": "pc", "qty": 1}])
    lines = [int(x) for x in get_po(po)["lines"]["line_id"]]
    ok, msg, _ = save_split(po, [{"delivery": date.today(), "qty": {lines[0]: 2}}, {"delivery": date.today(), "qty": {lines[1]: 1}}], "stale-stamp")
    assert not ok and msg == CHANGED


def test_the_split_page_saves_and_orders_all_parts(web, db):
    _admin(web)
    from engines.po_rules import get_po
    cid, po = _draft(db, [{"item_name": "Tire A", "unit": "pc", "qty": 6}, {"item_name": "Tire B", "unit": "pc", "qty": 5}])
    page = web.get(f"/purchase-orders/{po}/edit/").content.decode()
    assert f'/purchase-orders/{po}/split/' in page and "Split into parts" in page
    page = web.get(f"/purchase-orders/{po}/split/?n=2").content.decode()
    assert "Part 1" in page and "Part 2" in page and "Save the parts" in page
    lines = {r.item_name: int(r.line_id) for r in get_po(po)["lines"].itertuples()}
    a, b = lines["Tire A"], lines["Tire B"]
    d1, d2 = date.today() + timedelta(days=5), date.today() + timedelta(days=12)
    r = web.post(f"/purchase-orders/{po}/split/", {"n": "2", "action": "save", "stamp": __import__("engines.po_rules", fromlist=["po_stamp"]).po_stamp(po),
                                                   "line": [str(a), str(b)], f"q_{a}_0": "6", f"q_{a}_1": "", f"q_{b}_0": "", f"q_{b}_1": "5",
                                                   "d_0": d1.isoformat(), "d_1": d2.isoformat()})
    assert r.status_code == 302 and "parts=" in r["Location"]
    done = web.get(r["Location"]).content.decode()
    assert "Order all parts" in done
    web.post(r["Location"], {"action": "order_all", "confirm_over": "1"})
    ids = [int(x) for x in r["Location"].split("parts=")[1].split(",")]
    assert all(get_po(i)["header"]["status"] == "ORDERED" for i in ids)
