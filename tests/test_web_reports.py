"""Reports -> Schedule of payments in Django (owner, 2026-10-08, for the co-owner): everything still owed, by pay day, filtered, with Excel and PDF of what is
shown; the totals are the same as the engine's; the page follows the person's Reports switch."""
import io
import sqlite3
from datetime import timedelta

from openpyxl import load_workbook

from conftest import make_creditor, make_statement
from engines.clock import manila_today
from tests.test_web_d0 import _admin, _sign_in_new, fetchone_uid


def _weekday(d):
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _check(db, creditor, number, d, amount, statement_id=None):
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO checks_issued (bank_account_id, creditor_id, statement_id, check_number, check_date, amount, status)
                        VALUES (1, ?, ?, ?, ?, ?, 'Active')""", (creditor, statement_id, number, str(d), amount))


def _data(db):
    sup, other = make_creditor(db, name="Goodyear Philippines"), make_creditor(db, name="Westlake Tires")
    a, b = _weekday(manila_today() + timedelta(days=3)), _weekday(manila_today() + timedelta(days=12))
    stmt = make_statement(db, sup)                                         # one batch (make_statement gives every batch the same ref)
    _check(db, sup, "4000000001", a, 1000.0, stmt)
    _check(db, sup, "4000000002", a, 250.0, stmt)
    _check(db, other, "4000000003", b, 700.0, stmt)
    return a, b


def test_the_schedule_groups_by_pay_day_and_matches_the_engine(web, db):
    from engines.reports_service import filter_schedule, get_payment_schedule_all_tracks
    _admin(web)
    a, b = _data(db)
    page = web.get(f"/reports/?from={manila_today()}&to={b}").content.decode()
    assert "Schedule of payments" in page and "Goodyear Philippines" in page and "Westlake Tires" in page
    assert "₱1,250.00" in page and "₱1,950.00" in page and "4000000001" in page                   # the day's subtotal, the total, the reference
    engine = filter_schedule(get_payment_schedule_all_tracks(), None, manila_today(), b, None)
    assert round(float(engine["amount"].sum()), 2) == 1950.0
    only = web.get(f"/reports/?from={manila_today()}&to={b}&payee=Westlake+Tires").content.decode()
    assert "Goodyear Philippines</td>" not in only and "₱700.00" in only


def test_excel_and_pdf_are_what_is_shown(web, db):
    _admin(web)
    a, b = _data(db)
    x = web.get(f"/reports/?from={manila_today()}&to={b}&kind=Supplier&download=xlsx")
    assert x.status_code == 200 and x["Content-Type"].startswith("application/vnd.openxmlformats")
    ws = load_workbook(io.BytesIO(x.content)).active
    assert [c.value for c in ws[1]] == ["Pay day", "Due date", "Pay to", "Kind", "Reference", "Batch", "Amount"] and ws.max_row == 4
    p = web.get(f"/reports/?from={manila_today()}&to={b}&download=pdf")
    assert p.status_code == 200 and p.content[:4] == b"%PDF"


def test_the_default_range_and_an_empty_one(web, db):
    _admin(web)
    _data(db)
    assert "Total owed in range" in web.get("/reports/").content.decode()            # today to the end of next month
    assert "Nothing is owed in this range." in web.get("/reports/?from=2020-01-01&to=2020-01-31").content.decode()


def test_the_reports_switch_hides_it(web, db):
    from engines.auth_service import set_restrictions
    _admin(web)
    family = _sign_in_new("family@slicks.test", "owner")
    assert family.get("/reports/").status_code == 200
    assert set_restrictions(fetchone_uid("family@slicks.test"), {"reports.use"})[0]
    page = family.get("/reports/")                                                  # owners see every module (2026-10-09): view only now
    assert page.status_code == 200 and "View only" in page.content.decode()
