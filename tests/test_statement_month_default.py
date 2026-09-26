"""get_default_statement_month(): pre-selects each card's next statement month, rolling December to January."""
import sqlite3
from datetime import date

import app
from conftest import make_creditor


def _card(db):
    issuer = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    ok, _ = app.add_credit_card(issuer, None, "Gold", "1234", 50000.0, None, None)
    conn = sqlite3.connect(db); cid = conn.execute("SELECT card_id FROM credit_cards").fetchone()[0]; conn.close()
    return cid


def test_card_with_no_statements_defaults_to_this_month(db):
    cid = _card(db)
    today = date.today()
    assert app.get_default_statement_month(cid) == (today.year, today.month)


def test_defaults_to_the_month_after_the_last_recorded_one(db):
    cid = _card(db)
    app.add_cc_statement(cid, date(2026, 9, 20), 0, 1000, 0, 0, statement_month="2026-08")
    assert app.get_default_statement_month(cid) == (2026, 9)


def test_december_rolls_over_to_january_of_next_year(db):
    cid = _card(db)
    app.add_cc_statement(cid, date(2027, 1, 5), 0, 1000, 0, 0, statement_month="2026-12")
    assert app.get_default_statement_month(cid) == (2027, 1)


def test_older_statement_without_a_month_falls_back_to_its_due_date_month(db):
    cid = _card(db)
    app.add_cc_statement(cid, date(2026, 12, 15), 0, 1000, 0, 0)
    assert app.get_default_statement_month(cid) == (2027, 1)
