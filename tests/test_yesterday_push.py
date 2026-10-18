"""The morning "Yesterday" phone notification (owner, 2026-10-19: "me and owners, sales and purchases, send no sales and purchases too"; "our shop is
always open. remind my family to input as soon as possible"; "not 2 pm but after working hours")."""
from datetime import datetime, timedelta

from tests.test_web_d0 import _admin, _sign_in_new


def _sent(monkeypatch):
    out = []
    import engines.web_push as wp
    monkeypatch.setattr(wp, "send", lambda email, title, body, url, **k: out.append((email, title, body, url)) or 1)
    return out


def test_a_day_with_sales_is_reported_to_the_admin_and_the_owners_once(web, db, monkeypatch):
    from engines import yesterday_push as yp
    from engines.clock import manila_today
    from engines.sales_rules import save_sale
    from tests.test_web_stock import _item, _stock
    _admin(web)
    _sign_in_new("owner@slicks.test", "owner")
    a, _ = _item("Sunny", "185/55R16", "NP226")
    _stock(a, 5, 2000.0)
    y = manila_today() - timedelta(days=1)
    assert save_sale(9401, y, None, "Walk-in", [{"item_id": a, "qty": 2, "price": 2600}], [], [{"method": "CASH", "amount": 5200}])[0]
    out = _sent(monkeypatch)
    now = datetime.combine(manila_today(), datetime.min.time()).replace(hour=8)
    assert "sent to 2" in yp.run(now)
    assert out[0][1].startswith("Yesterday ·") and "1 invoice · ₱5,200 · profit ₱1,200" in out[0][2] and "cash ₱5,200" in out[0][2]
    assert "No purchases received yesterday." in out[0][2] and out[0][3] == f"/phone/day/?d={y.isoformat()}"
    assert "sent to 0" in yp.run(now)                                                                  # never twice
    assert yp.run(now.replace(hour=18)) == "Yesterday has sales: no reminder."


def test_no_sales_asks_to_type_them_and_reminds_after_working_hours(web, db, monkeypatch):
    from engines import yesterday_push as yp
    from engines.clock import manila_today
    _admin(web)
    out = _sent(monkeypatch)
    now = datetime.combine(manila_today(), datetime.min.time()).replace(hour=8)
    yp.run(now)
    assert "not typed in yet" in out[0][1] and "as soon as possible" in out[0][2]
    yp.run(now.replace(hour=18))
    assert out[-1][1].startswith("Reminder: yesterday's sales")
    assert yp.run(now.replace(hour=11)) == "Not a Yesterday hour."
    ok, _ = yp.set_pref("admin@slicks.test", False)
    assert ok and not yp.people()[0]["receives"]
    assert "Yesterday on the phone" in web.get("/settings/notifications/").content.decode()


def test_the_payment_statement_picks_a_check_date_from_the_calendar_in_a_panel(web, db):
    """Owner, 2026-10-19: "i have to go back and forth between PS and Calendar"."""
    from conftest import make_creditor
    from tests.test_web_d3a import _add_inv, _new_batch
    _admin(web)
    sid = _new_batch(web, make_creditor(db, "CSS Autoparts Inc.", terms="30"))
    _add_inv(web, sid, doc="110744", gross="6,000")
    page = web.get(f"/order-statements/{sid}/?tab=checks").content.decode()
    assert 'id="cal_pick_dlg"' in page
    cal = web.get("/calendar/?pick=1&m=2026-11&day=2026-11-20")
    assert cal.status_code == 200 and cal.get("X-Frame-Options") == "SAMEORIGIN"
    body = cal.content.decode()
    assert 'data-day="2026-11-20"' in body and "Use 11/20/2026 for the check" in body and "pick=1" in body
    assert "Use " not in web.get("/calendar/?m=2026-11&day=2026-11-20").content.decode().split('id="day"')[0][-200:]
