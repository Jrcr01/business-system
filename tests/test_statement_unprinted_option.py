"""Bulk statements can leave out checks that are not printed yet -- printed = its Chrysanth run is marked printed (2026-09-27)."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import make_creditor, make_statement, fetchone
from engines.batch_statement_service import _fetch_batch_data, bulk_summary_rows
from engines.check_run_service import create_check_print_batch, mark_batch_printed


def _three_checks(db):
    """check 0: in run A, marked printed; check 1: in run B, exported but NOT marked printed (like RUN 005); check 2: only parked."""
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 15000.0, [], statement_id)
    invoice_id = fetchone(db, "SELECT invoice_id FROM invoices WHERE statement_id = ?", (statement_id,))[0]
    ids = []
    for i in range(3):
        _, _, cid = app.issue_check(creditor_id, 5000.0, date.today() + timedelta(days=30 + i), statement_id=statement_id)
        assert app.allocate_check_to_invoices(cid, [(invoice_id, 5000.0)])[0]
        ids.append(cid)
    run_a = create_check_print_batch([ids[0]])
    create_check_print_batch([ids[1]])
    assert mark_batch_printed(run_a, True)[0]
    return statement_id, ids


def test_by_default_every_check_is_listed(db):
    statement_id, ids = _three_checks(db)
    data = _fetch_batch_data(statement_id, True)
    assert len(data["checks"]) == 3 and data["invoices"][0]["display_status"].startswith("Check issued")


def test_only_checks_of_a_run_marked_printed_count_as_printed(db):
    statement_id, ids = _three_checks(db)

    data = _fetch_batch_data(statement_id, True, include_unprinted=False)

    assert len(data["checks"]) == 1                                        # the exported-but-not-marked and the parked ones are out
    assert len(data["payment_ledger"]) == 1
    assert data["invoices"][0]["display_status"] == "Partly covered"       # only the printed check covers part of the invoice


def test_marking_the_other_run_printed_brings_its_checks_in(db):
    statement_id, ids = _three_checks(db)
    run_b = fetchone(db, "SELECT print_batch_id FROM checks_issued WHERE check_id = ?", (ids[1],))[0]
    assert mark_batch_printed(run_b, True)[0]

    data = _fetch_batch_data(statement_id, True, include_unprinted=False)

    assert len(data["checks"]) == 2


def test_a_check_printed_the_old_in_app_way_with_no_run_counts_as_printed(db):
    statement_id, ids = _three_checks(db)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE checks_issued SET printed_at = CURRENT_TIMESTAMP WHERE check_id = ?", (ids[2],))       # no print_batch_id
    conn.commit()
    conn.close()

    assert len(_fetch_batch_data(statement_id, True, include_unprinted=False)["checks"]) == 2


def test_summary_counts_follow_the_same_option(db):
    statement_id, ids = _three_checks(db)
    both = bulk_summary_rows([statement_id])[0]
    printed_only = bulk_summary_rows([statement_id], include_unprinted_checks=False)[0]
    assert (both["checks"], both["checks_total"]) == (3, 15000.0)
    assert (printed_only["checks"], printed_only["checks_total"]) == (1, 5000.0)
