"""The 10/19 review (owner: "controls for error ... as well as easiness of using the system"): odd supplier invoices asked again, an order quantity far
above normal noted, the family's short Home, Home figures kept briefly."""
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


def test_the_family_home_shows_the_daily_six_first_and_folds_the_rest(web, db):
    _admin(web)
    owner = _sign_in_new("owner@slicks.test", "owner")
    page = owner.get("/").content.decode()
    assert "Show everything" in page and 'class="home-more"' in page
    head = page.split("Show everything")[0]
    assert "Record a sale" in head and "Sales today" in head and "New order" in head
    assert 'class="home-more"' not in web.get("/").content.decode()                                # the System admin keeps the full Home


def test_a_home_figure_is_kept_briefly(db, monkeypatch):
    import engines.home_tiles as ht
    monkeypatch.setenv("SLICKS_HOME_KEEP", "60")
    calls = []
    t = ht.Tile("sales", "sell", "Test", lambda: (calls.append(1) or "1", "x", "none"), key="zz-test")
    ht.evaluate(t)
    ht.evaluate(t)
    assert len(calls) == 1
