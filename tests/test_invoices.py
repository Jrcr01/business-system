"""add_invoice() / edit_invoice() / delete_invoice() -- creation, in-place edit
gated behind batch lock, duplicate doc_ref rejection, and the batch-total /
payment-status sync that has to happen after every mutation."""
from datetime import date

import app
from conftest import make_creditor, make_statement, fetchone


def test_add_invoice_computes_net_amount_and_recomputes_batch_total(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)

    net_amount, total_discount, warnings = app.add_invoice(
        creditor_id, "INV-001", date(2026, 1, 15), date(2026, 2, 14), 1000.0, [10.0], statement_id,
    )

    assert net_amount == 900.0
    assert total_discount == 100.0
    assert warnings == []

    row = fetchone(db, "SELECT total_amount FROM order_statements WHERE statement_id = ?", (statement_id,))
    assert row == (900.0,)  # recompute_statement_total() ran


def test_add_invoice_full_discount_zeroes_out_net_amount(db):
    """2026-09-17 fix: calculate_trade_discount()'s `0 < rate < 100` used to exclude exactly 100
    (a full write-off/waiver), silently charging the full gross amount instead of zero."""
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)

    net_amount, total_discount, warnings = app.add_invoice(
        creditor_id, "INV-WAIVED", date(2026, 1, 15), date(2026, 2, 14), 1000.0, [100.0], statement_id,
    )

    assert net_amount == 0.0
    assert total_discount == 1000.0


def test_add_invoice_duplicate_doc_ref_same_supplier_raises_integrity_error(db):
    # Unlike edit_invoice() (which catches this and returns a friendly message),
    # add_invoice() lets sqlite3.IntegrityError propagate -- documenting that
    # asymmetry here rather than assuming a friendly-message path it doesn't have.
    import sqlite3
    import pytest

    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-DUP", date(2026, 1, 1), date(2026, 1, 31), 500.0, [], statement_id)
    with pytest.raises(sqlite3.IntegrityError):
        app.add_invoice(creditor_id, "INV-DUP", date(2026, 1, 2), date(2026, 2, 1), 700.0, [], statement_id)


def test_edit_invoice_updates_fields_and_logs_only_changed_ones(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-002", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-002'")[0]

    ok, msg = app.edit_invoice(invoice_id, "INV-002", date(2026, 1, 1), date(2026, 1, 31), 2000.0, 0.0)

    assert ok
    assert "1 field(s) changed" in msg
    row = fetchone(db, "SELECT amount FROM invoices WHERE invoice_id = ?", (invoice_id,))
    assert row == (2000.0,)


def test_edit_invoice_no_changes_returns_true_with_no_op_message(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-003", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-003'")[0]

    ok, msg = app.edit_invoice(invoice_id, "INV-003", date(2026, 1, 1), date(2026, 1, 31), 1000.0, 0.0)

    assert ok
    assert msg == "No changes to save."


def test_edit_invoice_rejects_blank_doc_ref(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-004", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-004'")[0]

    ok, msg = app.edit_invoice(invoice_id, "   ", date(2026, 1, 1), date(2026, 1, 31), 1000.0, 0.0)

    assert not ok
    assert "Doc Ref" in msg


def test_edit_invoice_rejects_zero_or_negative_amount(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-005", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-005'")[0]

    ok, msg = app.edit_invoice(invoice_id, "INV-005", date(2026, 1, 1), date(2026, 1, 31), 0.0, 0.0)

    assert not ok
    assert "greater than zero" in msg


def test_edit_invoice_blocked_when_batch_locked(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-006", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-006'")[0]

    # Lock is possible here: remaining = net_total(1000) - active_checks(0) = 1000 >= 0.
    ok_lock, _ = app.lock_statement(statement_id)
    assert ok_lock

    ok, msg = app.edit_invoice(invoice_id, "INV-006", date(2026, 1, 1), date(2026, 1, 31), 2000.0, 0.0)

    assert not ok
    assert "locked" in msg.lower()
    row = fetchone(db, "SELECT amount FROM invoices WHERE invoice_id = ?", (invoice_id,))
    assert row == (1000.0,)  # unchanged


def test_delete_invoice_removes_row_and_recomputes_total(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-007", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-007'")[0]

    ok, _ = app.delete_invoice(invoice_id)

    assert ok
    assert fetchone(db, "SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)) is None
    assert fetchone(db, "SELECT total_amount FROM order_statements WHERE statement_id = ?", (statement_id,)) == (0.0,)


def test_delete_invoice_blocked_when_batch_locked(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-008", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-008'")[0]
    app.lock_statement(statement_id)

    ok, msg = app.delete_invoice(invoice_id)

    assert not ok
    assert "locked" in msg.lower()


def test_delete_invoice_blocked_when_check_already_allocated(db):
    """2026-09-17 fix: this used to let the DELETE hit invoice_check_allocations' FK constraint
    as a raw, uncaught sqlite3.IntegrityError instead of a friendly message."""
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-009", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-009'")[0]
    ok, _msg, check_ids = app.issue_checks_grid(creditor_id, statement_id, [{"check_date": date(2026, 1, 10), "amount": 1000.0}])
    assert ok
    app.allocate_check_to_invoices(check_ids[0], [(invoice_id, 1000.0)])

    ok, msg = app.delete_invoice(invoice_id)

    assert not ok
    assert "allocated" in msg.lower()
    assert fetchone(db, "SELECT COUNT(*) FROM invoices WHERE invoice_id = ?", (invoice_id,)) == (1,)
