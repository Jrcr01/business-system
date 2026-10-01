"""A card is flagged as missing a statement only after a waiting period past its expected-by day (owner, 2026-09-29: "i want the trigger of
missing statement to be 7 days"). Default 7, adjustable; the same rule for the coverage table and the warnings (Payment run, Dashboard,
Credit cards)."""
import sqlite3
from datetime import date

from conftest import make_creditor
from engines.cc_service import get_statement_coverage, get_statement_gap_alerts, missing_wait_days, set_missing_wait_days


def _card(db, issuer_id, name, last4, statement_day=None):
    with sqlite3.connect(db) as conn:
        return conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit, is_active, statement_day) "
                            "VALUES (?, ?, ?, 100000, 1, ?)", (issuer_id, name, last4, statement_day)).lastrowid


def _stmt(db, card_id, due):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, 1000, 'Unpaid')", (card_id, due))


def _flagged(today):
    g = get_statement_gap_alerts(today)
    return set(g.loc[g["alert"] == "Possibly missing a statement", "card_name"]) if not g.empty else set()


def test_a_card_is_flagged_only_after_the_waiting_days(db):
    """Owner, 2026-09-29: "i want the trigger of missing statement to be 7 days" -> a card shows as missing only when its statement is still
    not recorded 7 days after the expected-by date (default 7, adjustable). Example: expected Sep 5 -> flagged from Sep 12."""
    bank = make_creditor(db, name="Wait Bank", creditor_type="CREDIT_CARD")
    c = _card(db, bank, "Wait", "3030", statement_day=5)
    _stmt(db, c, "2026-09-01")                                      # the Aug 5 statement; September's is expected by Sep 5
    assert missing_wait_days() == 7
    assert get_statement_coverage(date(2026, 9, 5)).iloc[0]["status"] == "Not yet expected"
    assert get_statement_coverage(date(2026, 9, 11)).iloc[0]["status"] == "Not yet expected"
    assert get_statement_coverage(date(2026, 9, 12)).iloc[0]["status"] == "Missing this month"
    assert _flagged(date(2026, 9, 11)) == set() and _flagged(date(2026, 9, 12)) == {"Wait"}      # the warning follows the same rule

    set_missing_wait_days(0)                                        # flag from the expected day itself
    assert missing_wait_days() == 0 and _flagged(date(2026, 9, 5)) == {"Wait"}
    set_missing_wait_days(14)
    assert _flagged(date(2026, 9, 12)) == set() and _flagged(date(2026, 9, 19)) == {"Wait"}
