"""apply_check_correction()'s "Check Number" branch (2026-09-17 fix): used to re-implement the
duplicate/spoiled-number check inline instead of calling the shared _is_check_number_available()
that every other number-assignment path already uses -- two places to keep in sync, no shared
backstop if either drifted."""
import sqlite3
from datetime import date

import app
from conftest import make_creditor, make_statement, fetchone

BANK_ACCOUNT_ID = 1


def _make_printed_check(db_path, statement_id, creditor_id, check_number, amount=1000.0):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status, printed_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'Active', CURRENT_TIMESTAMP)",
        (BANK_ACCOUNT_ID, statement_id, creditor_id, check_number, amount, str(date(2026, 1, 1))),
    )
    check_id = cur.lastrowid
    conn.commit()
    conn.close()
    return check_id


def test_correct_check_number_to_a_free_number_succeeds(db):
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    check_id = _make_printed_check(db, statement_id, creditor_id, "1000")

    ok, msg = app.apply_check_correction(check_id, "Check Number", "1001", "typo fix")

    assert ok
    assert fetchone(db, "SELECT check_number FROM checks_issued WHERE check_id = ?", (check_id,)) == ("1001",)


def test_correct_check_number_rejects_a_number_already_on_file(db):
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    _make_printed_check(db, statement_id, creditor_id, "2000")
    check_id = _make_printed_check(db, statement_id, creditor_id, "2001")

    ok, msg = app.apply_check_correction(check_id, "Check Number", "2000", "typo fix")

    assert not ok
    assert "already on file" in msg.lower()


def test_correct_check_number_rejects_a_spoiled_number(db):
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    check_id = _make_printed_check(db, statement_id, creditor_id, "3000")
    app.log_spoiled_check("3001", "jammed printer")

    ok, msg = app.apply_check_correction(check_id, "Check Number", "3001", "typo fix")

    assert not ok
    assert "spoiled" in msg.lower()


def test_correct_check_number_allows_keeping_its_own_current_number(db):
    """Re-submitting the same number the check already has must not be blocked as
    'already on file' by matching itself."""
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    check_id = _make_printed_check(db, statement_id, creditor_id, "4000")

    ok, msg = app.apply_check_correction(check_id, "Check Number", "4000", "no real change")

    assert ok
