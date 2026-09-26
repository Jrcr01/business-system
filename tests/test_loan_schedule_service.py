"""engines/loan_schedule_service.py -- previously had zero test coverage at all. Covers the
2026-09-17 fixes: editing an installment that already has an Active check against it is now
blocked (mirroring delete_loan_installment's existing guard), and adding/editing an installment
on a "Pending Setup" placeholder note fails with a clear message instead of crashing."""
import sqlite3
from datetime import date

import app
from conftest import make_creditor, fetchone
from engines.loan_schedule_service import add_loan_installment, edit_loan_installment, delete_loan_installment


def _make_loan_note(db_path, principal=100000.0, rate=2.0):
    creditor_id = make_creditor(db_path, creditor_type="LENDER")
    ok, _msg, loan_note_id = app.create_loan_note(creditor_id, date(2026, 1, 1), principal, rate)
    assert ok
    return loan_note_id


def _mark_active(db_path, installment_id):
    conn = sqlite3.connect(db_path)
    conn.execute("UPDATE loan_installments SET status = 'Active' WHERE installment_id = ?", (installment_id,))
    conn.commit()
    conn.close()


def test_add_installment_recomputes_outstanding_balance(db):
    loan_note_id = _make_loan_note(db)

    ok, msg = add_loan_installment(loan_note_id, date(2026, 2, 1), 10000.0, 500.0)

    assert ok
    row = fetchone(db, "SELECT installment_no, outstanding_balance FROM loan_installments WHERE loan_note_id = ?", (loan_note_id,))
    assert row == (1, 90000.0)


def test_edit_installment_updates_amount_and_recomputes(db):
    loan_note_id = _make_loan_note(db)
    add_loan_installment(loan_note_id, date(2026, 2, 1), 10000.0, 500.0)
    installment_id = fetchone(db, "SELECT installment_id FROM loan_installments WHERE loan_note_id = ?", (loan_note_id,))[0]

    ok, msg = edit_loan_installment(installment_id, date(2026, 2, 1), 20000.0, 500.0)

    assert ok
    row = fetchone(db, "SELECT principal, outstanding_balance FROM loan_installments WHERE installment_id = ?", (installment_id,))
    assert row == (20000.0, 80000.0)


def test_edit_installment_rejected_once_a_check_is_active_against_it(db):
    """2026-09-17 fix: edit_loan_installment() had no lock at all, unlike
    delete_loan_installment() -- a settled installment's amount could be silently rewritten
    after a real check had already been cut against it."""
    loan_note_id = _make_loan_note(db)
    add_loan_installment(loan_note_id, date(2026, 2, 1), 10000.0, 500.0)
    installment_id = fetchone(db, "SELECT installment_id FROM loan_installments WHERE loan_note_id = ?", (loan_note_id,))[0]
    _mark_active(db, installment_id)

    ok, msg = edit_loan_installment(installment_id, date(2026, 2, 1), 99999.0, 500.0)

    assert not ok
    assert "active check" in msg.lower()
    row = fetchone(db, "SELECT principal FROM loan_installments WHERE installment_id = ?", (installment_id,))
    assert row == (10000.0,)  # unchanged


def test_delete_installment_still_rejected_once_active_unchanged_behavior(db):
    loan_note_id = _make_loan_note(db)
    add_loan_installment(loan_note_id, date(2026, 2, 1), 10000.0, 500.0)
    installment_id = fetchone(db, "SELECT installment_id FROM loan_installments WHERE loan_note_id = ?", (loan_note_id,))[0]
    _mark_active(db, installment_id)

    ok, msg = delete_loan_installment(installment_id)

    assert not ok
    assert "active check" in msg.lower()


def test_add_installment_on_pending_setup_note_fails_cleanly(db):
    conn = sqlite3.connect(db)
    creditor_id = make_creditor(db, creditor_type="LENDER")
    cur = conn.execute(
        "INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, monthly_interest_rate, status) "
        "VALUES (?, 'PENDING-99', '2026-01-01', NULL, NULL, 'Pending Setup')",
        (creditor_id,),
    )
    loan_note_id = cur.lastrowid
    conn.commit()
    conn.close()

    ok, msg = add_loan_installment(loan_note_id, date(2026, 2, 1), 1000.0, 50.0)

    assert not ok
    assert "terms first" in msg.lower()


def _add_check(db_path, loan_note_id, creditor_id, amount, check_date, number):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO checks_issued (creditor_id, loan_note_id, amount, check_date, check_number, status, bank_account_id) VALUES (?, ?, ?, ?, ?, 'Active', 1)",
        (creditor_id, loan_note_id, amount, check_date, number),
    )
    conn.commit()
    cid = cur.lastrowid
    conn.close()
    return cid


def test_create_schedule_from_existing_checks(db):
    from engines.loan_schedule_service import create_schedule_from_checks, unlinked_loan_checks
    from engines.duplicate_service import loan_check_mismatches
    loan_note_id = _make_loan_note(db, principal=100000.0)
    creditor_id = fetchone(db, "SELECT creditor_id FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))[0]
    _add_check(db, loan_note_id, creditor_id, 30000.0, "2026-10-01", "1001")
    _add_check(db, loan_note_id, creditor_id, 20000.0, "2026-11-01", "1002")
    assert len(unlinked_loan_checks(loan_note_id)) == 2

    ok, msg = create_schedule_from_checks(loan_note_id)

    assert ok, msg
    assert unlinked_loan_checks(loan_note_id) == []
    assert loan_check_mismatches(loan_note_id) == []
    assert fetchone(db, "SELECT outstanding_balance FROM loan_installments WHERE loan_note_id = ? ORDER BY due_date DESC", (loan_note_id,))[0] == 50000.0
    ok2, _ = create_schedule_from_checks(loan_note_id)
    assert not ok2   # nothing left to do -- no duplicates


def test_create_schedule_from_checks_refused_when_over_principal(db):
    from engines.loan_schedule_service import create_schedule_from_checks
    loan_note_id = _make_loan_note(db, principal=10000.0)
    creditor_id = fetchone(db, "SELECT creditor_id FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))[0]
    _add_check(db, loan_note_id, creditor_id, 30000.0, "2026-10-01", "1001")

    ok, msg = create_schedule_from_checks(loan_note_id)

    assert not ok and "Nothing was changed" in msg
    assert fetchone(db, "SELECT COUNT(*) FROM loan_installments WHERE loan_note_id = ?", (loan_note_id,))[0] == 0
