"""Credit Memo Deduction Guard (2026-09-14, migration 026) -- a batch with credit
memos silently reduces its net total (v_batch_net_totals), so issuing checks is
gated behind an explicit confirm (or removing the memo) once one exists and
hasn't been reviewed yet. Covers get_batch_memo_status()/confirm_credit_memos(),
the reset-on-add/reset-on-amount-edit behavior, and the zero-memo passthrough."""
from datetime import date

import app
from conftest import make_creditor, make_statement, fetchone


def test_guard_blocks_when_memos_unconfirmed(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-001", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    app.add_credit_memo(statement_id, "CM-001", date(2026, 1, 2), 100.0, "Damaged goods")

    status = app.get_batch_memo_status(statement_id)

    assert status["unapplied_count"] == 1
    assert status["total_amount"] == 100.0
    assert status["confirmed"] is False
    blocking = status["unapplied_count"] > 0 and not status["confirmed"]
    assert blocking is True


def test_guard_allows_after_confirm(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-002", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    app.add_credit_memo(statement_id, "CM-002", date(2026, 1, 2), 100.0, "Discount")

    ok = app.confirm_credit_memos(statement_id)
    status = app.get_batch_memo_status(statement_id)

    assert ok is True
    assert status["confirmed"] is True
    blocking = status["unapplied_count"] > 0 and not status["confirmed"]
    assert blocking is False


def test_guard_resets_when_new_memo_added(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-003", date(2026, 1, 1), date(2026, 1, 31), 2000.0, [], statement_id)
    app.add_credit_memo(statement_id, "CM-003a", date(2026, 1, 2), 100.0, "First memo")
    app.confirm_credit_memos(statement_id)
    assert app.get_batch_memo_status(statement_id)["confirmed"] is True

    app.add_credit_memo(statement_id, "CM-003b", date(2026, 1, 3), 50.0, "Second memo")

    status = app.get_batch_memo_status(statement_id)
    assert status["unapplied_count"] == 2
    assert status["total_amount"] == 150.0
    assert status["confirmed"] is False  # a new memo re-opens the guard, even though it was confirmed before


def test_guard_passes_when_no_memos(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-004", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)

    status = app.get_batch_memo_status(statement_id)

    assert status["unapplied_count"] == 0
    blocking = status["unapplied_count"] > 0 and not status["confirmed"]
    assert blocking is False  # nothing to confirm, regardless of the raw confirmed flag


def test_net_total_includes_memo_deduction(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-005", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    app.add_credit_memo(statement_id, "CM-005", date(2026, 1, 2), 300.0, "Return")

    net_total, active_sum, remaining = app.get_batch_balance(statement_id)

    assert net_total == 700.0  # 1000 gross - 300 credit memo
    assert remaining == 700.0  # no checks issued yet


def test_delete_last_memo_clears_the_guard(db):
    """Not one of the 5 requested tests, but the exact "re-evaluate" behavior
    delete_credit_memo() was asked to add -- worth locking in since it's easy
    to regress silently."""
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-006", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    app.add_credit_memo(statement_id, "CM-006", date(2026, 1, 2), 100.0, "Will be removed")
    memo_id = fetchone(db, "SELECT credit_memo_id FROM credit_memos WHERE memo_ref = 'CM-006'")[0]
    assert app.get_batch_memo_status(statement_id)["confirmed"] is False

    ok, _msg = app.delete_credit_memo(memo_id)

    assert ok
    status = app.get_batch_memo_status(statement_id)
    assert status["unapplied_count"] == 0
    assert status["confirmed"] is True
