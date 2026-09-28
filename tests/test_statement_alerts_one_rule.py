"""One rule for "a card statement may be missing" (owner, 2026-09-29): the Payment run / Dashboard / Credit cards warning
(get_statement_gap_alerts) says exactly what the Credit cards coverage table (get_statement_coverage) says -- a card is flagged
when its statement is "Missing this month" (from its Expected-by day) or "Missing both months"; "No statement ever" stays the
quiet note. Real case (2026-09-29): the old 35-days-since-due-date warning and the table disagreed; both now follow the table, which
works a statement's month out from the card's statement date (engines/cc_dates.py)."""
import sqlite3
from datetime import date

from conftest import make_creditor
from engines.cc_service import get_statement_coverage, get_statement_gap_alerts

TODAY = date(2026, 9, 29)


def _card(db, issuer_id, name, last4, statement_day=None):
    with sqlite3.connect(db) as conn:
        return conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit, is_active, statement_day) "
                            "VALUES (?, ?, ?, 100000, 1, ?)", (issuer_id, name, last4, statement_day)).lastrowid


def _stmt(db, card_id, due, month=None):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status, statement_month) VALUES (?, ?, 1000, 'Unpaid', ?)",
                     (card_id, due, month))


def _flagged(today):
    g = get_statement_gap_alerts(today)
    return set(g.loc[g["alert"] == "Possibly missing a statement", "card_name"]) if not g.empty else set()


def test_the_warning_flags_exactly_what_the_table_flags(db):
    bank = make_creditor(db, name="RCBC", creditor_type="CREDIT_CARD")
    gold = _card(db, bank, "Gold", "0041", statement_day=3)
    _stmt(db, gold, "2026-09-28", month="2026-07")                  # labelled July, but statement day 3 + due Sep 28 = the Sep 3 statement
    ok = _card(db, bank, "Ok", "1111")
    _stmt(db, ok, "2026-09-10"); _stmt(db, ok, "2026-10-10")          # Aug + Sep statements
    late = _card(db, bank, "Late", "2222", statement_day=11)
    _stmt(db, late, "2026-08-03")                                   # only the July statement (due Aug 3)
    _card(db, bank, "Never", "3333")
    cov = get_statement_coverage(TODAY)
    table_flags = set(cov.loc[cov["status"].isin(["Missing this month", "Missing both months"]), "card_name"])
    assert table_flags == {"Late"}                                  # Gold is OK: its September statement is in
    assert _flagged(TODAY) == table_flags                           # same rule, same cards
    g = get_statement_gap_alerts(TODAY).set_index("card_name")
    assert g.loc["Never", "alert"] == "No statement on file yet"    # stays the quiet note


def test_the_warning_waits_for_the_expected_by_day(db):
    bank = make_creditor(db, name="BDO", creditor_type="CREDIT_CARD")
    card = _card(db, bank, "Shopmore", "0007", statement_day=25)
    _stmt(db, card, "2026-09-20")                                   # August statement (due Sep 20); September's expected by Sep 25
    assert _flagged(date(2026, 9, 20)) == set()                     # not yet expected
    assert _flagged(date(2026, 9, 26)) == {"Shopmore"}              # overdue now


def test_no_due_this_month_stops_the_warning_and_can_be_undone(db):
    """Owner, 2026-09-29: "there are also times that recording statement is not necessary because it has no due so I opt not to
    record this" -> one click marks a card's month as 'No due' (no statement, nothing added to payables); undo brings the warning back."""
    from engines.cc_service import mark_no_due, no_due_marks, undo_no_due

    bank = make_creditor(db, name="PNB", creditor_type="CREDIT_CARD")
    quiet = _card(db, bank, "Quiet", "4444")
    _stmt(db, quiet, "2026-09-05")                                  # the Aug 15 statement; September's expected Sep 15 -> missing on Sep 29
    both = _card(db, bank, "Both", "5555")
    _stmt(db, both, "2026-08-05")                                   # the Jul 15 statement; nothing for August or September
    assert _flagged(TODAY) == {"Quiet", "Both"}

    mark_no_due(quiet, "2026-09", "no charges this month")
    mark_no_due(both, "2026-08"); mark_no_due(both, "2026-09")
    assert _flagged(TODAY) == set()
    cov = get_statement_coverage(TODAY).set_index("card_name")
    assert cov.loc["Quiet", "status"] == "No due" and cov.loc["Both", "status"] == "No due"
    assert (quiet, "2026-09") in no_due_marks()

    undo_no_due(both, "2026-09")                                    # August stays 'No due' -> only this month is missing
    assert get_statement_coverage(TODAY).set_index("card_name").loc["Both", "status"] == "Missing this month"
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE user_action IN ('CC_NO_DUE_MARKED', 'CC_NO_DUE_UNDONE')").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM cc_statements WHERE card_id IN (?, ?)", (quiet, both)).fetchone()[0] == 2   # nothing added
