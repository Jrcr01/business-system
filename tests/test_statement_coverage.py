"""get_statement_coverage() (2026-09-19): month-over-month check for cards whose statement is missing.
Owner rule (2026-09-29): a statement's month is the month of its STATEMENT DATE (engines/cc_dates.py: the card's statement day
10-45 days before the due date; no statement day -> due date minus 21 days), whatever month was typed on it."""
import sqlite3
from datetime import date

from conftest import make_creditor
from engines.cc_service import get_statement_coverage

TODAY = date(2026, 9, 19)


import pytest


@pytest.fixture(autouse=True)
def _flag_from_the_expected_day(db):
    """These tests are about WHICH cards are flagged; the 7-day waiting rule (2026-09-29) has its own file: tests/test_cc_missing_wait.py."""
    from engines.cc_service import set_missing_wait_days

    set_missing_wait_days(0)


def _card(db, issuer_id, name, last4, active=1):
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit, is_active) VALUES (?, ?, ?, 100000, ?)",
        (issuer_id, name, last4, active),
    )
    conn.commit(); cid = cur.lastrowid; conn.close()
    return cid


def _stmt(db, card_id, due, amount=1000.0):
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, ?, 'Unpaid')", (card_id, due, amount))
    conn.commit(); conn.close()


def test_each_status_is_assigned_correctly(db):
    issuer = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    ok = _card(db, issuer, "Ok", "1111");             _stmt(db, ok, "2026-09-10"); _stmt(db, ok, "2026-10-10")   # Aug + Sep statements
    missing_now = _card(db, issuer, "MissNow", "2222"); _stmt(db, missing_now, "2026-09-05")                        # Aug 15 statement only
    missing_both = _card(db, issuer, "MissBoth", "3333"); _stmt(db, missing_both, "2026-08-12")                    # July statement only
    never = _card(db, issuer, "Never", "4444")

    df = get_statement_coverage(TODAY).set_index("card_name")

    assert df.loc["Ok", "status"] == "OK"
    assert df.loc["MissNow", "status"] == "Missing this month"
    assert df.loc["MissBoth", "status"] == "Missing both months"
    assert df.loc["Never", "status"] == "No statement ever"


def test_inactive_cards_are_excluded_and_worst_status_sorts_first(db):
    issuer = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    _card(db, issuer, "Closed", "5555", active=0)
    ok = _card(db, issuer, "Fine", "6666"); _stmt(db, ok, "2026-10-05")
    miss = _card(db, issuer, "Late", "7777"); _stmt(db, miss, "2026-09-05")

    df = get_statement_coverage(TODAY)

    assert "Closed" not in set(df["card_name"])
    assert list(df["card_name"]) == ["Late", "Fine"]


def test_january_looks_back_to_december(db):
    issuer = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    c = _card(db, issuer, "NewYear", "8888"); _stmt(db, c, "2026-01-04")   # the December 14 statement, due Jan 4

    df = get_statement_coverage(date(2026, 1, 15))

    assert df.iloc[0]["status"] == "Missing this month"


def test_card_only_flagged_once_its_statement_should_have_arrived(db):
    """Not on the 1st: a card whose statement normally lands mid/late month is 'Not yet expected' until then (with no Statement
    day set: last month's statement date -- its due date minus 21 days -- on the same day this month)."""
    issuer = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    c = _card(db, issuer, "Late", "9999"); _stmt(db, c, "2026-09-06")   # the Aug 16 statement -> September's expected Sep 16

    early = get_statement_coverage(date(2026, 9, 10)).iloc[0]
    later = get_statement_coverage(date(2026, 9, 18)).iloc[0]

    assert early["status"] == "Not yet expected" and early["expected_by"] == "2026-09-16"
    assert later["status"] == "Missing this month"


def test_statement_day_on_the_card_overrides_the_estimate(db):
    issuer = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    c = _card(db, issuer, "HasDay", "1010"); _stmt(db, c, "2026-09-01")   # with statement day 5: the Aug 5 statement
    conn = sqlite3.connect(db); conn.execute("UPDATE credit_cards SET statement_day = 5 WHERE card_id = ?", (c,)); conn.commit(); conn.close()

    row = get_statement_coverage(date(2026, 9, 6)).iloc[0]

    assert row["expected_by"] == "2026-09-05" and row["status"] == "Missing this month"


def test_the_typed_statement_month_does_not_decide_the_due_date_does(db):
    """Owner, 2026-09-29: most old statements were labelled with their DUE month. A statement due Sep 3 is the August statement,
    whatever month was typed on it."""
    issuer = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    c = _card(db, issuer, "Labelled", "2020")
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO cc_statements (card_id, statement_month, due_date, total_amount_due, status) VALUES (?, '2026-09', '2026-09-03', 500, 'Paid')", (c,))
    conn.commit(); conn.close()

    row = get_statement_coverage(date(2026, 9, 20)).iloc[0]

    assert row["status"] == "Missing this month"   # August on file (due Sep 3); September's statement (due in October) not yet
    assert row["prev_month_due"] == "2026-09-03"
