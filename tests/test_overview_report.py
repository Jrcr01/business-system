"""engines/overview_report_service.py -- period maths, paid vs scheduled, comparison with the previous period."""
import sqlite3
from datetime import date, timedelta

from conftest import make_creditor
from engines.overview_pdf_service import generate_overview_pdf
from engines.overview_report_service import get_overview, period_bounds, previous_bounds


def test_period_bounds():
    assert period_bounds("Month", date(2026, 9, 21))[:2] == (date(2026, 9, 1), date(2026, 9, 30))
    assert period_bounds("Week", date(2026, 9, 21))[:2] == (date(2026, 9, 21), date(2026, 9, 27))       # Monday-Sunday
    assert period_bounds("Quarter", date(2026, 11, 5))[:2] == (date(2026, 10, 1), date(2026, 12, 31))
    assert period_bounds("Year", date(2026, 3, 3))[:2] == (date(2026, 1, 1), date(2026, 12, 31))
    assert previous_bounds("Month", date(2026, 1, 1))[:2] == (date(2025, 12, 1), date(2025, 12, 31))   # rolls back over the year


def _check(db, creditor, amount, on, statement=True):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status, bank_cleared_date) "
                 "VALUES (1, 1, ?, ?, ?, ?, 'Active', ?)",                  # a past-dated check here is one the bank cleared (paid = cleared, 2026-10-08)
                 (creditor, f"39900{abs(hash((amount, str(on)))) % 100000:05d}", amount, str(on), str(on) if on < date.today() else None))
    conn.commit(); conn.close()


def test_overview_splits_paid_from_scheduled_and_compares_with_previous_period(db):
    cid = make_creditor(db, name="Acom Trading", creditor_type="SUPPLIER")
    today = date.today()
    first_of_month = today.replace(day=1)
    last_month_day = first_of_month - timedelta(days=5)
    _check(db, cid, 1000, first_of_month)                       # this month, date arrived (or is today)
    _check(db, cid, 500, last_month_day)                        # previous month
    future = today + timedelta(days=2)
    if future.month == today.month:
        _check(db, cid, 250, future)                            # this month but not yet due

    o = get_overview("Month", today)

    on_the_first = first_of_month == today                      # due today = still owed (owner, 2026-09-28): on the 1st that check is scheduled, not paid
    assert o["current"]["paid"] == (0 if on_the_first else 1000)
    assert o["previous"]["paid"] == 500
    assert o["current"]["scheduled"] == (1000 if on_the_first else 0) + (250 if future.month == today.month else 0)
    assert any("Paid out" in h for h in o["highlights"])
    assert o["current"]["payees"].iloc[0]["payee"] == "Acom Trading"


def test_overview_pdf_builds_in_both_versions(db):
    cid = make_creditor(db, creditor_type="SUPPLIER")
    _check(db, cid, 100, date.today())
    o = get_overview("Week", date.today())
    assert generate_overview_pdf(o, short=True)[:4] == b"%PDF"
    assert generate_overview_pdf(o, short=False)[:4] == b"%PDF"


def test_payment_schedule_is_sorted_by_track_then_amount_within_each_day(db):
    from engines.reports_service import get_payment_schedule_all_tracks
    lender = make_creditor(db, name="Ramon", creditor_type="LENDER")
    supplier = make_creditor(db, name="Acom", creditor_type="SUPPLIER")
    day = date.today() + timedelta(days=3)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    conn = sqlite3.connect(db)
    ln = conn.execute("INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, monthly_interest_rate, total_interest, total_payable, status) VALUES (?, 'LN-S', '2026-01-01', 1, 1, 0, 1, 'Active')", (lender,)).lastrowid
    for cid, amount, tag, note in ((supplier, 900, "S2", None), (supplier, 100, "S1", None), (lender, 500, "L1", ln), (lender, 50, "L0", ln)):
        conn.execute("INSERT INTO checks_issued (bank_account_id, statement_id, loan_note_id, creditor_id, check_number, amount, check_date, status) VALUES (1, ?, ?, ?, ?, ?, ?, 'Active')",
                     (None if note else 1, note, cid, tag, amount, str(day)))
    conn.commit(); conn.close()

    df = get_payment_schedule_all_tracks()

    assert list(df["reference"]) == ["L0", "L1", "S1", "S2"]      # lender first, then supplier; smallest amount first inside each


def test_startup_check_reports_a_missing_column_and_is_quiet_when_current(db, tmp_path):
    import shutil
    from engines.schema_check import pending_schema_updates
    assert pending_schema_updates(db) == []
    old = str(tmp_path / "old.db")
    shutil.copy(db, old)
    conn = sqlite3.connect(old)
    conn.execute("ALTER TABLE cc_statements RENAME TO cc_statements_tmp")
    conn.execute("CREATE TABLE cc_statements AS SELECT cc_statement_id, card_id, due_date, total_amount_due FROM cc_statements_tmp")
    conn.commit(); conn.close()
    assert any("cc_statements.paid_confirmed" in m for m in pending_schema_updates(old))
