"""Bulk assign in sequence (2026-09-27): consecutive free booklet leaves, in Chrysanth print order, one all-or-nothing transaction."""
from datetime import date

import pandas as pd

import app
from conftest import make_creditor, make_statement, fetchone
from engines.check_run_service import sort_by_print_order

BANK = 1


def _checks(db, n):
    import sqlite3
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 1000.0 * n, [], statement_id)
    ids = []
    for i in range(n):
        _, _, cid = app.issue_check(creditor_id, 1000.0, date(2026, 3, 1 + i), statement_id=statement_id)
        ids.append(cid)
    return ids


def _booklet(db, start=3992554301, end=3992554310):
    ok, msg = app.create_booklet(BANK, "B1", start, end, date(2026, 1, 1))
    assert ok, msg
    return fetchone(db, "SELECT booklet_id FROM check_booklets WHERE booklet_code = 'B1'")[0]


def test_plan_takes_consecutive_free_leaves_and_steps_over_spoiled_ones(db):
    bid = _booklet(db)
    ids = _checks(db, 3)
    assert app.log_spoiled_check("3992554302", "jam")[0]

    plan, skipped, err = app.plan_sequential_numbers(bid, None, ids)

    assert err is None
    assert [leaf for _, leaf in plan] == [3992554301, 3992554303, 3992554304]      # 302 is spoiled, so it is skipped
    assert skipped == [3992554302]


def test_plan_can_start_further_in_and_refuses_when_the_booklet_runs_out(db):
    bid = _booklet(db, end=3992554303)
    ids = _checks(db, 3)

    plan, skipped, err = app.plan_sequential_numbers(bid, 3992554302, ids)

    assert plan == [] and "Only 2 free leaf" in err


def test_bulk_assign_writes_every_number_and_moves_the_booklet_pointer(db):
    bid = _booklet(db)
    ids = _checks(db, 3)
    plan, _, _ = app.plan_sequential_numbers(bid, None, ids)

    ok, msg, done = app.assign_check_numbers_from_booklet(plan, bid)

    assert ok and len(done) == 3
    assert [fetchone(db, "SELECT check_number FROM checks_issued WHERE check_id = ?", (i,))[0] for i in ids] == ["3992554301", "3992554302", "3992554303"]
    assert fetchone(db, "SELECT next_available_number FROM check_booklets WHERE booklet_id = ?", (bid,))[0] == 3992554304
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_NUMBER_ASSIGNED'")[0] == 3


def test_bulk_assign_is_all_or_nothing_if_a_leaf_was_taken_after_the_preview(db):
    bid = _booklet(db)
    ids = _checks(db, 3)
    plan, _, _ = app.plan_sequential_numbers(bid, None, ids)
    assert app.log_spoiled_check("3992554302", "jam")[0]          # someone spoils a leaf between the preview and the confirm

    ok, msg, done = app.assign_check_numbers_from_booklet(plan, bid)

    assert not ok and "Nothing was assigned" in msg and done == []
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE check_number IS NOT NULL")[0] == 0     # even the first leaf was rolled back


def test_print_order_puts_the_lower_order_statement_first():
    df = pd.DataFrame([
        {"check_id": 9, "check_number": None, "reference": "2026 - OS 0012", "statement_id": 12, "loan_note_id": None, "print_batch_id": None, "export_placeholder": None},
        {"check_id": 4, "check_number": None, "reference": "2026 - OS 0003", "statement_id": 3, "loan_note_id": None, "print_batch_id": None, "export_placeholder": None},
        {"check_id": 2, "check_number": None, "reference": "2026 - LN 0001", "statement_id": None, "loan_note_id": 1, "print_batch_id": None, "export_placeholder": None},
        {"check_id": 7, "check_number": "3992554301", "reference": "2026 - OS 0099", "statement_id": 99, "loan_note_id": None, "print_batch_id": None, "export_placeholder": None},
    ])

    ordered = list(sort_by_print_order(df)["check_id"])

    assert ordered == [7, 4, 9, 2]          # already numbered first, then OS 3, OS 12, lender last
