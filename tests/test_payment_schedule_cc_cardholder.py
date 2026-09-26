"""Credit Card leg of get_payment_schedule_all_tracks()'s "reference" column (2026-09-17) --
mirrors the Bank Loan leg's existing beneficiary-name prefix (e.g. "Emelita Roxas - PENDING-153")
so it's clear at a glance whose card a row belongs to, not just which bank issued it."""
import sqlite3
from datetime import date, timedelta

from conftest import make_creditor
from engines.reports_service import get_payment_schedule_all_tracks


def _make_card(db_path, issuer_creditor_id, cardholder_id, card_name="Gold", last4="1234"):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO credit_cards (creditor_id, cardholder_id, card_name, card_number_last4, credit_limit) VALUES (?, ?, ?, ?, 100000)",
        (issuer_creditor_id, cardholder_id, card_name, last4),
    )
    card_id = cur.lastrowid
    conn.commit()
    conn.close()
    return card_id


def _make_cardholder(db_path, name):
    conn = sqlite3.connect(db_path)
    cur = conn.execute("INSERT INTO cardholders (name) VALUES (?)", (name,))
    cardholder_id = cur.lastrowid
    conn.commit()
    conn.close()
    return cardholder_id


def _make_statement(db_path, card_id, due_date, amount):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, ?, 'Unpaid')",
        (card_id, str(due_date), amount),
    )
    conn.commit()
    conn.close()


def test_cc_reference_prefixed_with_cardholder_name_when_set(db):
    issuer_id = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    holder_id = _make_cardholder(db, "Rhodora Roxas")
    card_id = _make_card(db, issuer_id, holder_id, card_name="Shopmore", last4="4102")
    _make_statement(db, card_id, date.today() + timedelta(days=5), 15000.0)

    df = get_payment_schedule_all_tracks()
    cc_rows = df[df["track"] == "Credit Card"]
    assert len(cc_rows) == 1
    assert cc_rows["reference"].iloc[0] == "Rhodora Roxas - Shopmore - 4102"


def test_cc_reference_falls_back_without_cardholder(db):
    issuer_id = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    card_id = _make_card(db, issuer_id, None, card_name="Shopmore", last4="4102")
    _make_statement(db, card_id, date.today() + timedelta(days=5), 15000.0)

    df = get_payment_schedule_all_tracks()
    cc_rows = df[df["track"] == "Credit Card"]
    assert len(cc_rows) == 1
    assert cc_rows["reference"].iloc[0] == "Shopmore - 4102"
