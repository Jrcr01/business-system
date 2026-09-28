"""Due today = still owed, everywhere (owner, 2026-09-28): something dated today is not paid / cleared yet -- it is paid once its date has
passed. Pinned in the main places: the effective check status, a batch's invoice status, the batch rundown, the overview's paid vs
scheduled, bank-loan installments, and the AP aging (an invoice due today is "Current" -- it used to fall into no bucket at all)."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import make_creditor, make_statement
from engines.bank_loan_service import CLEARED_SQL
from engines.overview_report_service import _cash_rows
from engines.reports_service import get_payables_rundown, get_supplier_ap_aging_report
from engines.statement_rules import sync_invoice_payment_status

TODAY = date.today()
YESTERDAY = TODAY - timedelta(days=1)


def _batch(db, ref, check_day, amount=1000.0):
    sup = make_creditor(db, name=f"Supplier {ref}")
    sid = make_statement(db, sup)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE order_statements SET statement_ref = ?, status = 'Issued' WHERE statement_id = ?", (ref, sid))
    app.add_invoice(sup, f"INV-{ref}", TODAY - timedelta(days=30), check_day, amount, [], sid)
    _, _, cid = app.issue_check(sup, amount, check_day, statement_id=sid)
    return sid, cid


def test_a_check_dated_today_is_not_cleared_yet(db):
    sid_today, today_check = _batch(db, "OS-TODAY", TODAY)
    sid_past, past_check = _batch(db, "OS-PAST", YESTERDAY)
    with sqlite3.connect(db) as conn:
        status = dict(conn.execute("SELECT check_id, effective_status FROM v_effective_checks").fetchall())
    assert status[today_check] == "Active" and status[past_check] == "Cleared"
    sync_invoice_payment_status(sid_today)
    sync_invoice_payment_status(sid_past)
    with sqlite3.connect(db) as conn:
        inv = dict(conn.execute("SELECT statement_id, status FROM invoices").fetchall())
    assert inv[sid_today] == "Unpaid" and inv[sid_past] == "Paid"
    rd = get_payables_rundown().set_index("batch")
    assert (rd.loc["OS-TODAY", "paid"], rd.loc["OS-TODAY", "scheduled"], rd.loc["OS-TODAY", "status"]) == (0.0, 1000.0, "Pending")
    assert (rd.loc["OS-PAST", "paid"], rd.loc["OS-PAST", "status"]) == (1000.0, "Paid")
    with sqlite3.connect(db) as conn:
        cash = _cash_rows(conn, YESTERDAY, TODAY)
    by_day = {r.date: (r.paid, r.scheduled) for r in cash.itertuples()}
    assert by_day[TODAY.isoformat()] == (0.0, 1000.0) and by_day[YESTERDAY.isoformat()] == (1000.0, 0.0)


def test_bank_loan_installment_due_today_is_still_owed(db):
    bank = make_creditor(db, name="Metrobank", creditor_type="BANK_LOAN")
    with sqlite3.connect(db) as conn:
        loan = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref) VALUES (?, 'BL-1')", (bank,)).lastrowid
        for n, d in ((1, YESTERDAY), (2, TODAY)):
            conn.execute("INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (?, ?, ?, 500)",
                         (loan, n, d.isoformat()))
        cleared = [r[0] for r in conn.execute(f"SELECT installment_no FROM bank_loan_installments WHERE {CLEARED_SQL} ORDER BY installment_no")]
    assert cleared == [1]


def test_aging_puts_an_invoice_due_today_in_current(db):
    sup = make_creditor(db, name="Aging Supplier")
    sid = make_statement(db, sup)
    for ref, due, amt in (("A", TODAY, 100.0), ("B", TODAY - timedelta(days=1), 200.0), ("C", TODAY - timedelta(days=30), 300.0),
                          ("D", TODAY - timedelta(days=31), 400.0), ("E", TODAY - timedelta(days=61), 500.0)):
        app.add_invoice(sup, f"INV-{ref}", due - timedelta(days=30), due, amt, [], sid)
    with sqlite3.connect(db) as conn:                       # add_invoice works the due date out from the terms; pin the test dates
        for ref, due in (("A", TODAY), ("B", TODAY - timedelta(days=1)), ("C", TODAY - timedelta(days=30)),
                         ("D", TODAY - timedelta(days=31)), ("E", TODAY - timedelta(days=61))):
            conn.execute("UPDATE invoices SET due_date = ?, status = 'Unpaid' WHERE doc_ref = ?", (due.isoformat(), f"INV-{ref}"))
    row = get_supplier_ap_aging_report().iloc[0]
    assert (row["current_due"], row["days_1_30"], row["days_31_60"], row["days_over_60"]) == (100.0, 500.0, 400.0, 500.0)
    assert row["total_unpaid"] == 1500.0
