"""Unified Debt Interest Tracking (2026-09-15) -- interest visibility across the three
debt sources: Private Loans (loan_installments.interest, already tracked), Bank Loans
(bank_loan_installments.interest_portion, optional, "still simplified"), and Credit
Cards (cc_statements' new rolling-balance columns). Covers the rolling CC balance
calculation, the outstanding-past-due-date fix, interest extraction per source, the
unified total, and the trend helper."""
import sqlite3
from datetime import date

import app
from conftest import make_creditor, fetchone
from engines import interest_service
from engines.bank_loan_service import create_bank_loan, add_bank_loan_installment


def _make_loan_note(db_path, creditor_id, total_payable=10000.0, principal_amount=10000.0, note_ref="LN-1"):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, total_payable, status) "
        "VALUES (?, ?, '2026-01-01', ?, ?, 'Active')",
        (creditor_id, note_ref, principal_amount, total_payable),
    )
    loan_note_id = cur.lastrowid
    conn.commit()
    conn.close()
    return loan_note_id


def test_cc_rolling_balance_calculation(db):
    """previous + new + interest - paid = unpaid_balance_next, and total_amount_due is
    computed, not typed in."""
    issuer_id = make_creditor(db, creditor_type="CREDIT_CARD", name="Amex")
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '1234', 100000)",
        (issuer_id,),
    )
    card_id = cur.lastrowid
    conn.commit()
    conn.close()

    app.add_cc_statement(card_id, date(2026, 3, 15), previous_balance=0.0, new_charges=100000.0, interest_charged=5000.0, amount_paid=80000.0)

    row = fetchone(db, "SELECT total_amount_due, unpaid_balance_next, status FROM cc_statements WHERE card_id = ?", (card_id,))
    assert row == (105000.0, 25000.0, "Partially Paid")


def test_cc_previous_balance_autofills_from_last_statement(db):
    issuer_id = make_creditor(db, creditor_type="CREDIT_CARD", name="Amex")
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '1234', 100000)",
        (issuer_id,),
    )
    card_id = cur.lastrowid
    conn.commit()
    conn.close()

    assert app.get_last_cc_statement_unpaid_balance(card_id) == 0.0

    app.add_cc_statement(card_id, date(2026, 3, 15), previous_balance=0.0, new_charges=100000.0, interest_charged=5000.0, amount_paid=80000.0)
    assert app.get_last_cc_statement_unpaid_balance(card_id) == 25000.0

    # Next month's statement should pick up exactly that as its own previous_balance.
    next_previous = app.get_last_cc_statement_unpaid_balance(card_id)
    app.add_cc_statement(card_id, date(2026, 4, 15), previous_balance=next_previous, new_charges=0.0, interest_charged=1000.0, amount_paid=26000.0)

    row = fetchone(db, "SELECT previous_balance, total_amount_due, unpaid_balance_next FROM cc_statements WHERE due_date = '2026-04-15'")
    assert row == (25000.0, 26000.0, 0.0)


def test_cc_outstanding_counts_past_due_when_balance_remains(db):
    """The 'new rule wins' fix: a statement with a real unpaid_balance_next stays
    outstanding even once its due date has passed -- mirrors the exact CASE expression
    used for the Dashboard's ap_outstanding KPI and get_combined_ap_summary()."""
    issuer_id = make_creditor(db, creditor_type="CREDIT_CARD", name="Amex")
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '1234', 100000)",
        (issuer_id,),
    )
    card_id = cur.lastrowid
    conn.commit()
    conn.close()

    # A statement due well in the past, still carrying a real unpaid balance.
    app.add_cc_statement(card_id, date(2020, 1, 1), previous_balance=0.0, new_charges=10000.0, interest_charged=0.0, amount_paid=4000.0)

    outstanding = fetchone(
        db,
        """SELECT COALESCE(SUM(
               CASE WHEN unpaid_balance_next > 0 THEN unpaid_balance_next
                    WHEN due_date >= DATE('now', 'localtime') THEN total_amount_due
                    ELSE 0 END
           ), 0) FROM cc_statements WHERE status NOT IN ('Paid', 'Cancelled')""",
    )[0]
    assert outstanding == 6000.0


def test_private_loan_interest_extraction(db):
    creditor_id = make_creditor(db, creditor_type="LENDER", name="Mom")
    loan_note_id = _make_loan_note(db, creditor_id, total_payable=100000.0)
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO loan_installments (loan_note_id, installment_no, due_date, principal, interest, outstanding_balance, status) "
        "VALUES (?, 1, ?, 5000, 2000, 95000, 'Pending')",
        (loan_note_id, date.today().isoformat()),
    )
    installment_id = cur.lastrowid
    conn.commit()
    conn.close()

    ok, _msg, _check_id = app.issue_check(creditor_id, 7000.0, date.today(), loan_note_id=loan_note_id, installment_id=installment_id)
    assert ok

    today = date.today()
    summary = interest_service.get_monthly_interest_summary(today.year, today.month)
    assert summary["private_loans"]["subtotal"] == 2000.0
    assert summary["private_loans"]["count"] == 1


def test_bank_loan_interest_extraction(db):
    creditor_id = make_creditor(db, creditor_type="BANK_LOAN", name="Metrobank")
    today = date.today()
    bank_loan_id = create_bank_loan(
        creditor_id, "BL-1", "ACCT-1", today, date(today.year + 5, today.month, today.day), None, 500000.0, "ADA",
    )
    ok, _msg = add_bank_loan_installment(bank_loan_id, today, 10500.0, interest_portion=2500.0)
    assert ok
    # due today = still owed (owner, 2026-09-28): not paid interest yet
    assert interest_service.get_monthly_interest_summary(today.year, today.month)["bank_loans"]["subtotal"] == 0

    past = date(today.year - 1, today.month, 1)                     # a due date that has passed counts
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE bank_loan_installments SET due_date = ? WHERE bank_loan_id = ?", (past.isoformat(), bank_loan_id))
    summary = interest_service.get_monthly_interest_summary(past.year, past.month)
    assert summary["bank_loans"]["subtotal"] == 2500.0
    assert summary["bank_loans"]["count"] == 1


def test_bank_loan_installment_without_interest_defaults_zero(db):
    """A plain installment (no interest_portion given) still works exactly as before --
    "still simplified" -- and just contributes nothing to interest tracking."""
    creditor_id = make_creditor(db, creditor_type="BANK_LOAN", name="BDO")
    today = date.today()
    bank_loan_id = create_bank_loan(
        creditor_id, "BL-2", "ACCT-2", today, date(today.year + 3, today.month, today.day), None, 200000.0, "ADA",
    )
    ok, _msg = add_bank_loan_installment(bank_loan_id, today, 5000.0)  # no interest_portion arg
    assert ok

    row = fetchone(db, "SELECT interest_portion FROM bank_loan_installments WHERE bank_loan_id = ?", (bank_loan_id,))
    assert row == (0.0,)

    summary = interest_service.get_monthly_interest_summary(today.year, today.month)
    assert summary["bank_loans"]["subtotal"] == 0.0


def test_cc_interest_extraction(db):
    issuer_id = make_creditor(db, creditor_type="CREDIT_CARD", name="Citi")
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '5678', 100000)",
        (issuer_id,),
    )
    card_id = cur.lastrowid
    conn.commit()
    conn.close()

    today = date.today()
    app.add_cc_statement(card_id, today, previous_balance=0.0, new_charges=100000.0, interest_charged=5000.0, amount_paid=80000.0)

    summary = interest_service.get_monthly_interest_summary(today.year, today.month)
    assert summary["credit_cards"]["subtotal"] == 5000.0
    assert summary["credit_cards"]["count"] == 1


def test_unified_interest_total(db):
    """Total interest = private + bank + credit cards, combining all three sources set
    up the same way the individual extraction tests do."""
    today = date(date.today().year - 1, date.today().month, 15)   # a past date: bank-loan interest counts once its due date has passed

    lender_id = make_creditor(db, creditor_type="LENDER", name="Mom")
    loan_note_id = _make_loan_note(db, lender_id, total_payable=100000.0)
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO loan_installments (loan_note_id, installment_no, due_date, principal, interest, outstanding_balance, status) "
        "VALUES (?, 1, ?, 5000, 2000, 95000, 'Pending')",
        (loan_note_id, today.isoformat()),
    )
    installment_id = cur.lastrowid
    conn.commit()
    conn.close()
    app.issue_check(lender_id, 7000.0, today, loan_note_id=loan_note_id, installment_id=installment_id)

    bank_id = make_creditor(db, creditor_type="BANK_LOAN", name="Metrobank")
    bank_loan_id = create_bank_loan(bank_id, "BL-3", "ACCT-3", today, date(today.year + 5, today.month, today.day), None, 500000.0, "ADA")
    add_bank_loan_installment(bank_loan_id, today, 10500.0, interest_portion=2500.0)

    issuer_id = make_creditor(db, creditor_type="CREDIT_CARD", name="Amex")
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '1234', 100000)",
        (issuer_id,),
    )
    card_id = cur.lastrowid
    conn.commit()
    conn.close()
    app.add_cc_statement(card_id, today, previous_balance=0.0, new_charges=100000.0, interest_charged=5000.0, amount_paid=80000.0)

    summary = interest_service.get_monthly_interest_summary(today.year, today.month)
    assert summary["total_interest_paid"] == 2000.0 + 2500.0 + 5000.0


def test_yearly_interest_trend_has_twelve_months(db):
    today = date.today()
    trend = interest_service.get_yearly_interest_trend(today.year)

    assert len(trend) == 12
    assert list(trend["month"]) == list(range(1, 13))
    assert "total_interest" in trend.columns
