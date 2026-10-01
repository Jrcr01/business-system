"""Per-invoice check allocation (2026-09-14, migration 025) -- which specific
invoice(s) a check pays, and how much of each. Covers the waterfall auto-fill
preview, the actual allocation write path, the resulting per-invoice
Paid/Partially Paid/Unpaid status, and the legacy fallback for a batch that
has never had an allocated check (still the old blanket batch-level status)."""
from datetime import date

import app
from conftest import make_creditor, make_statement, fetchone


def test_waterfall_preview_splits_one_check_across_two_invoices():
    import pandas as pd

    open_df = pd.DataFrame([
        {"invoice_id": 1, "doc_ref": "INV-1", "due_date": "2026-01-31", "amount": 10000.0, "remaining": 10000.0},
        {"invoice_id": 2, "doc_ref": "INV-2", "due_date": "2026-02-04", "amount": 5000.0, "remaining": 5000.0},
    ])
    rows = [{"check_date": date(2026, 1, 10), "amount": 12000.0}]

    preview = app.waterfall_allocate_preview(rows, open_df)

    assert list(preview["invoice_id"]) == [1, 2]
    assert list(preview["allocated"]) == [10000.0, 2000.0]
    assert preview["allocated"].sum() == 12000.0


def test_waterfall_preview_spans_multiple_checks_when_one_check_isnt_enough():
    import pandas as pd

    open_df = pd.DataFrame([
        {"invoice_id": 1, "doc_ref": "INV-1", "due_date": "2026-01-31", "amount": 10000.0, "remaining": 10000.0},
    ])
    rows = [
        {"check_date": date(2026, 1, 10), "amount": 6000.0},
        {"check_date": date(2026, 1, 20), "amount": 4000.0},
    ]

    preview = app.waterfall_allocate_preview(rows, open_df)

    assert list(preview["check_row"]) == [1, 2]
    assert list(preview["allocated"]) == [6000.0, 4000.0]


def test_full_flow_issue_allocate_sync_gives_per_invoice_status(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 10000.0, [], statement_id)
    app.add_invoice(creditor_id, "INV-2", date(2026, 1, 5), date(2026, 2, 4), 5000.0, [], statement_id)

    open_df = app.get_invoices_with_remaining(statement_id)
    rows = [{"check_date": date(2026, 1, 10), "amount": 12000.0}]  # in the past -> clears
    preview = app.waterfall_allocate_preview(rows, open_df)

    ok, _msg, check_ids = app.issue_checks_grid(creditor_id, statement_id, rows)
    assert ok

    alloc_ok, _alloc_msg = app.allocate_check_to_invoices(
        check_ids[0], [(int(r["invoice_id"]), float(r["allocated"])) for _, r in preview.iterrows()]
    )
    assert alloc_ok

    app.sync_invoice_payment_status(statement_id)

    inv1_status = fetchone(db, "SELECT status FROM invoices WHERE doc_ref = 'INV-1'")[0]
    inv2_status = fetchone(db, "SELECT status FROM invoices WHERE doc_ref = 'INV-2'")[0]
    assert inv1_status == "Paid"  # fully covered (10000 of 10000)
    assert inv2_status == "Partially Paid"  # only 2000 of 5000 covered

    remaining_after = app.get_invoices_with_remaining(statement_id)
    assert len(remaining_after) == 1
    assert remaining_after.iloc[0]["doc_ref"] == "INV-2"
    assert remaining_after.iloc[0]["remaining"] == 3000.0


def test_legacy_batch_with_no_allocations_keeps_blanket_status(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "LEG-1", date(2026, 1, 1), date(2026, 1, 31), 8000.0, [], statement_id)
    app.add_invoice(creditor_id, "LEG-2", date(2026, 1, 5), date(2026, 2, 4), 2000.0, [], statement_id)

    # A check issued the old way -- no allocation row at all.
    ok, _msg, _check_ids = app.issue_checks_grid(creditor_id, statement_id, [{"check_date": date(2026, 1, 10), "amount": 5000.0}])
    assert ok

    app.sync_invoice_payment_status(statement_id)

    # Old blanket behavior: every invoice in the batch gets the SAME status, based on the
    # batch's total cleared checks (5000) vs its net total (10000) -- neither invoice is
    # individually distinguished, unlike the per-invoice path above.
    leg1_status = fetchone(db, "SELECT status FROM invoices WHERE doc_ref = 'LEG-1'")[0]
    leg2_status = fetchone(db, "SELECT status FROM invoices WHERE doc_ref = 'LEG-2'")[0]
    assert leg1_status == "Partially Paid"
    assert leg2_status == "Partially Paid"


def test_allocate_check_to_invoices_rejects_over_allocation(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-X", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    ok, _msg, check_ids = app.issue_checks_grid(creditor_id, statement_id, [{"check_date": date(2026, 1, 10), "amount": 1000.0}])
    assert ok
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-X'")[0]

    alloc_ok, msg = app.allocate_check_to_invoices(check_ids[0], [(invoice_id, 1500.0)])

    assert not alloc_ok
    assert "remaining" in msg.lower()
    # Nothing should have been written on a rejected allocation.
    assert fetchone(db, "SELECT COUNT(*) FROM invoice_check_allocations") == (0,)


def test_get_invoices_with_remaining_excludes_cancelled_and_fully_allocated(db):
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-CANCELLED", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    app.add_invoice(creditor_id, "INV-OPEN", date(2026, 1, 1), date(2026, 1, 31), 500.0, [], statement_id)
    cancelled_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-CANCELLED'")[0]

    with app.get_conn() as conn:
        conn.execute("UPDATE invoices SET status = 'Cancelled' WHERE invoice_id = ?", (cancelled_id,))
        conn.commit()

    open_df = app.get_invoices_with_remaining(statement_id)

    assert list(open_df["doc_ref"]) == ["INV-OPEN"]


def test_cancel_check_releases_its_invoice_allocation(db):
    """2026-09-17 fix: cancelling an allocated check used to leave its
    invoice_check_allocations row in place -- the invoice stayed permanently "paid" by a check
    that no longer existed, and that slice of it could never be allocated to anything else."""
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-1'")[0]
    ok, _msg, check_ids = app.issue_checks_grid(creditor_id, statement_id, [{"check_date": date(2026, 1, 10), "amount": 1000.0}])
    assert ok
    app.allocate_check_to_invoices(check_ids[0], [(invoice_id, 1000.0)])

    ok, msg = app.cancel_check(check_ids[0])

    assert ok
    assert fetchone(db, "SELECT COUNT(*) FROM invoice_check_allocations WHERE check_id = ?", (check_ids[0],)) == (0,)
    remaining_after = app.get_invoices_with_remaining(statement_id)
    assert len(remaining_after) == 1
    assert remaining_after.iloc[0]["remaining"] == 1000.0


def test_cancel_check_rejects_cancelled_batch(db):
    """2026-09-17 fix: cancel_check() had no batch guard at all. Since 2026-10-01 a batch that is merely locked (fully covered) reopens by itself
    when a check is voided (tests/test_auto_lock.py); a CANCELLED batch stays closed for good."""
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    ok, _msg, check_ids = app.issue_checks_grid(creditor_id, statement_id, [{"check_date": date(2026, 1, 10), "amount": 1000.0}])
    assert ok
    with app.get_conn() as conn:
        conn.execute("UPDATE order_statements SET status = 'Cancelled', is_locked = 1 WHERE statement_id = ?", (statement_id,))
        conn.commit()

    ok, msg = app.cancel_check(check_ids[0])

    assert not ok
    assert "cancelled" in msg.lower()
    assert fetchone(db, "SELECT status FROM checks_issued WHERE check_id = ?", (check_ids[0],)) == ("Active",)


def test_replace_check_retargets_its_invoice_allocation(db):
    """2026-09-17 fix: replacing a check used to leave its invoice_check_allocations row
    pointing at the now-Replaced old check_id instead of moving to the new one."""
    creditor_id = make_creditor(db)
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 1000.0, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE doc_ref = 'INV-1'")[0]
    ok, _msg, check_ids = app.issue_checks_grid(creditor_id, statement_id, [{"check_date": date(2026, 1, 10), "amount": 1000.0}])
    assert ok
    app.allocate_check_to_invoices(check_ids[0], [(invoice_id, 1000.0)])

    ok, msg, new_check_id = app.replace_check(check_ids[0], date(2026, 1, 15), "postdated")

    assert ok
    row = fetchone(db, "SELECT check_id FROM invoice_check_allocations WHERE invoice_id = ?", (invoice_id,))
    assert row == (new_check_id,)
