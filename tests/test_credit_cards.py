"""Credit card helpers, including the 2026-09-14 card-label fix (issuer bank
now shown in the dropdown label) and the statement/settlement functions."""
import sqlite3
from datetime import date

import pandas as pd

import app
from conftest import fetchone
from engines.cc_service import settle_cc_statement_via_cash


def _make_card_issuer(db_path, name="BDO Credit Card Services"):
    conn = sqlite3.connect(db_path)
    cur = conn.execute("INSERT INTO creditors (creditor_name, creditor_type) VALUES (?, 'CREDIT_CARD')", (name,))
    conn.commit()
    issuer_id = cur.lastrowid
    conn.close()
    return issuer_id


def test_card_label_includes_issuer_name(db):
    """The 2026-09-14 fix: disambiguates two cards with the same name/last4
    pattern by showing which bank issued each one."""
    issuer_id = _make_card_issuer(db, "RCBC Credit Card")
    app.add_credit_card(issuer_id, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE card_number_last4 = '8000'")[0]

    cards_df = pd.DataFrame([{
        "card_id": card_id, "issuer": "RCBC Credit Card", "card_name": "Gold", "card_number_last4": "8000",
    }])
    label = app._card_label(card_id, cards_df)

    assert label == "Gold - 8000 (RCBC Credit Card)"


def test_add_credit_card_stores_all_fields(db):
    issuer_id = _make_card_issuer(db)

    ok, msg = app.add_credit_card(issuer_id, None, "Platinum", "9000", 100000.0, 6, 15)

    assert ok
    row = fetchone(db, "SELECT card_name, card_number_last4, credit_limit, annual_fee_due_month, statement_day FROM credit_cards WHERE creditor_id = ?", (issuer_id,))
    assert row == ("Platinum", "9000", 100000.0, 6, 15)


def test_add_credit_card_rejects_zero_credit_limit(db):
    """2026-09-15 fix: credit_cards.credit_limit has a CHECK(credit_limit > 0) constraint --
    a 0-or-blank widget left untouched used to hit that raw sqlite3.IntegrityError uncaught,
    same class of bug as create_loan_note()'s principal_amount."""
    issuer_id = _make_card_issuer(db)

    ok, msg = app.add_credit_card(issuer_id, None, "Gold", "8000", 0.0, None, None)

    assert not ok
    assert "greater than zero" in msg.lower()
    assert fetchone(db, "SELECT COUNT(*) FROM credit_cards") == (0,)


def test_edit_credit_card_rejects_zero_credit_limit(db):
    issuer_id = _make_card_issuer(db)
    app.add_credit_card(issuer_id, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE creditor_id = ?", (issuer_id,))[0]

    ok, msg = app.edit_credit_card(card_id, None, 0.0, None, None)

    assert not ok
    assert "greater than zero" in msg.lower()
    row = fetchone(db, "SELECT credit_limit FROM credit_cards WHERE card_id = ?", (card_id,))
    assert row == (50000.0,)  # unchanged


def test_add_cc_statement_defaults_to_unpaid(db):
    """2026-09-15: add_cc_statement() now takes the rolling-balance breakdown (previous
    balance, new charges, interest, amount paid) instead of a single typed-in total --
    total_amount_due and status are both computed from it."""
    issuer_id = _make_card_issuer(db)
    app.add_credit_card(issuer_id, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE creditor_id = ?", (issuer_id,))[0]

    app.add_cc_statement(card_id, date(2026, 3, 1), previous_balance=0.0, new_charges=15000.0, interest_charged=0.0, amount_paid=0.0)

    row = fetchone(db, "SELECT total_amount_due, status FROM cc_statements WHERE card_id = ?", (card_id,))
    assert row == (15000.0, "Unpaid")


def test_settle_cc_statement_via_cash_marks_paid(db):
    issuer_id = _make_card_issuer(db)
    app.add_credit_card(issuer_id, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE creditor_id = ?", (issuer_id,))[0]
    app.add_cc_statement(card_id, date(2026, 3, 1), previous_balance=0.0, new_charges=15000.0, interest_charged=0.0, amount_paid=0.0)
    statement_id = fetchone(db, "SELECT cc_statement_id FROM cc_statements WHERE card_id = ?", (card_id,))[0]

    ok, msg = settle_cc_statement_via_cash(statement_id, 15000.0, "2026-02-20")

    assert ok
    row = fetchone(db, "SELECT status, payment_method FROM cc_statements WHERE cc_statement_id = ?", (statement_id,))
    assert row == ("Paid", "Cash / Online")


def test_settle_cc_statement_via_cash_rejects_already_paid(db):
    issuer_id = _make_card_issuer(db)
    app.add_credit_card(issuer_id, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE creditor_id = ?", (issuer_id,))[0]
    app.add_cc_statement(card_id, date(2026, 3, 1), previous_balance=0.0, new_charges=15000.0, interest_charged=0.0, amount_paid=0.0)
    statement_id = fetchone(db, "SELECT cc_statement_id FROM cc_statements WHERE card_id = ?", (card_id,))[0]
    settle_cc_statement_via_cash(statement_id, 15000.0, "2026-02-20")

    ok, msg = settle_cc_statement_via_cash(statement_id, 15000.0, "2026-02-21")

    assert not ok
    assert "already" in msg.lower()
