"""The 10/19 review (owner: "controls for error ... as well as easiness of using the system"): odd supplier invoices asked again, an order quantity far
above normal noted, the cash count at closing, the family's short Home, Home figures kept briefly."""
import sqlite3

from conftest import fetchone, make_creditor
from tests.test_web_d0 import _admin, _sign_in_new



def test_odd_invoice_warnings(db):
    from datetime import timedelta
    from engines.clock import manila_today
    from engines.duplicate_service import invoice_warnings
    cid = make_creditor(db, "Sunny Tire Supply", terms="30")
    today = manila_today()
    with sqlite3.connect(db) as conn:
        for n, amt in enumerate((10000, 12000, 15000), start=1):
            conn.execute("INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status) VALUES (?, ?, ?, ?, ?, ?, 'Unpaid')",
                         (cid, f"SI-{n}", str(today), str(today), amt, amt))
    assert invoice_warnings(cid, [("SI-9", today, 14000)]) == []
    assert any("after today" in w for w in invoice_warnings(cid, [("SI-9", today + timedelta(days=5), 14000)]))
    assert any("typo in the year" in w for w in invoice_warnings(cid, [("SI-9", today - timedelta(days=120), 14000)]))
    assert any("extra zero" in w for w in invoice_warnings(cid, [("SI-9", today, 150000)]))


def test_the_cash_count_compares_the_drawer_with_the_cash_sales(web, db):
    from engines.cash_count import get_count, save_count
    from engines.clock import manila_today
    from engines.sales_rules import save_sale
    from tests.test_web_stock import _item, _stock
    _admin(web)
    a, _ = _item("Sunny", "185/55R16", "NP226")
    _stock(a, 5, 2000.0)
    assert save_sale(9201, manila_today(), None, "Walk-in", [{"item_id": a, "qty": 1, "price": 2600}], [], [{"method": "CASH", "amount": 2600}])[0]
    ok, msg = save_count(manila_today(), "3,600", "1,000")
    assert ok and "matches" in msg and get_count(manila_today())["diff"] == 0
    ok, msg = save_count(manila_today(), "3,000", "1,000", "")
    assert ok and "short" in msg and get_count(manila_today())["diff"] == -600
    assert fetchone(db, "SELECT COUNT(*) FROM review_flags WHERE kind = 'CASH_COUNT_DIFF'")[0] == 1
    from tests.test_phone import _on
    from tests.test_phone_approval import PHONE_UA
    _on()
    assert "Cash count at closing" in web.get("/phone/day/", HTTP_USER_AGENT=PHONE_UA, follow=True).content.decode()


def test_the_family_home_shows_the_daily_six_first_and_folds_the_rest(web, db):
    _admin(web)
    owner = _sign_in_new("owner@slicks.test", "owner")
    page = owner.get("/").content.decode()
    assert "Show everything" in page and 'class="home-more"' in page
    head = page.split("Show everything")[0]
    assert "Record a sale" in head and "Sales today" in head and "New order" in head
    assert "Show everything" not in web.get("/").content.decode()                                     # the System admin keeps the full Home


def test_a_home_figure_is_kept_briefly(db, monkeypatch):
    import engines.home_tiles as ht
    monkeypatch.setattr(ht, "KEEP_SECONDS", 60)
    calls = []
    t = ht.Tile("sales", "sell", "Test", lambda: (calls.append(1) or "1", "x", "none"), key="zz-test")
    ht.evaluate(t)
    ht.evaluate(t)
    assert len(calls) == 1
