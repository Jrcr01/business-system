"""Leaves used but never recorded (owner, 2026-09-30/10-01: the owner's mother hand-writes urgent checks from her own booklet and sends photos
on Messenger; a forgotten photo means a check missing from the system until the bank clears it). The booklet audit only saw gaps BELOW the highest
recorded leaf; now the owner can also say "her booklet is up to no. ___", and a leaf the bank already cleared (or that is logged spoiled / voided)
is accounted for, so only real unknowns are listed."""
import sqlite3
from datetime import date

import app
from conftest import fetchone, make_creditor, make_statement
from engines.checkbook_service import compress_leaf_ranges, set_reported_up_to, unrecorded_alerts, unrecorded_leaves

START, END = 3992554201, 3992554300


def _booklet(db, code="Mom - urgent", start=START, end=END):
    assert app.create_booklet(1, code, start, end, date(2026, 1, 1))[0]
    return fetchone(db, "SELECT booklet_id FROM check_booklets WHERE booklet_code = ?", (code,))[0]


def _check(db, number, status="Active"):
    creditor_id = make_creditor(db, name=f"Payee {number}")
    statement_id = make_statement(db, creditor_id)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE order_statements SET statement_ref = ? WHERE statement_id = ?", (f"T-{number}", statement_id))
        conn.execute("INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) VALUES (1, ?, ?, ?, 100, '2026-12-01', ?)",
                     (statement_id, creditor_id, str(number), status))


def _bank_line(db, number, day="2026-09-10"):
    with sqlite3.connect(db) as conn:
        imp = conn.execute("SELECT import_id FROM bank_statement_imports").fetchone()
        if not imp:
            conn.execute("INSERT INTO bank_statement_imports (file_name, file_hash, period_from, period_to, line_count) VALUES ('t.csv', 'h', '2026-09-01', '2026-09-30', 1)")
            imp = conn.execute("SELECT import_id FROM bank_statement_imports").fetchone()
        conn.execute("INSERT INTO bank_transactions (import_id, txn_date, posting_date, check_number, description, debit, kind, match_status, line_key) "
                     "VALUES (?, ?, ?, ?, 'IM IN- CLRNG', 100, 'CHECK', 'not_in_system', ?)", (imp[0], day, day, number, f"k{number}"))


def test_nothing_is_flagged_until_a_leaf_is_known_to_be_used(db):
    booklet_id = _booklet(db)
    assert unrecorded_leaves(booklet_id) == []
    assert unrecorded_alerts().empty


def test_leaves_below_the_highest_recorded_one_are_listed_unless_accounted_for(db):
    booklet_id = _booklet(db)
    _check(db, 3992554297)                                   # highest recorded
    _check(db, 3992554296, "Cancelled")                      # a voided check is recorded too
    assert app.log_spoiled_check("3992554295", "jam")[0]
    _bank_line(db, "3992554294")                             # cleared by the bank (10-digit form)
    _bank_line(db, "2554293")                                # cleared by the bank (7-digit form of 3992554293)

    unrecorded = unrecorded_leaves(booklet_id)

    assert 3992554297 not in unrecorded and 3992554296 not in unrecorded and 3992554295 not in unrecorded
    assert 3992554294 not in unrecorded and 3992554293 not in unrecorded
    assert set(unrecorded) == set(range(START, 3992554298)) - {3992554297, 3992554296, 3992554295, 3992554294, 3992554293}


def test_saying_the_booklet_is_up_to_a_number_lists_the_leaves_after_the_last_recorded(db):
    booklet_id = _booklet(db)
    _check(db, 3992554297)
    ok, msg = set_reported_up_to(booklet_id, 3992554299)
    assert ok, msg

    unrecorded = unrecorded_leaves(booklet_id)

    assert 3992554298 in unrecorded and 3992554299 in unrecorded and 3992554300 not in unrecorded
    assert "3992554299" in fetchone(db, "SELECT new_values FROM audit_logs WHERE user_action = 'BOOKLET_REPORTED_UP_TO'")[0]


def test_recording_a_listed_leaf_takes_it_off_the_list(db):
    booklet_id = _booklet(db)
    _check(db, 3992554297)
    set_reported_up_to(booklet_id, 3992554299)
    assert 3992554298 in unrecorded_leaves(booklet_id)

    _check(db, 3992554298)

    assert 3992554298 not in unrecorded_leaves(booklet_id)


def test_the_reported_number_must_be_inside_the_booklet(db):
    booklet_id = _booklet(db)
    assert not set_reported_up_to(booklet_id, 3992554100)[0]
    assert not set_reported_up_to(booklet_id, 3992554301)[0]
    assert not set_reported_up_to(9999, 3992554250)[0]
    assert set_reported_up_to(booklet_id, 3992554250)[0]


def test_the_alert_lists_each_booklet_with_unrecorded_leaves_in_short_ranges(db):
    booklet_id = _booklet(db)
    _check(db, 3992554297)
    set_reported_up_to(booklet_id, 3992554299)
    _booklet(db, "Mine - bulk", 3992554301, 3992554400)           # nothing known used -> not in the alert

    alerts = unrecorded_alerts()

    assert list(alerts["booklet"]) == ["Mom - urgent"]
    row = alerts.iloc[0]
    assert row["count"] == 98 and row["up_to"] == 3992554299
    assert row["leaves"].startswith("3992554201-3992554296, 3992554298-3992554299")


def test_compress_leaf_ranges():
    assert compress_leaf_ranges([]) == ""
    assert compress_leaf_ranges([5]) == "5"
    assert compress_leaf_ranges([1, 2, 3, 7, 9, 10]) == "1-3, 7, 9-10"
