"""Un-park (2026-09-26): take parked, unnumbered, unprinted checks back out of a batch -- deleted completely with their invoice links,
and an Issued batch that ends up with no active checks goes back to Draft keeping its permanent OS number."""
from datetime import date

import app
from conftest import make_creditor, make_statement, fetchone
from engines.db_engine import promote_draft_statement


def _park(db, amounts):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), sum(amounts), [], statement_id)
    inv_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE statement_id = ?", (statement_id,))[0]
    ids = []
    for i, amt in enumerate(amounts):
        _, _, check_id = app.issue_check(creditor_id, amt, date(2026, 3, 1 + i), statement_id=statement_id)
        assert app.allocate_check_to_invoices(check_id, [(inv_id, amt)])[0]
        ids.append(check_id)
    return creditor_id, statement_id, ids


def test_unpark_deletes_checks_and_their_allocations_and_frees_the_balance(db):
    creditor_id, statement_id, ids = _park(db, [4000.0, 6000.0])

    ok, msg = app.unpark_checks(statement_id, [ids[0]])

    assert ok, msg
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE statement_id = ?", (statement_id,))[0] == 1
    assert fetchone(db, "SELECT COUNT(*) FROM invoice_check_allocations WHERE check_id = ?", (ids[0],))[0] == 0
    _, active, remaining = app.get_batch_balance(statement_id)
    assert (active, remaining) == (6000.0, 4000.0)
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_UNPARKED'")[0] == 1


def test_unpark_refuses_numbered_or_foreign_checks_and_changes_nothing(db):
    creditor_id, statement_id, ids = _park(db, [4000.0, 6000.0])
    app.assign_check_number(ids[1], None, None, "3992495984")
    ok, msg = app.unpark_checks(statement_id, [ids[0], ids[1]])
    assert not ok and "already has the number" in msg
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE statement_id = ?", (statement_id,))[0] == 2   # all-or-nothing

    ok, msg = app.unpark_checks(statement_id, [99999])
    assert not ok and "isn't part of this batch" in msg


def test_unnumbered_check_already_exported_can_still_be_unparked_and_edited(db):
    """2026-09-27: 'as long as it does not have a check number assigned then it is not final' -- exporting to Chrysanth stamps
    printed_at, but that no longer freezes a check that still has no number."""
    from engines.check_run_service import create_check_print_batch
    creditor_id, statement_id, ids = _park(db, [4000.0, 6000.0])
    create_check_print_batch(ids)                                    # exported: printed_at + print_batch_id set, still no number

    ok, msg = app.edit_parked_check(ids[0], 3500.0, date(2026, 4, 1))
    assert ok, msg
    assert fetchone(db, "SELECT amount, check_date FROM checks_issued WHERE check_id = ?", (ids[0],)) == (3500.0, "2026-04-01")

    ok, msg = app.unpark_checks(statement_id, [ids[0]])
    assert ok and "exported to Chrysanth" in msg
    assert fetchone(db, "SELECT COUNT(*) FROM checks_issued WHERE check_id = ?", (ids[0],))[0] == 0
    count, total = fetchone(db, "SELECT check_count, total_amount FROM check_print_batches")
    assert (count, total) == (1, 6000.0)                             # the export history stays honest

    app.assign_check_number(ids[1], None, None, "3992495985")        # once numbered it is final
    ok, msg = app.edit_parked_check(ids[1], 5000.0, date(2026, 4, 2))
    assert not ok and "already has a check number" in msg


def test_issued_batch_goes_back_to_draft_and_keeps_its_number_when_emptied(db):
    creditor_id, statement_id, ids = _park(db, [10000.0])
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute("UPDATE order_statements SET statement_ref = 'DRAFT-26-09-01' WHERE statement_id = ?", (statement_id,))
    conn.commit()
    conn.close()
    ref = promote_draft_statement(statement_id, "Issued")
    assert ref.startswith(f"{date.today().year} - OS ")

    ok, msg = app.unpark_checks(statement_id, ids)

    assert ok and "Draft again" in msg
    assert fetchone(db, "SELECT status, statement_ref FROM order_statements WHERE statement_id = ?", (statement_id,)) == ("Draft", ref)
    # parking again and promoting again must NOT burn a new OS number
    assert promote_draft_statement(statement_id, "Issued") == ref
