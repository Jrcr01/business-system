"""Supplier / lender history reports (owner, 2026-09-29): the Supplier history showed no invoices or checks because the default From/To
range came out as the batch-created dates only (pandas 3 read the date-only invoice and check dates with the "date time" format of the
batch column and dropped them). Also: the bank's clearing date is shown next to each check."""
import sqlite3

import pandas as pd

from engines.reports_service import get_lender_history, get_supplier_history
from views.shared import history_span


def test_the_default_range_covers_every_kind_of_date():
    """Real case: AMK Auto Parts -- batches created Sep 19-21 ("date time"), invoices Aug 20-Sep 12 and checks Nov 3 - Mar 31 ("date only")."""
    created = pd.Series(["2026-09-19 14:27:19", "2026-09-21 06:32:38"])
    invoices = pd.Series(["2026-08-20", "2026-09-12"])
    checks = pd.Series(["2026-11-03", "2027-03-31"])
    lo, hi = history_span([created, invoices, checks])
    assert (str(lo), str(hi)) == ("2026-08-20", "2027-03-31")
    assert history_span([checks, created, invoices]) == (lo, hi)                    # whatever the order
    assert history_span([None, pd.Series([], dtype=str)]) is None                 # nothing to range over
    assert history_span([pd.Series(["not a date", None])]) is None


def _supplier_with_a_cleared_and_an_open_check(db):
    from conftest import make_creditor, make_statement

    sup = make_creditor(db, name="AMK Auto Parts")
    sid = make_statement(db, sup)
    with sqlite3.connect(db) as conn:
        imp = conn.execute("INSERT INTO bank_statement_imports (bank_account_id, file_name, file_hash) VALUES (1, 's.csv', 'h')").lastrowid
        txn = conn.execute("""INSERT INTO bank_transactions (import_id, txn_date, posting_date, check_number, debit, kind, line_key, match_status)
                             VALUES (?, '2026-09-24', '2026-09-24', '3992000001', 1000, 'CHECK', 'k1', 'matched')""", (imp,)).lastrowid
        conn.execute("INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_number, check_date, amount, status, bank_cleared_date, bank_txn_id) "
                     "VALUES (?, 1, ?, '3992000001', '2026-09-20', 1000, 'Active', '2026-09-24', ?)", (sup, sid, txn))
        conn.execute("INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_number, check_date, amount, status) "
                     "VALUES (?, 1, ?, '3992000002', '2027-01-20', 2000, 'Active')", (sup, sid))
    return sup


def test_the_supplier_history_shows_when_the_bank_cleared_each_check(db):
    sup = _supplier_with_a_cleared_and_an_open_check(db)
    _, _, checks = get_supplier_history(sup)
    assert "bank_cleared_date" in checks.columns
    got = dict(zip(checks["check_number"], checks["bank_cleared_date"]))
    assert got["3992000001"] == "2026-09-24" and pd.isna(got["3992000002"])          # blank when the bank has not shown it


def test_the_lender_history_shows_the_clearing_date_too(db):
    from conftest import make_creditor

    lender = make_creditor(db, name="Tita Lending", creditor_type="LENDER")
    with sqlite3.connect(db) as conn:
        note = conn.execute("INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, monthly_interest_rate, total_interest, "
                            "total_payable, status) VALUES (?, '2026 - LN 0001', '2026-09-01', 10000, 1, 0, 10000, 'Active')", (lender,)).lastrowid
        conn.execute("INSERT INTO checks_issued (creditor_id, bank_account_id, loan_note_id, check_number, check_date, amount, status, bank_cleared_date) "
                     "VALUES (?, 1, ?, '3992000009', '2026-09-20', 5000, 'Active', '2026-09-23')", (lender, note))
    _, checks = get_lender_history(lender)
    assert list(checks["bank_cleared_date"]) == ["2026-09-23"]
