"""Stage B part 3: cash in by day beside the plan's out -- firm checks and due invoices in colour, the weekday's usual takings as a labelled
estimate -- and deposits-light, where each day's takings sit beside the statement's credits and the difference only shows."""
from datetime import timedelta

from conftest import fetchone

from tests.test_web_collect import _collect
from tests.test_web_customer_checks import _pdc_sale
from tests.test_web_d0 import _admin
from tests.test_web_sell import _ready, _sale


def _db():
    import engines.db_engine as d
    return d.DB_PATH


def _n(sql, params=()):
    return fetchone(_db(), sql, params)[0]


def test_checks_and_due_invoices_land_on_their_days(web, db):
    from engines.cash_in import in_by_day
    from engines.clock import manila_today
    _admin(web)
    cust, sid, pid = _pdc_sale(web)                                             # a 10,000 PDC dated 2099-01-15
    today = manila_today()
    cin = in_by_day(today, today + timedelta(days=3))
    assert not cin                                                              # far post-dated: outside this window... nothing in these 4 days
    from datetime import date
    far = date(2099, 1, 15)
    cin = in_by_day(far - timedelta(days=1), far + timedelta(days=1))
    assert cin[far]["checks"] == 10000.0 and cin[far]["firm"] == 10000.0
    # a charge sale due in 30 days (the customer's terms)
    a2 = _n("SELECT id FROM items")
    _sale(web, a2, si="004502", qty="1", price="3000", customer="RGV Trucking", pay_method_0="CHARGE")
    due = today + timedelta(days=30)
    cin = in_by_day(today, due)
    assert cin[due]["due"] == 3000.0


def test_an_old_uncleared_check_and_an_overdue_invoice_count_now(web, db):
    from engines.cash_in import in_by_day
    from engines.clock import manila_today
    _admin(web)
    cust, sid, pid = _pdc_sale(web)
    from engines.common import get_conn
    with get_conn() as conn:
        conn.execute("UPDATE sale_payments SET check_date = '2026-01-05' WHERE payment_id = ?", (pid,))     # long past, never cleared
    today = manila_today()
    cin = in_by_day(today, today + timedelta(days=6))
    assert cin[today]["checks"] == 10000.0                                      # it can be deposited now, so it counts today


def test_the_usual_takings_are_a_weekday_average_on_future_days_only(web, db):
    from engines.cash_in import in_by_day
    from engines.clock import manila_today
    _admin(web)
    a, _ = _ready(web)
    today = manila_today()
    from engines.common import get_conn
    with get_conn() as conn:                                                     # two past same-weekday days of cash sales: 4,000 and 6,000
        for back, amount in ((7, 4000), (14, 6000)):
            d = str(today - timedelta(days=back))
            sid = conn.execute("INSERT INTO sales (sale_date, status, total, created_by) VALUES (?, 'SAVED', ?, 'x') RETURNING sale_id",
                               (d, amount)).fetchone()[0]
            conn.execute("INSERT INTO sale_payments (sale_id, method, amount) VALUES (?, 'CASH', ?)", (sid, amount))
    nxt = today + timedelta(days=7)                                             # the same weekday, next week
    cin = in_by_day(today, nxt)
    assert cin[nxt]["usual"] == 5000.0 and cin[nxt]["firm"] == 0.0              # the average, greyed, never counted as firm
    assert today not in cin or cin[today]["usual"] == 0.0                       # today is real takings, not an estimate


def test_the_calendar_shows_cash_in_only_while_stage_b_is_on(web, db, monkeypatch):
    _admin(web)
    cust, sid, pid = _pdc_sale(web)
    from engines.common import get_conn
    from engines.clock import manila_today
    with get_conn() as conn:
        conn.execute("UPDATE sale_payments SET check_date = ? WHERE payment_id = ?", (str(manila_today() + timedelta(days=2)), pid))
    page = web.get("/calendar/").content.decode()
    assert "in ₱10,000" in page.replace(",", ",")                                # the day's in-line, green
    monkeypatch.setenv("WEB_STAGE_B", "0")
    page = web.get("/calendar/").content.decode()
    assert "in ₱" not in page                                                    # hidden with the rest of stage B


def test_deposits_light_shows_each_side_and_the_difference(web, db):
    from engines.clock import manila_today
    _admin(web)
    cust, sid, pid = _pdc_sale(web)                                              # the PDC: deposit it today
    a2 = _n("SELECT id FROM items")
    _sale(web, a2, si="004502", qty="1", price="2500", pay_method_0="CASH", pay_amount_0="2500")      # cash taken today
    web.post("/collect/", {"action": "mark_check", "source": "sale", "payment_id": pid, "what": "deposited"})
    from engines.common import get_conn
    with get_conn() as conn:                                                     # the statement says 12,000 came in today
        imp = conn.execute("INSERT INTO bank_statement_imports (file_name, file_hash) VALUES ('t.xlsx', 'h1') RETURNING import_id").fetchone()[0]
        conn.execute("""INSERT INTO bank_transactions (import_id, txn_date, posting_date, credit, line_key, kind)
                        VALUES (?, ?, ?, 12000, 'k1', 'DEPOSIT')""", (imp, str(manila_today()), str(manila_today())))
    page = web.get("/collect/?view=deposits").content.decode()
    assert "2,500.00" in page and "10,000.00" in page and "12,000.00" in page
    assert "-500.00" in page                                                     # 12,000 on the statement vs 12,500 ours: shown, not policed
