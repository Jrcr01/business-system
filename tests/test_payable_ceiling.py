"""The system-wide hard rule from CLAUDE.md: "a check can never be issued,
corrected, or left in place for more than what's actually payable." Covers
all three payable bases issue_check() checks (batch, loan note, installment)
plus issue_checks_grid()'s bulk-total version, and lock_statement()'s mirror
rule that a batch can't be locked while overpaid."""
from datetime import date

import app
from conftest import make_creditor, make_statement, fetchone


def test_issue_check_rejects_amount_over_batch_remaining(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-100", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)

    ok, msg, check_id = app.issue_check(creditor_id, 1000.01, date(2026, 1, 15), statement_id=statement_id)

    assert not ok
    assert check_id is None
    assert "exceeds" in msg


def test_issue_check_allows_amount_exactly_at_batch_remaining(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-101", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)

    ok, msg, check_id = app.issue_check(creditor_id, 1000.0, date(2026, 1, 15), statement_id=statement_id)

    assert ok
    assert check_id is not None


def test_issue_check_rejects_second_check_that_would_push_batch_over(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-102", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    ok1, _, _ = app.issue_check(creditor_id, 600.0, date(2026, 1, 15), statement_id=statement_id)
    assert ok1

    ok2, msg, check_id = app.issue_check(creditor_id, 500.0, date(2026, 1, 16), statement_id=statement_id)

    assert not ok2
    assert check_id is None
    assert "400" in msg.replace(",", "")  # remaining should read as 400.00


def test_issue_checks_grid_rejects_when_bulk_total_exceeds_remaining(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-103", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)

    ok, msg, ids = app.issue_checks_grid(
        creditor_id, statement_id,
        [{"check_date": date(2026, 1, 15), "amount": 600.0}, {"check_date": date(2026, 1, 16), "amount": 500.0}],
    )

    assert not ok
    assert ids is None
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE statement_id = ?", (statement_id,)) == (0,)


def test_issue_checks_grid_allows_bulk_total_exactly_at_remaining(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-104", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)

    ok, msg, ids = app.issue_checks_grid(
        creditor_id, statement_id,
        [{"check_date": date(2026, 1, 15), "amount": 600.0}, {"check_date": date(2026, 1, 16), "amount": 400.0}],
    )

    assert ok
    assert len(ids) == 2


def test_issue_checks_grid_rejects_any_zero_or_negative_row(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-105", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)

    ok, msg, ids = app.issue_checks_grid(
        creditor_id, statement_id, [{"check_date": date(2026, 1, 15), "amount": 0.0}],
    )

    assert not ok
    assert ids is None


def _make_loan_note(db_path, creditor_id, total_payable=10000.0, principal_amount=10000.0, note_ref="LN-1"):
    import sqlite3
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, total_payable, status) "
        "VALUES (?, ?, '2026-01-01', ?, ?, 'Active')",
        (creditor_id, note_ref, principal_amount, total_payable),
    )
    conn.commit()
    loan_note_id = cur.lastrowid
    conn.close()
    return loan_note_id


def test_issue_check_rejects_amount_over_loan_note_total_payable(db):
    creditor_id = make_creditor(db, creditor_type="LENDER")
    loan_note_id = _make_loan_note(db, creditor_id, total_payable=10000.0)

    ok, msg, check_id = app.issue_check(creditor_id, 10000.01, date(2026, 1, 15), loan_note_id=loan_note_id)

    assert not ok
    assert check_id is None
    assert "total payable" in msg.lower()


def test_issue_check_rejects_second_check_that_would_push_loan_note_over(db):
    creditor_id = make_creditor(db, creditor_type="LENDER")
    loan_note_id = _make_loan_note(db, creditor_id, total_payable=10000.0)
    ok1, _, _ = app.issue_check(creditor_id, 6000.0, date(2026, 1, 15), loan_note_id=loan_note_id)
    assert ok1

    ok2, msg, check_id = app.issue_check(creditor_id, 4000.01, date(2026, 1, 16), loan_note_id=loan_note_id)

    assert not ok2
    assert check_id is None


def test_issue_check_rejects_amount_over_installment_payable(db):
    import sqlite3

    creditor_id = make_creditor(db, creditor_type="LENDER")
    loan_note_id = _make_loan_note(db, creditor_id, total_payable=50000.0)
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO loan_installments (loan_note_id, installment_no, due_date, principal, interest, outstanding_balance, status) "
        "VALUES (?, 1, '2026-02-01', 5000, 500, 45000, 'Pending')",
        (loan_note_id,),
    )
    installment_id = cur.lastrowid
    conn.commit()
    conn.close()

    ok, msg, check_id = app.issue_check(creditor_id, 5500.01, date(2026, 1, 15), installment_id=installment_id)

    assert not ok
    assert check_id is None
    assert "payable" in msg.lower()


def test_lock_statement_rejects_when_active_checks_exceed_net_total(db):
    """Guards against a hypothetical future bug that lets an over-payable check
    through some other path (e.g. a direct SQL edit, or a bug in issue_check's
    own guard) -- lock_statement() is meant to be the last line of defense."""
    import sqlite3

    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-106", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    # Bypass issue_check()'s own guard entirely, simulating a stray over-payable check.
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_date, amount, status) "
        "VALUES (?, 1, ?, '2026-01-15', 1500, 'Active')",
        (creditor_id, statement_id),
    )
    conn.commit()
    conn.close()

    ok, msg = app.lock_statement(statement_id)

    assert not ok
    assert "exceed" in msg.lower()


def test_lock_statement_allows_when_exactly_balanced(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-107", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    app.issue_check(creditor_id, 1000.0, date(2026, 1, 15), statement_id=statement_id)

    ok, _ = app.lock_statement(statement_id)

    assert ok
    assert fetchone(db, "SELECT is_locked FROM order_statements WHERE statement_id = ?", (statement_id,)) == (1,)
