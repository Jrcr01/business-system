"""The payment calendar shows past days as PAID instead of 0 (owner, 2026-09-30). The "still owed" schedule (Schedule of payments, forecasts) is unchanged:
only the calendar source reaches back, with the same four tracks. Past days = what fell due then (checks dated that day, bank-loan installments, card
statements); today and later = still owed."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import make_creditor, make_statement
from engines.bank_loan_service import add_bank_loan_installment, create_bank_loan
from engines.reports_service import (
    get_payment_calendar_daily_totals, get_payment_calendar_items, get_payment_schedule_all_tracks, get_payment_schedule_daily_totals, next_banking_day,
)


def _weekday(d: date, back: bool = True) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=-1 if back else 1)
    return d


def _check(db, creditor, number, d, amount, statement_id=None, loan_note_id=None, status="Active", settled=False, cleared=None):
    """cleared: the bank cleared it on its date (default: when it is dated before today) -- paid = cleared (2026-10-08)."""
    cleared = (d < date.today() and not settled) if cleared is None else cleared
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO checks_issued (bank_account_id, creditor_id, statement_id, loan_note_id, check_number, check_date, amount, status, settled_outside_at,
                                                   bank_cleared_date) VALUES (1, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                     (creditor, statement_id, loan_note_id, number, str(d), amount, status, "2026-09-30" if settled else None, str(d) if cleared else None))


def _row(df, d):
    r = df[df["actual_payment_date"] == d]
    return None if r.empty else r.iloc[0]


def test_a_past_day_shows_what_was_paid_and_a_future_day_what_is_due(db):
    sup = make_creditor(db, name="Sup")
    stmt = make_statement(db, sup)
    past = _weekday(date.today() - timedelta(days=10))
    future = _weekday(date.today() + timedelta(days=10), back=False)
    _check(db, sup, "3992000001", past, 1000.0, statement_id=stmt)
    _check(db, sup, "3992000002", past, 500.0, statement_id=stmt)
    _check(db, sup, "3992000003", future, 700.0, statement_id=stmt)

    totals = get_payment_calendar_daily_totals()

    p, f = _row(totals, past), _row(totals, future)
    assert (p["paid_amount"], p["paid_items"], p["due_amount"], p["due_items"]) == (1500.0, 2, 0.0, 0)
    assert (f["due_amount"], f["due_items"], f["paid_amount"], f["paid_items"]) == (700.0, 1, 0.0, 0)


def test_cancelled_replaced_and_settled_outside_checks_are_not_counted_as_paid_on_a_past_day(db):
    sup = make_creditor(db, name="Sup")
    stmt = make_statement(db, sup)
    past = _weekday(date.today() - timedelta(days=12))
    _check(db, sup, "3992000011", past, 100.0, statement_id=stmt, status="Cancelled")
    _check(db, sup, "3992000012", past, 200.0, statement_id=stmt, status="Replaced")
    _check(db, sup, "3992000013", past, 300.0, statement_id=stmt, settled=True)
    _check(db, sup, "3992000014", past, 400.0, statement_id=stmt)

    assert _row(get_payment_calendar_daily_totals(), past)["paid_amount"] == 400.0


def test_lender_bank_loan_and_card_dues_of_past_days_are_included(db):
    lender = make_creditor(db, name="Lender", creditor_type="LENDER")
    ok, _, lnid = app.create_loan_note(lender, date(2026, 1, 5), 100000.0, 0.0)
    past = _weekday(date.today() - timedelta(days=15))
    _check(db, lender, "3992000021", past, 5000.0, loan_note_id=lnid)
    bank = make_creditor(db, name="BDO", creditor_type="BANK_LOAN")
    blid = create_bank_loan(bank, "L1", "A", date(2026, 1, 1), date(2027, 1, 1), None, 500000.0, "ADA")
    add_bank_loan_installment(blid, past, 20000.0)
    with sqlite3.connect(db) as conn:                          # paid = ticked (or on the bank statement), not its date (2026-10-09)
        conn.execute("UPDATE bank_loan_installments SET cleared_at = ? WHERE bank_loan_id = ?", (str(past), blid))
    issuer = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    with sqlite3.connect(db) as conn:
        card = conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '1234', 1)", (issuer,)).lastrowid
        conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, 3000, 'Paid')", (card, str(past)))

    items = get_payment_calendar_items()
    day = items[items["actual_payment_date"] == past]

    assert set(day["track"]) == {"Lender", "Bank Loan", "Credit Card"}
    assert day["amount"].sum() == 28000.0 and set(day["state"]) == {"Paid"}


def test_the_still_owed_schedule_does_not_reach_back(db):
    sup = make_creditor(db, name="Sup")
    stmt = make_statement(db, sup)
    past = _weekday(date.today() - timedelta(days=10))
    future = _weekday(date.today() + timedelta(days=10), back=False)
    _check(db, sup, "3992000031", past, 1000.0, statement_id=stmt)                       # cleared by the bank: paid
    _check(db, sup, "3992000032", future, 700.0, statement_id=stmt)

    assert list(get_payment_schedule_all_tracks()["amount"]) == [700.0]
    assert list(get_payment_schedule_daily_totals()["total_amount"]) == [700.0]


def test_a_past_dated_check_not_cleared_yet_is_still_owed_today(db):
    """The clearing rule (owner, 2026-10-08): the supplier can deposit it any day, so it is owed from today until the bank clears it."""
    sup = make_creditor(db, name="Sup")
    stmt = make_statement(db, sup)
    past = _weekday(date.today() - timedelta(days=10))
    _check(db, sup, "3992000051", past, 1000.0, statement_id=stmt, cleared=False)
    sched = get_payment_schedule_all_tracks()
    assert list(sched["amount"]) == [1000.0] and sched["actual_payment_date"].iloc[0] == next_banking_day(date.today())
    assert _row(get_payment_calendar_daily_totals(), past) is None                            # not shown as paid on its own date


def test_a_check_dated_today_is_still_due_and_an_empty_database_gives_empty_frames(db):
    assert get_payment_calendar_daily_totals().empty and get_payment_calendar_items().empty
    sup = make_creditor(db, name="Sup")
    stmt = make_statement(db, sup)
    _check(db, sup, "3992000041", date.today(), 900.0, statement_id=stmt)

    r = _row(get_payment_calendar_daily_totals(), next_banking_day(date.today()))        # a weekend date lands on Monday, as everywhere
    assert r["due_amount"] == 900.0 and r["paid_amount"] == 0.0
