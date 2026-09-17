"""Check print batch log (2026-09-17) -- the "what went out for printing, and when" history now
that printing happens outside this system (Chrysanth), replacing the old in-app print PDF as the
record of a print run."""
from datetime import date

import app
from conftest import make_creditor, make_statement
from engines.check_run_service import create_check_print_batch, get_check_print_batches, get_check_print_batch_detail


def test_create_check_print_batch_returns_none_for_empty_list(db):
    assert create_check_print_batch([]) is None


def test_create_check_print_batch_logs_batch_and_tags_checks(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 9000.0, [], statement_id)
    _, _, check_id_1 = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    _, _, check_id_2 = app.issue_check(creditor_id, 4000.0, date(2026, 3, 2), statement_id=statement_id)

    batch_id = create_check_print_batch([check_id_1, check_id_2])

    batches = get_check_print_batches()
    assert len(batches) == 1
    assert batches["print_batch_id"].iloc[0] == batch_id
    assert batches["check_count"].iloc[0] == 2
    assert batches["total_amount"].iloc[0] == 9000.0

    row_1 = app.q_live("SELECT print_batch_id, printed_at FROM checks_issued WHERE check_id = ?", (check_id_1,))
    assert row_1["print_batch_id"].iloc[0] == batch_id
    assert row_1["printed_at"].iloc[0] is not None


def test_create_check_print_batch_does_not_overwrite_earlier_printed_at(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 5000.0, [], statement_id)
    _, _, check_id = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    app.mark_checks_printed([check_id])
    first_printed_at = app.q_live("SELECT printed_at FROM checks_issued WHERE check_id = ?", (check_id,))["printed_at"].iloc[0]

    create_check_print_batch([check_id])

    second_printed_at = app.q_live("SELECT printed_at FROM checks_issued WHERE check_id = ?", (check_id,))["printed_at"].iloc[0]
    assert second_printed_at == first_printed_at


def test_get_check_print_batch_detail_includes_booklet_and_reference(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 5000.0, [], statement_id)
    _, _, check_id = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    batch_id = create_check_print_batch([check_id])

    detail = get_check_print_batch_detail(batch_id)
    assert len(detail) == 1
    row = detail.iloc[0]
    assert row["payee_name"] == "ACME Tires"
    assert row["track"] == "Supplier"
    assert row["booklet_code"] is None
    assert row["check_number"] is None
