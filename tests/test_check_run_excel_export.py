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


def test_csv_export_matches_the_chrysanth_sample_layout(db):
    from engines.check_run_service import generate_pending_check_run_csv
    creditor_id = make_creditor(db, name="Roy Ang")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 5000.5, [], statement_id)
    app.issue_check(creditor_id, 5000.5, date(2026, 7, 10), statement_id=statement_id)

    raw = generate_pending_check_run_csv()
    df = pd.read_csv(io.BytesIO(raw), dtype=str, keep_default_na=False)

    assert raw.startswith(b"\xef\xbb\xbf")                                          # Excel-friendly BOM
    assert list(df.columns) == ["Date", "Account No", "Payee", "Amount", "Cheque No", "Remark", "A/C Payee Only"]
    row = df.iloc[0]
    assert (row["Date"], row["Payee"], row["Amount"]) == ("7/10/2026", "Roy Ang", "5000.50")
    assert row["Account No"] == "" and row["Cheque No"] == ""                      # no account number; not numbered yet
    assert row["A/C Payee Only"] == "Y" and row["Remark"] != ""


def test_csv_export_filters_to_given_check_ids(db):
    from engines.check_run_service import generate_pending_check_run_csv
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 9000.0, [], statement_id)
    _, _, check_id_1 = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    app.issue_check(creditor_id, 4000.0, date(2026, 3, 2), statement_id=statement_id)

    df = pd.read_csv(io.BytesIO(generate_pending_check_run_csv([check_id_1])), dtype=str)

    assert len(df) == 1 and df["Amount"].iloc[0] == "5000.00"


def test_a_logged_batch_can_be_exported_again(db):
    from engines.check_run_service import create_check_print_batch, generate_batch_csv, generate_pending_check_run_csv
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 1, 1), date(2026, 1, 31), 9000.0, [], statement_id)
    _, _, id1 = app.issue_check(creditor_id, 5000.0, date(2026, 3, 1), statement_id=statement_id)
    _, _, id2 = app.issue_check(creditor_id, 4000.0, date(2026, 3, 2), statement_id=statement_id)
    batch_id = create_check_print_batch([id1, id2])           # marks them printed, so the pending export is now empty

    assert len(pd.read_csv(io.BytesIO(generate_pending_check_run_csv()), dtype=str)) == 0
    again = pd.read_csv(io.BytesIO(generate_batch_csv(batch_id)), dtype=str)

    assert list(again["Amount"]) == ["5000.00", "4000.00"]
