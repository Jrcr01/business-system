"""engines/bank_loan_service.py -- previously had zero test coverage at all. Covers the
2026-09-17 fixes: editing an installment whose due date has already passed (treated as paid) is
now blocked (mirroring delete_bank_loan_installment's existing guard), and create_bank_loan()
rejects a non-positive principal or a maturity date after the start date."""
from datetime import date, timedelta

import pytest

from conftest import make_creditor, fetchone
from engines.bank_loan_service import (
    create_bank_loan,
    add_bank_loan_installment,
    edit_bank_loan_installment,
    delete_bank_loan_installment,
)


def _make_bank_loan(db_path, principal=500000.0):
    creditor_id = make_creditor(db_path, creditor_type="BANK_LOAN")
    bank_loan_id = create_bank_loan(
        creditor_id, "BL-TEST-1", "ACC-001", date(2026, 1, 1), date(2027, 1, 1), None, principal, "ADA",
    )
    return bank_loan_id


def test_create_bank_loan_rejects_non_positive_principal(db):
    creditor_id = make_creditor(db, creditor_type="BANK_LOAN")

    with pytest.raises(ValueError, match="greater than zero"):
        create_bank_loan(creditor_id, "BL-BAD-1", "ACC-002", date(2026, 1, 1), date(2027, 1, 1), None, 0.0, "ADA")


def test_create_bank_loan_rejects_maturity_before_start(db):
    creditor_id = make_creditor(db, creditor_type="BANK_LOAN")

    with pytest.raises(ValueError, match="after the start date"):
        create_bank_loan(creditor_id, "BL-BAD-2", "ACC-003", date(2027, 1, 1), date(2026, 1, 1), None, 100000.0, "ADA")


def test_edit_bank_loan_installment_updates_amount(db):
    bank_loan_id = _make_bank_loan(db)
    future_due = date.today() + timedelta(days=30)
    add_bank_loan_installment(bank_loan_id, future_due, 5000.0)
    installment_id = fetchone(db, "SELECT installment_id FROM bank_loan_installments WHERE bank_loan_id = ?", (bank_loan_id,))[0]

    ok, msg = edit_bank_loan_installment(installment_id, future_due, 7500.0)

    assert ok
    row = fetchone(db, "SELECT amount FROM bank_loan_installments WHERE installment_id = ?", (installment_id,))
    assert row == (7500.0,)


def test_edit_bank_loan_installment_rejected_once_due_date_has_passed(db):
    """2026-09-17 fix: edit_bank_loan_installment() had no is_installment_cleared() check at
    all, unlike delete_bank_loan_installment() -- an already-"Paid" (past due date) row's
    amount could be silently rewritten with no audit trail."""
    bank_loan_id = _make_bank_loan(db)
    past_due = date.today() - timedelta(days=5)
    add_bank_loan_installment(bank_loan_id, past_due, 5000.0)
    installment_id = fetchone(db, "SELECT installment_id FROM bank_loan_installments WHERE bank_loan_id = ?", (bank_loan_id,))[0]

    ok, msg = edit_bank_loan_installment(installment_id, past_due, 99999.0)

    assert not ok
    assert "already passed" in msg.lower()
    row = fetchone(db, "SELECT amount FROM bank_loan_installments WHERE installment_id = ?", (installment_id,))
    assert row == (5000.0,)  # unchanged


def test_delete_bank_loan_installment_still_rejected_once_past_due_unchanged_behavior(db):
    bank_loan_id = _make_bank_loan(db)
    past_due = date.today() - timedelta(days=5)
    add_bank_loan_installment(bank_loan_id, past_due, 5000.0)
    installment_id = fetchone(db, "SELECT installment_id FROM bank_loan_installments WHERE bank_loan_id = ?", (bank_loan_id,))[0]

    ok, msg = delete_bank_loan_installment(installment_id)

    assert not ok
    assert "already passed" in msg.lower()


def test_create_bank_loan_rejects_sub_peso_typo_principal(db):
    creditor_id = make_creditor(db, creditor_type="BANK_LOAN")

    with pytest.raises(ValueError, match="typo"):
        create_bank_loan(creditor_id, "BL-TYPO-1", "ACC-004", date(2026, 1, 1), date(2027, 1, 1), None, 0.001, "ADA")


def test_create_bank_loan_rejects_maturity_equal_to_start(db):
    creditor_id = make_creditor(db, creditor_type="BANK_LOAN")

    with pytest.raises(ValueError, match="after the start date"):
        create_bank_loan(creditor_id, "BL-SAME-1", "ACC-005", date(2026, 1, 1), date(2026, 1, 1), None, 100000.0, "ADA")


def test_cancelled_bank_loan_keeps_rows_but_stops_counting_as_due(db):
    from engines.bank_loan_service import cancel_bank_loan
    from engines.reports_service import get_bank_loan_servicing_report
    bank_loan_id = _make_bank_loan(db)
    add_bank_loan_installment(bank_loan_id, date.today() + timedelta(days=30), 5000.0)

    ok, _ = cancel_bank_loan(bank_loan_id)

    assert ok
    assert fetchone(db, "SELECT COUNT(*) FROM bank_loan_installments WHERE bank_loan_id = ?", (bank_loan_id,)) == (1,)
    report = get_bank_loan_servicing_report()
    assert report.iloc[0]["status"] == "Cancelled"
    assert report.iloc[0]["outstanding"] == 0
    assert cancel_bank_loan(bank_loan_id)[0] is False
