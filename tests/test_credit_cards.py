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

    ok, msg = app.edit_credit_card(card_id, "Gold", issuer_id, None, 0.0, None, None)

    assert not ok
    assert "greater than zero" in msg.lower()
    row = fetchone(db, "SELECT credit_limit FROM credit_cards WHERE card_id = ?", (card_id,))
    assert row == (50000.0,)  # unchanged


def test_edit_credit_card_updates_name_and_issuer(db):
    issuer_a = _make_card_issuer(db, "RCBC")
    issuer_b = _make_card_issuer(db, "BDO")
    app.add_credit_card(issuer_a, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE creditor_id = ?", (issuer_a,))[0]

    ok, msg = app.edit_credit_card(card_id, "Platinum", issuer_b, None, 50000.0, None, None)

    assert ok
    row = fetchone(db, "SELECT card_name, creditor_id FROM credit_cards WHERE card_id = ?", (card_id,))
    assert row == ("Platinum", issuer_b)


def test_edit_credit_card_rejects_blank_name(db):
    issuer_id = _make_card_issuer(db)
    app.add_credit_card(issuer_id, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE creditor_id = ?", (issuer_id,))[0]

    ok, msg = app.edit_credit_card(card_id, "   ", issuer_id, None, 50000.0, None, None)

    assert not ok
    assert "blank" in msg.lower()


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


def test_deactivate_and_reactivate_credit_card(db):
    """2026-09-17 CRUD parity fix: credit_cards.is_active existed in the schema but had no
    function toggling it -- a closed/replaced card couldn't be hidden from new-statement pickers."""
    issuer_id = _make_card_issuer(db)
    app.add_credit_card(issuer_id, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE creditor_id = ?", (issuer_id,))[0]

    app.deactivate_credit_card(card_id)
    assert fetchone(db, "SELECT is_active FROM credit_cards WHERE card_id = ?", (card_id,)) == (0,)

    app.reactivate_credit_card(card_id)
    assert fetchone(db, "SELECT is_active FROM credit_cards WHERE card_id = ?", (card_id,)) == (1,)


def test_edit_cardholder_updates_fields(db):
    """2026-09-17 CRUD parity fix: cardholders had add_cardholder() only, no edit at all."""
    cardholder_id = app.add_cardholder("Rhodora Roxas", "Finance", "0917-000-0000")

    ok, msg = app.edit_cardholder(cardholder_id, "Rhodora R. Roxas", "Finance", "0917-111-1111")

    assert ok
    row = fetchone(db, "SELECT name, contact_number FROM cardholders WHERE cardholder_id = ?", (cardholder_id,))
    assert row == ("Rhodora R. Roxas", "0917-111-1111")


def test_edit_cardholder_rejects_blank_name(db):
    cardholder_id = app.add_cardholder("Rhodora Roxas", "Finance", "0917-000-0000")

    ok, msg = app.edit_cardholder(cardholder_id, "   ", "Finance", "0917-000-0000")

    assert not ok
    assert "blank" in msg.lower()


def test_deactivate_and_reactivate_cardholder(db):
    """2026-09-17 CRUD parity fix: no is_active column or toggle existed for cardholders at all
    (migration 037) -- someone who's left couldn't be removed from new-card/statement pickers
    without losing their existing card history."""
    cardholder_id = app.add_cardholder("Rhodora Roxas", "Finance", "0917-000-0000")

    app.deactivate_cardholder(cardholder_id)
    assert fetchone(db, "SELECT is_active FROM cardholders WHERE cardholder_id = ?", (cardholder_id,)) == (0,)

    app.reactivate_cardholder(cardholder_id)
    assert fetchone(db, "SELECT is_active FROM cardholders WHERE cardholder_id = ?", (cardholder_id,)) == (1,)


def test_edit_cc_statement_updates_latest_statement(db):
    """2026-09-17 CRUD parity fix: cc_statements had no general edit at all, only the
    settle-via-cash status transition -- a mistyped amount couldn't be corrected."""
    issuer_id = _make_card_issuer(db)
    app.add_credit_card(issuer_id, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE creditor_id = ?", (issuer_id,))[0]
    app.add_cc_statement(card_id, date(2026, 3, 1), previous_balance=0.0, new_charges=15000.0, interest_charged=0.0, amount_paid=0.0)
    statement_id = fetchone(db, "SELECT cc_statement_id FROM cc_statements WHERE card_id = ?", (card_id,))[0]

    ok, msg = app.edit_cc_statement(statement_id, date(2026, 3, 1), previous_balance=0.0, new_charges=16000.0, interest_charged=0.0, amount_paid=0.0)

    assert ok
    row = fetchone(db, "SELECT total_amount_due, status FROM cc_statements WHERE cc_statement_id = ?", (statement_id,))
    assert row == (16000.0, "Unpaid")


def test_edit_cc_statement_rejects_non_latest(db):
    """Editing an older statement would leave a later statement's carried-forward
    previous_balance out of sync with the change -- only the latest is editable."""
    issuer_id = _make_card_issuer(db)
    app.add_credit_card(issuer_id, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE creditor_id = ?", (issuer_id,))[0]
    app.add_cc_statement(card_id, date(2026, 3, 1), previous_balance=0.0, new_charges=15000.0, interest_charged=0.0, amount_paid=0.0)
    old_statement_id = fetchone(db, "SELECT cc_statement_id FROM cc_statements WHERE card_id = ?", (card_id,))[0]
    app.add_cc_statement(card_id, date(2026, 4, 1), previous_balance=15000.0, new_charges=0.0, interest_charged=0.0, amount_paid=0.0)

    ok, msg = app.edit_cc_statement(old_statement_id, date(2026, 3, 1), previous_balance=0.0, new_charges=16000.0, interest_charged=0.0, amount_paid=0.0)

    assert not ok
    assert "most recent" in msg.lower()


def test_delete_cc_statement_removes_latest(db):
    issuer_id = _make_card_issuer(db)
    app.add_credit_card(issuer_id, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE creditor_id = ?", (issuer_id,))[0]
    app.add_cc_statement(card_id, date(2026, 3, 1), previous_balance=0.0, new_charges=15000.0, interest_charged=0.0, amount_paid=0.0)
    statement_id = fetchone(db, "SELECT cc_statement_id FROM cc_statements WHERE card_id = ?", (card_id,))[0]

    ok, msg = app.delete_cc_statement(statement_id)

    assert ok
    assert fetchone(db, "SELECT COUNT(*) FROM cc_statements WHERE cc_statement_id = ?", (statement_id,)) == (0,)


def test_delete_cc_statement_rejects_non_latest(db):
    issuer_id = _make_card_issuer(db)
    app.add_credit_card(issuer_id, None, "Gold", "8000", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE creditor_id = ?", (issuer_id,))[0]
    app.add_cc_statement(card_id, date(2026, 3, 1), previous_balance=0.0, new_charges=15000.0, interest_charged=0.0, amount_paid=0.0)
    old_statement_id = fetchone(db, "SELECT cc_statement_id FROM cc_statements WHERE card_id = ?", (card_id,))[0]
    app.add_cc_statement(card_id, date(2026, 4, 1), previous_balance=15000.0, new_charges=0.0, interest_charged=0.0, amount_paid=0.0)

    ok, msg = app.delete_cc_statement(old_statement_id)

    assert not ok
    assert "most recent" in msg.lower()


def test_add_credit_card_rejects_sub_peso_typo_limit(db):
    """A number field typed into without clearing can append after "0.00" (100000 -> 0.001);
    anything under the minimum money amount is treated as a typo rather than saved."""
    issuer_id = _make_card_issuer(db)

    ok, msg = app.add_credit_card(issuer_id, None, "Gold", "8000", 0.001, None, None)

    assert not ok
    assert "typo" in msg.lower()
    assert fetchone(db, "SELECT COUNT(*) FROM credit_cards") == (0,)


def test_get_or_create_card_issuer_reuses_an_existing_bank_case_insensitively(db):
    """2026-09-19: issuer is just a name typed/picked on the card now, not a record managed on its own."""
    first = app.get_or_create_card_issuer("BDO")
    again = app.get_or_create_card_issuer("  bdo ")

    assert first == again
    assert fetchone(db, "SELECT COUNT(*) FROM creditors WHERE creditor_type = 'CREDIT_CARD'") == (1,)


def test_get_or_create_card_issuer_creates_a_new_bank_and_reactivates_a_deactivated_one(db):
    new_id = app.get_or_create_card_issuer("Security Bank")
    assert fetchone(db, "SELECT creditor_name, creditor_type FROM creditors WHERE creditor_id = ?", (new_id,)) == ("Security Bank", "CREDIT_CARD")

    conn = sqlite3.connect(db); conn.execute("UPDATE creditors SET is_active = 0 WHERE creditor_id = ?", (new_id,)); conn.commit(); conn.close()
    assert app.get_or_create_card_issuer("security bank") == new_id
    assert fetchone(db, "SELECT is_active FROM creditors WHERE creditor_id = ?", (new_id,)) == (1,)


def test_get_or_create_card_issuer_rejects_a_blank_name(db):
    import pytest
    with pytest.raises(ValueError):
        app.get_or_create_card_issuer("   ")


def test_payment_is_confirmed_by_the_next_statements_carried_over_balance(db):
    """Statement 1 is entered with the payment unknown; statement 2 shows 300 carried over from it."""
    import app
    issuer_id = _make_card_issuer(db, "Test Bank")
    app.add_credit_card(issuer_id, None, "Test Card", "1111", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE card_number_last4 = '1111'")[0]
    app.add_cc_statement(card_id, date(2026, 10, 10), 0.0, 1000.0, 0.0, 0.0, statement_month="2026-09", paid_confirmed=0)

    pending = app.get_unconfirmed_cc_statement(card_id)
    assert pending and pending["total"] == 1000.0

    paid, status = app.confirm_cc_payment_from_next_statement(pending["id"], 300.0)

    assert (paid, status) == (700.0, "Partially Paid")
    assert app.get_unconfirmed_cc_statement(card_id) is None
    assert fetchone(db, "SELECT amount_paid, unpaid_balance_next, paid_confirmed FROM cc_statements WHERE cc_statement_id = ?", (pending["id"],)) == (700.0, 300.0, 1)


def test_carried_over_zero_means_paid_in_full_and_never_exceeds_the_total(db):
    import app
    issuer_id = _make_card_issuer(db, "Test Bank 2")
    app.add_credit_card(issuer_id, None, "Test Card", "2222", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE card_number_last4 = '2222'")[0]
    app.add_cc_statement(card_id, date(2026, 10, 10), 0.0, 500.0, 0.0, 0.0, paid_confirmed=0)
    sid = app.get_unconfirmed_cc_statement(card_id)["id"]
    assert app.confirm_cc_payment_from_next_statement(sid, 0.0) == (500.0, "Paid")
    app.add_cc_statement(card_id, date(2026, 11, 10), 0.0, 500.0, 0.0, 0.0, paid_confirmed=0)
    sid2 = app.get_unconfirmed_cc_statement(card_id)["id"]
    assert app.confirm_cc_payment_from_next_statement(sid2, 900.0)[0] == 0.0     # more carried over than the total: nothing counted as paid


def test_carried_over_balance_is_not_counted_twice_in_payables(db):
    """300 left unpaid on statement 1 is INSIDE statement 2's 1,500 -- payable must be 1,500, not 1,800."""
    from engines.reports_service import get_combined_ap_summary, get_payment_schedule_all_tracks
    issuer_id = _make_card_issuer(db, "Double Count Bank")
    app.add_credit_card(issuer_id, None, "Test Card", "3333", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE card_number_last4 = '3333'")[0]
    future = date.today().replace(day=1) + pd.Timedelta(days=70)
    app.add_cc_statement(card_id, future - pd.Timedelta(days=30), 0.0, 1000.0, 0.0, 700.0)         # leaves 300 unpaid
    app.add_cc_statement(card_id, future, 300.0, 1200.0, 0.0, 0.0)                                  # total 1,500 includes that 300

    summary = get_combined_ap_summary().set_index("track")["outstanding"]

    assert summary["Credit Card"] == 1500.0
    schedule = get_payment_schedule_all_tracks()
    assert list(schedule[schedule["track"] == "Credit Card"]["amount"]) == [1500.0]


def test_migrated_statements_without_a_carried_balance_still_count_individually(db):
    from engines.reports_service import get_combined_ap_summary
    issuer_id = _make_card_issuer(db, "Legacy Bank")
    app.add_credit_card(issuer_id, None, "Test Card", "4444", 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE card_number_last4 = '4444'")[0]
    today = date.today()
    for days, amount in ((10, 500.0), (40, 800.0)):
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, ?, 'Unpaid')", (card_id, str(today + pd.Timedelta(days=days)), amount))
        conn.commit(); conn.close()
    assert get_combined_ap_summary().set_index("track")["outstanding"]["Credit Card"] == 1300.0


def _card_with_statement(db, last4, total, due_in_days=20, paid=0.0):
    issuer_id = _make_card_issuer(db, f"Pay Bank {last4}")
    app.add_credit_card(issuer_id, None, "Pay Card", last4, 50000.0, None, None)
    card_id = fetchone(db, "SELECT card_id FROM credit_cards WHERE card_number_last4 = ?", (last4,))[0]
    app.add_cc_statement(card_id, date.today() + pd.Timedelta(days=due_in_days), 0.0, total, 0.0, paid, paid_confirmed=0)
    sid = fetchone(db, "SELECT cc_statement_id FROM cc_statements WHERE card_id = ?", (card_id,))[0]
    return card_id, sid


