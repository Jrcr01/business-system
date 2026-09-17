"""Same-day-duplicate check warning and "last check issued" reference (2026-09-17) -- the owner's
own standing practice is to never write two checks to the same supplier on the same day
(postdating past the due date instead specifically to avoid it), so these back an advisory (not
blocking) warning surfaced when issuing a check."""
import sqlite3
from datetime import date

import app
from conftest import make_creditor, make_statement


def _make_statement_with_ref(db_path, creditor_id, statement_ref):
    """conftest.make_statement() hardcodes a single 'TEST-REF' ref, so it can't be called twice
    in the same test (UNIQUE constraint) -- this variant takes its own ref."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, ?, 0, 'Draft')",
        (creditor_id, statement_ref),
    )
    conn.commit()
    statement_id = cur.lastrowid
    conn.close()
    return statement_id


def test_latest_check_for_creditor_returns_none_when_nothing_issued(db):
    creditor_id = make_creditor(db)
    assert app.get_latest_check_for_creditor(creditor_id) is None


def test_latest_check_for_creditor_returns_most_recent_by_date_not_insertion_order(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 5000.0, [], statement_id)
    # Issued second but dated earlier -- the function should still pick the later DATE, not
    # whichever row was inserted last.
    app.issue_check(creditor_id, 2000.0, date(2026, 3, 1), statement_id=statement_id)
    app.issue_check(creditor_id, 3000.0, date(2026, 2, 1), statement_id=statement_id)

    result = app.get_latest_check_for_creditor(creditor_id)
    assert result == (date(2026, 3, 1), 2000.0)


def test_latest_check_for_creditor_ignores_cancelled_checks(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 5000.0, [], statement_id)
    ok, _, check_id = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    assert ok
    app.cancel_check(check_id)

    assert app.get_latest_check_for_creditor(creditor_id) is None


def test_existing_check_dates_for_creditor_excludes_other_creditors(db):
    creditor_a = make_creditor(db, name="Supplier A")
    statement_a = _make_statement_with_ref(db, creditor_a, "REF-A")
    app.add_invoice(creditor_a, "INV-A", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_a)
    app.issue_check(creditor_a, 1000.0, date(2026, 5, 1), statement_id=statement_a)

    creditor_b = make_creditor(db, name="Supplier B")
    statement_b = _make_statement_with_ref(db, creditor_b, "REF-B")
    app.add_invoice(creditor_b, "INV-B", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_b)
    app.issue_check(creditor_b, 1000.0, date(2026, 5, 2), statement_id=statement_b)

    assert app.get_existing_check_dates_for_creditor(creditor_a) == {date(2026, 5, 1)}
    assert app.get_existing_check_dates_for_creditor(creditor_b) == {date(2026, 5, 2)}
