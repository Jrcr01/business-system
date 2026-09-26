"""Chrysanth never remembers what it printed, so an export run can be marked printed here (2026-09-27)."""
from datetime import date

import app
from conftest import make_creditor, make_statement, fetchone
from engines.check_run_service import (
    create_check_print_batch, get_check_print_batches, get_unprinted_batches, mark_batch_printed,
)


def _exported_run(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 5000.0, [], statement_id)
    _, _, check_id = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    return create_check_print_batch([check_id])


def test_a_new_run_is_not_printed_until_marked_and_the_mark_can_be_undone(db):
    run_id = _exported_run(db)
    assert list(get_unprinted_batches()["print_batch_id"]) == [run_id]
    assert get_unprinted_batches()["tag"].iloc[0] == "RUN 001"

    ok, msg = mark_batch_printed(run_id, True)
    assert ok and "RUN 001 marked as printed" in msg
    assert get_unprinted_batches().empty
    assert str(get_check_print_batches()["printed_confirmed_at"].iloc[0]) not in ("None", "nan", "")
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BATCH_MARKED_PRINTED'")[0] == 1

    ok, msg = mark_batch_printed(run_id, False)
    assert ok and "no longer marked" in msg
    assert list(get_unprinted_batches()["print_batch_id"]) == [run_id]


def test_marking_an_unknown_run_is_refused(db):
    ok, msg = mark_batch_printed(999, True)
    assert not ok and "not found" in msg
