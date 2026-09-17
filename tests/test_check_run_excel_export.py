"""Excel export of the pending check run (2026-09-17) -- checks now get printed outside this
system via Chrysanth's own batch-print feature, so this is the handoff point: park checks here,
export, import into Chrysanth, print, then come back and assign check numbers."""
import io
from datetime import date

import pandas as pd

import app
from conftest import make_creditor, make_statement
from engines.check_run_service import generate_pending_check_run_excel


def test_excel_export_contains_expected_columns_and_values(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 5000.0, [], statement_id)
    app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)

    excel_bytes = generate_pending_check_run_excel()
    df = pd.read_excel(io.BytesIO(excel_bytes))

    assert list(df.columns) == ["Payee Name", "Amount", "Check Date", "Reference"]
    assert len(df) == 1
    assert df["Payee Name"].iloc[0] == "ACME Tires"
    assert df["Amount"].iloc[0] == 5000.0
    assert df["Check Date"].iloc[0] == pd.Timestamp(2026, 3, 1)


def test_excel_export_filters_to_given_check_ids(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 9000.0, [], statement_id)
    _, _, check_id_1 = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    _, _, check_id_2 = app.issue_check(creditor_id, 4000.0, date(2026, 3, 2), statement_id=statement_id)

    excel_bytes = generate_pending_check_run_excel([check_id_1])
    df = pd.read_excel(io.BytesIO(excel_bytes))

    assert len(df) == 1
    assert df["Amount"].iloc[0] == 5000.0


def test_excel_export_excludes_already_printed_checks(db):
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 5000.0, [], statement_id)
    _, _, check_id = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    app.mark_checks_printed([check_id])

    excel_bytes = generate_pending_check_run_excel()
    df = pd.read_excel(io.BytesIO(excel_bytes))

    assert df.empty
