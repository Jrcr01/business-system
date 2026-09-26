"""create_loan_note() (2026-09-15 fix): a real crash the user hit live -- Principal
amount left at its 0.0 default and submitted hit loan_notes' own
CHECK(principal_amount > 0) constraint as a raw, uncaught sqlite3.IntegrityError
all the way up to the user. Validates like every other create/edit function in
this app now (edit_loan_note_terms already did this correctly)."""
from datetime import date

import app
from conftest import make_creditor, fetchone


def test_create_loan_note_rejects_zero_principal(db):
    creditor_id = make_creditor(db, creditor_type="LENDER")

    ok, msg, loan_note_id = app.create_loan_note(creditor_id, date(2026, 1, 1), 0.0, 2.0)

    assert not ok
    assert loan_note_id is None
    assert "greater than zero" in msg.lower()
    assert fetchone(db, "SELECT COUNT(*) FROM loan_notes") == (0,)


def test_create_loan_note_rejects_negative_rate(db):
    creditor_id = make_creditor(db, creditor_type="LENDER")

    ok, msg, loan_note_id = app.create_loan_note(creditor_id, date(2026, 1, 1), 10000.0, -1.0)

    assert not ok
    assert loan_note_id is None
    assert "negative" in msg.lower()


def test_create_loan_note_succeeds_with_valid_data(db):
    creditor_id = make_creditor(db, creditor_type="LENDER")

    ok, msg, loan_note_id = app.create_loan_note(creditor_id, date(2026, 1, 1), 100000.0, 2.0)

    assert ok
    assert loan_note_id is not None
    row = fetchone(db, "SELECT principal_amount, monthly_interest_rate, total_payable, status FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
    assert row == (100000.0, 2.0, 100000.0, "Active")


def test_update_loan_note_status_accepts_a_valid_status(db):
    creditor_id = make_creditor(db, creditor_type="LENDER")
    _, _, loan_note_id = app.create_loan_note(creditor_id, date(2026, 1, 1), 100000.0, 2.0)

    ok, msg = app.update_loan_note_status(loan_note_id, "Defaulted")

    assert ok
    assert fetchone(db, "SELECT status FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,)) == ("Defaulted",)


def test_update_loan_note_status_rejects_an_invalid_status(db):
    """2026-09-17 fix: this used to have no server-side whitelist at all -- only the UI's own
    constrained selectbox stopped a bad value, and any other caller would have hit the raw
    sqlite3.IntegrityError from loan_notes' own CHECK(status IN (...)) uncaught."""
    creditor_id = make_creditor(db, creditor_type="LENDER")
    _, _, loan_note_id = app.create_loan_note(creditor_id, date(2026, 1, 1), 100000.0, 2.0)

    ok, msg = app.update_loan_note_status(loan_note_id, "Bankrupt")

    assert not ok
    assert "valid" in msg.lower()
    assert fetchone(db, "SELECT status FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,)) == ("Active",)


def test_create_loan_note_rejects_sub_peso_typo_principal(db):
    creditor_id = make_creditor(db, creditor_type="LENDER")

    ok, msg, loan_note_id = app.create_loan_note(creditor_id, date(2026, 1, 1), 0.001, 3.0)

    assert not ok
    assert loan_note_id is None
    assert "typo" in msg.lower()
