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
    assert 'class="home-more"' not in page                                                          # no fold since 2026-10-19
    head = page.split('id="g-buy"')[0]
    assert "Record a sale" in head and "Sales today" in head and "New order" in head
    assert 'class="home-more"' not in web.get("/").content.decode()


def test_a_home_figure_is_kept_briefly(db, monkeypatch):
    import engines.home_tiles as ht
    monkeypatch.setenv("SLICKS_HOME_KEEP", "60")
    calls = []
    t = ht.Tile("sales", "sell", "Test", lambda: (calls.append(1) or "1", "x", "none"), key="zz-test")
    ht.evaluate(t)
    ht.evaluate(t)
    assert len(calls) == 1


def test_an_item_is_corrected_and_its_sales_take_the_new_name(web, db):
    """Owner, 2026-10-19: "no option for edit?" -- and "a link when needs a change from record the sales", in a floating panel."""
    from engines.clock import manila_today
    from engines.entry_changes import changes
    from engines.sales_rules import save_sale
    from engines.stock_rules import add_counter_item
    _admin(web)
    ok, msg, it = add_counter_item("Mags", "Rota Blak", "17", "")
    assert ok, msg
    assert save_sale(9301, manila_today(), None, "Walk-in", [{"item_id": it["id"], "qty": 1, "price": 12000, "margin": "2000"}], [], [{"method": "CASH", "amount": 12000}])[0]
    page = web.get(f"/stock/{it['id']}/?edit=1")
    assert page.status_code == 200 and "Edit this item" in page.content.decode() and page.get("X-Frame-Options") == "SAMEORIGIN"
    r = web.post(f"/stock/{it['id']}/", {"action": "edit", "brand": "Rota", "f_model": "", "f_size": "17", "unit": "set", "why": "brand misspelt"}, follow=True)
    assert "is now Rota 17" in r.content.decode()
    assert fetchone(db, "SELECT name, unit FROM items WHERE id = ?", (it["id"],)) == ("Rota 17", "set")
    assert fetchone(db, "SELECT description FROM sale_lines WHERE item_id = ?", (it["id"],))[0] == "Rota 17"
    assert changes()[-1]["kind"] == "ITEM_EDITED" and "Rota Blak" in changes()[-1]["before"]
    assert "edit item" in web.get("/sales/?view=new").content.decode() or 'id="sl_edit_dlg"' in web.get("/sales/?view=new").content.decode()


def test_purchasing_counts_a_delivery_on_its_received_date(web, db):
    """Owner, 2026-10-19: "basically should be the received date"."""
    from datetime import date
    from payables.analytics import _purchasing
    cid = make_creditor(db, "New Century", terms="30")
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO invoices (invoice_id, creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status) VALUES (901, ?, 'SI-9', '2026-09-28', '2026-10-28', 5000, 5000, 'Unpaid')", (cid,))
        conn.execute("INSERT INTO purchase_orders (po_id, po_no, creditor_id, order_date, status) VALUES (77, 'PO-0077', ?, '2026-09-20', 'RECEIVED')", (cid,))
        conn.execute("INSERT INTO po_receipts (po_id, received_date, invoice_id, actual_amount) VALUES (77, '2026-10-02', 901, 5000)")
        conn.execute("INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status) VALUES (?, 'SI-10', '2026-10-05', '2026-11-04', 700, 700, 'Unpaid')", (cid,))
    october = _purchasing(date(2026, 10, 19), date(2026, 10, 1), date(2026, 10, 31))
    september = _purchasing(date(2026, 10, 19), date(2026, 9, 1), date(2026, 9, 30))
    assert october["cards"][0][1] == 5700.0 and september["cards"][0][1] == 0.0                     # SI-9 dated 9/28, received 10/02: October
