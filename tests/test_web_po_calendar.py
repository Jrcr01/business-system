"""Purchase orders, step C (owner, 2026-10-08): "Order it" over the daily limit opens a "Confirm order anyway" dialog (no approval step); the month calendar of
committed payables, where clicking a day sets the delivery date so the order falls due that day."""
from datetime import date, timedelta

from engines.clock import manila_today
from tests.conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin
from tests.test_web_d2b import _stamp, _start, _supplier_with_price


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _order(web, po_id, **extra):
    return web.post(f"/purchase-orders/{po_id}/edit/", {"action": "order", "stamp": _stamp(po_id), "order_date": "2026-10-01", "delivery": "2026-10-08", "notes": "", **extra})


def _status(po_id):
    return fetchone(_db(), "SELECT status FROM purchase_orders WHERE po_id = ?", (po_id,))[0]


def _flags():
    return fetchone(_db(), "SELECT COUNT(*) FROM review_flags WHERE kind = 'PO_HIGH_DAY'")[0]


def test_over_the_limit_order_it_asks_first_and_confirm_orders_it_with_a_review_line(web, db):
    _admin(web)
    web.post("/settings/", {"action": "save", "key": "po_daily_payables_limit", "value": "5000"})
    po_id = _start(web, _supplier_with_price(web, db))                         # 10,000 on one day, over 5,000
    r = _order(web, po_id)
    assert r["Location"] == f"/purchase-orders/{po_id}/edit/?confirm=1"
    assert _status(po_id) == "DRAFT" and _flags() == 0                       # nothing ordered yet
    page = web.get(r["Location"]).content.decode()
    assert "Over your daily limit" in page and "Confirm order anyway" in page and "Change the date" in page
    r = _order(web, po_id, confirm_over="1")
    assert r["Location"] == f"/purchase-orders/{po_id}/" and _status(po_id) == "ORDERED" and _flags() == 1


def test_order_it_from_the_order_page_asks_first_too(web, db):
    _admin(web)
    web.post("/settings/", {"action": "save", "key": "po_daily_payables_limit", "value": "5000"})
    po_id = _start(web, _supplier_with_price(web, db))
    assert web.post(f"/purchase-orders/{po_id}/", {"action": "order"})["Location"] == f"/purchase-orders/{po_id}/edit/?confirm=1"
    assert _status(po_id) == "DRAFT"


def test_within_the_limit_no_dialog(web, db):
    _admin(web)
    web.post("/settings/", {"action": "save", "key": "po_daily_payables_limit", "value": "50000"})
    po_id = _start(web, _supplier_with_price(web, db))
    assert _order(web, po_id)["Location"] == f"/purchase-orders/{po_id}/" and _status(po_id) == "ORDERED" and _flags() == 0
    assert "Over your daily limit" not in web.get(f"/purchase-orders/{po_id}/edit/?confirm=1").content.decode()


def test_delivery_for_day_gives_that_pay_day_or_none(db):
    from engines.po_rules import delivery_for_day, expected_pay_date
    cid = make_creditor(db, "Acme Tires", terms="30")
    start = manila_today() + timedelta(days=40)
    target = start + timedelta(days=(7 - start.weekday()) % 7)                # a Monday
    d = delivery_for_day(cid, target, manila_today())
    assert d is not None and expected_pay_date(cid, d) == target
    assert delivery_for_day(cid, target + timedelta(days=5), manila_today()) is None       # a Saturday is never a pay day
    assert delivery_for_day(cid, manila_today() + timedelta(days=3), manila_today()) is None   # before the order could be paid


def test_the_month_calendar_shows_the_days_and_a_click_moves_the_order(web, db):
    from engines.po_rules import expected_pay_date
    _admin(web)
    web.post("/settings/", {"action": "save", "key": "po_daily_payables_limit", "value": "5000"})
    cid = _supplier_with_price(web, db)
    po_id = _start(web, cid)
    page = web.get(f"/purchase-orders/{po_id}/edit/").content.decode()
    assert 'class="cal"' in page and "Payables by pay day" in page and "Over limit" in page    # the month view is the first one shown
    target = manila_today() + timedelta(days=45)
    target += timedelta(days=(7 - target.weekday()) % 7)                       # a Monday, well ahead
    r = web.post(f"/purchase-orders/{po_id}/edit/", {"action": "pay_day", "stamp": _stamp(po_id), "order_date": "2026-10-01", "day": target.isoformat(),
                                                    "m": target.replace(day=1).isoformat()})
    assert r.status_code == 302
    delivery = date.fromisoformat(fetchone(_db(), "SELECT expected_delivery_date FROM purchase_orders WHERE po_id = ?", (po_id,))[0][:10])
    assert expected_pay_date(cid, delivery) == target
    month = web.get(f"/purchase-orders/{po_id}/edit/?view=month&m={target.replace(day=1).isoformat()}").content.decode()
    import re
    assert re.search(rf'value="{target.isoformat()}" class="d [a-z]* ?pick"', month)               # the new pay day is outlined


def test_the_month_calendar_adds_up_what_is_committed(db):
    from engines.po_rules import month_calendar, save_po, order_po
    from engines.settings_store import set_setting
    cid = make_creditor(db, "Acme Tires", terms="30")
    set_setting("po_daily_payables_limit", 10000, "Purchasing")
    delivery = manila_today() + timedelta(days=10)
    ok, msg, po = save_po(None, cid, manila_today(), delivery, [{"item_name": "Tires", "qty": 3, "est_unit_price": 3000}])
    assert ok and order_po(po)[0]
    from engines.po_rules import expected_pay_date
    pay = expected_pay_date(cid, delivery)
    cal = month_calendar(pay, pay)
    cell = next(c for w in cal["weeks"] for c in w if c["day"] == pay)
    assert cell["total"] == 9000.0 and cell["level"] == "near" and cell["selected"] and "Acme Tires" in cell["payees"]
    assert all(c["closed"] for w in cal["weeks"] for c in w if c["day"].weekday() >= 5)
    assert cal["weeks"][0][0]["day"].weekday() == 0 and len({len(w) for w in cal["weeks"]}) == 1


def test_new_order_lists_only_suppliers_with_a_price_list(web, db):
    _admin(web)
    priced = _supplier_with_price(web, db)
    bare = make_creditor(db, "No Prices Yet Trading")
    page = web.get("/purchase-orders/new/").content.decode()
    assert "Goodyear Philippines" in page and "No Prices Yet Trading" not in page
    r = web.post("/purchase-orders/new/", {"s": bare, "action": "start", "order_date": "2026-10-01", "delivery": "2026-10-08", "item": "Eagle F1", "qty": "1"}, follow=True)
    assert b"has no price list yet" in r.content and fetchone(_db(), "SELECT COUNT(*) FROM purchase_orders")[0] == 0
    assert _start(web, priced)
