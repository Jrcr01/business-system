"""Purchase orders, step C (owner, 2026-10-08): over the daily limit a dialog asks first (no approval step); the month calendar of committed payables,
where clicking a day sets the delivery date so the order falls due that day. Owner, 2026-10-11: the dialog shows when the draft is SENT to the supplier
(the earliest point to change the date) and again when the supplier confirms; the Review line is written only when it becomes a purchase order."""
from datetime import date, timedelta

from engines.clock import manila_today
from tests.conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin
from tests.test_web_d2b import _stamp, _start, _supplier_with_price


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _send(web, po_id, **extra):
    return web.post(f"/purchase-orders/{po_id}/edit/", {"action": "request", "stamp": _stamp(po_id), "order_date": "2026-10-01", "delivery": "2026-10-08", "notes": "", **extra})


def _confirm(web, po_id, **extra):
    return web.post(f"/purchase-orders/{po_id}/confirm/", {"stamp": _stamp(po_id), "delivery": "2026-10-08", **extra})


def _sent(po_id):
    return fetchone(_db(), "SELECT requested_at IS NOT NULL FROM purchase_orders WHERE po_id = ?", (po_id,))[0] == 1


def _status(po_id):
    return fetchone(_db(), "SELECT status FROM purchase_orders WHERE po_id = ?", (po_id,))[0]


def _flags():
    return fetchone(_db(), "SELECT COUNT(*) FROM review_flags WHERE kind = 'PO_HIGH_DAY'")[0]


def test_over_the_limit_order_it_asks_first_and_confirm_orders_it_with_a_review_line(web, db):
    _admin(web)
    web.post("/settings/", {"action": "save", "key": "po_daily_payables_limit", "value": "5000"})
    po_id = _start(web, _supplier_with_price(web, db))                         # 10,000 on one day, over 5,000
    r = _send(web, po_id)
    assert r["Location"] == f"/purchase-orders/{po_id}/edit/?confirm=1"
    assert _status(po_id) == "DRAFT" and not _sent(po_id) and _flags() == 0  # nothing sent yet
    page = web.get(r["Location"]).content.decode()
    assert "Over your daily limit" in page and "Send to supplier anyway" in page and "Change the date" in page
    r = _send(web, po_id, confirm_over="1")
    assert r["Location"] == f"/purchase-orders/{po_id}/" and _status(po_id) == "DRAFT" and _sent(po_id) and _flags() == 0   # a request: no review line yet
    page = _confirm(web, po_id).content.decode()                              # the supplier confirmed: asked once more
    assert "Over your daily limit" in page and "Confirm order anyway" in page and _status(po_id) == "DRAFT"
    r = _confirm(web, po_id, confirm_over="1")
    assert r["Location"] == f"/purchase-orders/{po_id}/" and _status(po_id) == "ORDERED" and _flags() == 1


def test_order_it_from_the_order_page_asks_first_too(web, db):
    _admin(web)
    web.post("/settings/", {"action": "save", "key": "po_daily_payables_limit", "value": "5000"})
    po_id = _start(web, _supplier_with_price(web, db))
    assert web.post(f"/purchase-orders/{po_id}/", {"action": "request"})["Location"] == f"/purchase-orders/{po_id}/edit/?confirm=1"
    assert _status(po_id) == "DRAFT" and not _sent(po_id)


def test_within_the_limit_no_dialog(web, db):
    _admin(web)
    web.post("/settings/", {"action": "save", "key": "po_daily_payables_limit", "value": "50000"})
    po_id = _start(web, _supplier_with_price(web, db))
    assert _send(web, po_id)["Location"] == f"/purchase-orders/{po_id}/" and _sent(po_id)
    assert _confirm(web, po_id)["Location"] == f"/purchase-orders/{po_id}/" and _status(po_id) == "ORDERED" and _flags() == 0
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
    from engines.po_rules import _blocked_days
    while target.isoformat() in {str(d)[:10] for d in _blocked_days()}:           # ... not a holiday (11/30/2026 is Bonifacio Day)
        target += timedelta(days=7)
    r = web.post(f"/purchase-orders/{po_id}/edit/", {"action": "pay_day", "stamp": _stamp(po_id), "order_date": manila_today().isoformat(), "day": target.isoformat(),
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


def test_the_clicked_day_alone_with_estimates_apart_from_what_is_committed(web, db):
    # owner, 2026-10-12: "keep the breakdown limited to the day clicked and removed daily and weekly view" + "seperate the estimate and actually comitted"
    from engines.po_rules import day_schedule, expected_pay_date, is_estimate, month_calendar, order_po, save_po
    cid = make_creditor(db, "Acme Tires", terms="30")
    delivery = manila_today() + timedelta(days=10)
    ok, msg, po = save_po(None, cid, manila_today(), delivery, [{"item_name": "Tires", "qty": 3, "est_unit_price": 3000}])
    assert ok and order_po(po)[0]                                               # an order not received yet: an estimate (9,000)
    pay = expected_pay_date(cid, delivery)
    import sqlite3
    with sqlite3.connect(_db()) as conn:                                        # and a recorded SI due the same day, no check yet: committed (4,000)
        sid = conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, 'OS-T1', 0, 'Draft') RETURNING statement_id",
                           (cid,)).fetchone()[0]
        conn.execute("""INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, trade_discounts, amount, status, statement_id)
                        VALUES (?, 'SI-77', ?, ?, 4000, '[]', 4000, 'Unpaid', ?)""", (cid, str(manila_today()), str(pay), sid))
    assert is_estimate("Purchase orders", "") and is_estimate("Credit cards", "Card reserve (estimate) · Gold") and not is_estimate("Supplier checks", "SI-77")
    day = day_schedule(pay)
    assert day["estimate_total"] == 9000.0 and day["committed_total"] == 4000.0 and day["total"] == 13000.0
    assert [r["kind"] for r in day["estimates"]] == ["Purchase orders"] and len(day["committed"]) == 1
    cell = next(c for w in month_calendar(pay, pay)["weeks"] for c in w if c["day"] == pay)
    assert cell["total"] == 13000.0 and cell["estimate"] == 9000.0 and cell["committed"] == 4000.0
    _admin(web)
    po_id = _start(web, _supplier_with_price(web, db))                          # a draft order's page carries the calendar
    page = web.get(f"/purchase-orders/{po_id}/edit/").content.decode()
    assert "Payables by pay day" in page and "Actually committed" in page and "Estimates" in page
    assert ">Daily<" not in page and ">Weekly<" not in page and "The week of" not in page


def test_the_list_under_the_calendar_follows_the_day_clicked(web, db):
    # owner, 2026-10-12: "when i click day from the calendar. committed and estimate are not changing" -- a day the order cannot fall due on
    # (before order date + terms) left the list on the order's own pay day
    from datetime import date
    from engines.po_rules import day_schedule
    from web.buying.orders import _calendar_ctx

    class R:
        slicks_user = {"role": "admin"}

        def __init__(self, **get):
            self.GET = get
    from tests.test_web_d0 import _admin
    from tests.test_web_d2b import _supplier_with_price
    _admin(web)
    cid = _supplier_with_price(web, db)
    ctx = _calendar_ctx(R(), cid, date(2026, 11, 2), 0.0, None)
    assert ctx["daysched"]["day"] == ctx["pay"] and ctx["looked_is_pay"]
    other = date(2026, 10, 14)
    ctx = _calendar_ctx(R(day=other.isoformat()), cid, date(2026, 11, 2), 0.0, None)
    assert ctx["daysched"]["day"] == other and not ctx["looked_is_pay"] and ctx["daysched"]["total"] == day_schedule(other)["total"]
    assert _calendar_ctx(R(look=other.isoformat()), cid, date(2026, 11, 2), 0.0, None)["daysched"]["day"] == other
