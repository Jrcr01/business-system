"""Room for new orders (owner, 2026-10-15: "the logic of this PO is to monitor orders because this is the variable payable"): per week, expected cash
in less the fixed payables less the orders already placed; days beyond the order window are shown, not judged; card reserves stop there."""
import sqlite3
from datetime import timedelta

from conftest import make_creditor
from tests.test_web_d0 import _admin
from tests.test_web_sell import _ready, _sale


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def test_room_is_cash_in_less_fixed_less_orders_and_the_order_is_judged_against_it(web, db):
    from engines.acting_user import acting_as
    from engines.check_rules import issue_check
    from engines.clock import manila_today
    from engines.order_room import weeks
    from engines.loan_schedule_service import create_note_with_payments
    _admin(web)
    today = manila_today()
    assert not weeks()["known"]                                                     # no sales yet: room is not shown
    a, _ = _ready(web)
    _sale(web, a, si="004501", qty="2", price="5000")                              # one day of sales: still not enough history
    assert not weeks()["known"]
    from engines import settings_registry as sr
    from engines.acting_user import acting_as
    with acting_as("admin@slicks.test", "admin"):
        sr.save("po_cash_in_week", "250000")                                       # the typed weekly figure stands in
    assert weeks()["known"] == "typed" and weeks()["weeks"][0]["cash_in"] == 250000.0
    lender = make_creditor(db, "Luz", creditor_type="LENDER")
    from engines.po_rules import _blocked_days
    pay = today + timedelta(days=21)
    pay += timedelta(days=(7 - pay.weekday()) % 7)                                  # a Monday, not a holiday: the check stays in its own week
    while pay.isoformat() in {str(x)[:10] for x in _blocked_days()}:
        pay += timedelta(days=7)
    with acting_as("admin@slicks.test", "admin"):
        ok, msg, lnid = create_note_with_payments(lender, today, 300000.0, 0.0, [(pay, 300000.0)])
        assert ok, msg
        inst = sqlite3.connect(_db()).execute("SELECT installment_id FROM loan_installments WHERE loan_note_id = ?", (lnid,)).fetchone()[0]
        ok, msg, chk = issue_check(lender, 300000.0, pay, loan_note_id=lnid, installment_id=inst)
        assert ok, msg
    r = weeks(order_pay_day=pay, order_amount=50000.0)
    assert r["known"] and r["window_end"] is not None
    wk = next(w for w in r["weeks"] if w["selected"])
    assert wk["fixed"] == 300000.0 and wk["orders"] == 0.0 and wk["room"] == round(wk["cash_in"] - 300000.0, 2)
    assert wk["level"] == ("over" if wk["room"] < 0 else ("tight" if 50000.0 > wk["room"] else "ok"))
    from tests.test_web_d2b import _supplier_with_price
    supplier = _supplier_with_price(web, db, "Sunny Tire Supply")
    page = web.get(f"/purchase-orders/new/?s={supplier}&order_date={today}&delivery={today + timedelta(days=7)}").content.decode()
    assert "Room for new orders" in page and "roomstrip" in page


def test_days_beyond_the_window_are_shown_not_judged_and_get_no_card_reserve(web, db):
    from engines import settings_registry as sr
    from engines.acting_user import acting_as
    from engines.clock import manila_today
    from engines.day_cap import card_reserve_rows, judged, level
    from engines.order_room import window_end
    _admin(web)
    today = manila_today()
    far = window_end() + timedelta(days=30)
    assert judged(today + timedelta(days=30)) and not judged(far)
    assert level(900000.0, today + timedelta(days=30)) == "over" and level(900000.0, far) == ""
    bank = make_creditor(db, "RCBC", creditor_type="CREDIT_CARD")
    with sqlite3.connect(_db()) as conn:
        card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Platinum', '5003', 1)", (bank,)).lastrowid
        for back, amt in ((3, 80_000), (2, 100_000), (1, 120_000)):
            m = (today.replace(day=1) - timedelta(days=28 * back)).replace(day=15)
            conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, ?, 'Unpaid')", (card, m.isoformat(), amt))
    rows = card_reserve_rows(today, today + timedelta(days=400))
    assert not rows.empty and (rows["day"] <= window_end()).all()                  # reserves stop at the window
    from engines.po_rules import month_calendar
    cal = month_calendar(far.replace(day=1))
    cells = [c for w in cal["weeks"] for c in w if c["in_month"]]
    assert all(not c["judged"] and c["level"] in ("later", "") for c in cells) and cal["window_end"] == window_end()
    with acting_as("admin@slicks.test", "admin"):
        sr.save("po_window_months", "0")                                           # 0 = every day judged, as before
    assert judged(far) and window_end() is None
    cal = month_calendar(far.replace(day=1))
    assert all(c["judged"] for w in cal["weeks"] for c in w)
