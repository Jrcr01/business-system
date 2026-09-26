"""get_payables_rundown(): paid / partially paid / pending per issued supplier batch."""
import sqlite3
from datetime import date, timedelta

from conftest import make_creditor
from engines.reports_service import get_payables_rundown

BANK = 1


def _batch(db, cid, ref, total, checks):
    conn = sqlite3.connect(db)
    sid = conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, ?, ?, 'Issued')", (cid, ref, total)).lastrowid
    for n, (amount, days) in enumerate(checks):
        conn.execute(
            "INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) VALUES (?, ?, ?, ?, ?, ?, 'Active')",
            (BANK, sid, cid, f"{ref}-{n}", amount, str(date.today() + timedelta(days=days))),
        )
    conn.commit(); conn.close()


def test_rundown_classifies_paid_partial_pending(db):
    cid = make_creditor(db, creditor_type="SUPPLIER")
    _batch(db, cid, "B-PAID", 1000, [(1000, -5)])
    _batch(db, cid, "B-PART", 1000, [(400, -5), (600, 30)])
    _batch(db, cid, "B-PEND", 1000, [(1000, 30)])

    df = get_payables_rundown().set_index("batch")

    assert df.loc["B-PAID", "status"] == "Paid"
    assert df.loc["B-PART", "status"] == "Partially Paid" and df.loc["B-PART", "balance"] == 600
    assert df.loc["B-PEND", "status"] == "Pending" and df.loc["B-PEND", "scheduled"] == 1000
    assert len(get_payables_rundown(cid + 999)) == 0


def test_rundown_from_to_narrows_by_check_dates(db):
    cid = make_creditor(db, creditor_type="SUPPLIER")
    _batch(db, cid, "B-OLD", 1000, [(1000, -60)])
    _batch(db, cid, "B-NEW", 1000, [(1000, 30)])

    window = get_payables_rundown(None, date.today() - timedelta(days=10), date.today() + timedelta(days=60))
    assert list(window["batch"]) == ["B-NEW"]
    assert len(get_payables_rundown()) == 2


def test_invoice_rundown_maps_status_and_skips_cancelled(db):
    from engines.reports_service import get_invoice_rundown
    cid = make_creditor(db, creditor_type="SUPPLIER")
    conn = sqlite3.connect(db)
    for ref, status in (("I1", "Unpaid"), ("I2", "Partially Paid"), ("I3", "Paid"), ("I4", "Cancelled")):
        conn.execute("INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount, status) VALUES (?, ?, '2026-09-01', '2026-10-01', 100, 100, ?)", (cid, ref, status))
    conn.commit(); conn.close()

    df = get_invoice_rundown().set_index("invoice")

    assert list(df.index) == ["I1", "I2", "I3"]
    assert df.loc["I1", "status"] == "Pending"
    assert len(get_invoice_rundown(cid, "2026-10-01", "2026-12-31")) == 0
