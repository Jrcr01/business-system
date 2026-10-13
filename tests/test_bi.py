"""Business intelligence on Analytics (owner, 2026-10-14: "our focus are now sales and liquidity"): the Sales tab (what happened, against the period
before) and the Cash ahead tab (the bank balance 60 days on), every figure from the documents."""
import sqlite3
from datetime import timedelta

from conftest import fetchone

from tests.test_web_d0 import _admin
from tests.test_web_sell import _ready, _sale


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _today():
    from engines.clock import manila_today
    return manila_today()


def test_sales_figures_match_the_documents_and_the_period_before(web, db):
    from engines.bi_service import sales
    _admin(web)
    a, _name = _ready(web, qty=20)
    today = _today()
    assert _sale(web, a, si="004501", qty="2", price="5000").status_code == 302                       # 10,000 today, cost 2,840 each
    web.post("/customers/", {"action": "save", "name": "RGV Trucking", "type": "FLEET", "terms": "30"})
    assert _sale(web, a, si="004502", qty="1", price="4000", pay_method_0="CHARGE", customer="RGV Trucking").status_code == 302
    from engines.bi_service import shift_months
    last = shift_months(today, 1)
    with sqlite3.connect(_db()) as conn:                                                                 # one sale on the same day a month back, by hand
        conn.execute("INSERT INTO sales (si_no, sale_date, status, total, cost_total) VALUES (4400, ?, 'SAVED', 6000, 2840)", (last.isoformat(),))
        sid = conn.execute("SELECT sale_id FROM sales WHERE si_no = 4400").fetchone()[0]
        conn.execute("INSERT INTO sale_lines (sale_id, item_id, description, qty, unit_price, amount, unit_cost) VALUES (?, ?, 'x', 1, 6000, 6000, 2840)", (sid, a))
        conn.execute("INSERT INTO sale_payments (sale_id, method, amount) VALUES (?, 'CASH', 6000)", (sid,))
        conn.execute("INSERT INTO sales (si_no, sale_date, status, total, cost_total) VALUES (4401, ?, 'VOID', 9999, 1)", (today.isoformat(),))   # never counts
    r = sales(today, today.replace(day=1), today, "month")
    assert r["now"]["sales"] == 14000.0 and r["now"]["cogs"] == 8520.0 and r["now"]["margin"] == 5480.0 and r["now"]["pieces"] == 3 and r["now"]["invoices"] == 2
    assert r["now"]["average"] == 7000.0 and r["now"]["margin_pct"] == 39.1
    assert r["before"]["sales"] == 6000.0 and r["delta"]["sales"] == 133.3                             # the same days a month back hold the 6,000 sale
    assert r["by_method"] == [("Cash", 10000.0), ("On credit (charge)", 4000.0)]
    assert r["by_type"] == [("Walk-in", 10000.0), ("Fleet", 4000.0)] and r["by_size"][0][1] == 3 and r["by_brand"][0]["amount"] == 14000.0
    assert sum(m["sales"] for m in r["by_month"]) == 20000.0 and len(r["by_month"]) == 12
    assert r["by_day"]["a"] and r["by_day"]["last"]["value"] == 14000.0


def test_the_sales_tab_draws_and_hides_with_the_sales_page(web, db):
    from tests.test_web_d0 import _sign_in_new, fetchone_uid
    from engines.auth_service import set_access
    _admin(web)
    a, _ = _ready(web)
    _sale(web, a, si="004501", qty="2", price="5000")
    page = web.get("/analytics/?tab=sales").content.decode()
    assert "Sales by day" in page and "10,000" in page and "This period against the others" in page and "bi-line" in page
    page = web.get("/analytics/?tab=sales&period=custom&from=2026-01-01&to=2026-01-31").content.decode()
    assert "01/01/2026 → 01/31/2026" in page and "No sales in the period yet" in page
    assert web.get("/").content.decode().count("Cash ahead") >= 1
    other = _sign_in_new("ana@slicks.test", "owner")
    set_access(fetchone_uid("ana@slicks.test"), {"sales"}, set())
    page = other.get("/analytics/").content.decode()
    assert "?tab=sales" not in page and "?tab=cash" in page


def test_cash_ahead_starts_from_the_bank_and_finds_the_lowest_day(web, db):
    from engines.bi_service import liquidity
    from conftest import make_creditor
    from tests.test_web_d3a import _add_inv, _new_batch
    _admin(web)
    assert liquidity(_today())["none"]
    assert "No bank statement is imported yet" in web.get("/analytics/?tab=cash").content.decode()
    today = _today()
    cid = make_creditor(db, "Sunny Tire Supply", terms="30")
    sid = _new_batch(web, cid)
    _add_inv(web, sid, doc="INV-1", gross="300,000")
    web.post(f"/order-statements/{sid}/", {"action": "chk_park", "d1": (today + timedelta(days=10)).isoformat(), "a1": "300,000"})
    with sqlite3.connect(_db()) as conn:
        imp = conn.execute("INSERT INTO bank_statement_imports (bank_account_id, file_name, file_hash) VALUES (1, 's.csv', 'h')").lastrowid
        conn.execute("""INSERT INTO bank_transactions (import_id, txn_date, posting_date, description, debit, credit, kind, line_key, balance)
                        VALUES (?, ?, ?, 'OPENING', 0, 0, 'OTHER', 'k0', 500000)""", (imp, today.isoformat(), today.isoformat()))
    q = liquidity(today)
    assert not q["none"] and q["opening"] == 500000.0 and q["money_out"] == 300000.0 and q["lowest"] == 200000.0
    assert q["lowest_day"] >= today + timedelta(days=10) and q["heavy"][0]["amount"] == 300000.0 and "Sunny Tire Supply" in q["heavy"][0]["what"]
    page = web.get("/analytics/?tab=cash").content.decode()
    assert "Bank balance ahead" in page and "500,000" in page and "200,000" in page and "The heavy days" in page
