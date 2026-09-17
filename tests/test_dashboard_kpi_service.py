"""get_top_supplier_dependency() (2026-09-17) -- the Dashboard's supplier concentration alert
banner reads this directly, so its None-vs-real-dict behavior at the empty-data boundary matters:
a wrong answer there is either a banner that never fires or one that crashes on an empty db."""
import sqlite3
from datetime import date

from conftest import make_creditor, make_statement
from engines import dashboard_kpi_service as kpi


def _make_check(db_path, creditor_id, check_date, amount, statement_id):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_number, check_date, amount, status) "
        "VALUES (?, 1, ?, ?, ?, ?, 'Active')",
        (creditor_id, statement_id, f"CHK-{check_date}-{amount}", str(check_date), amount),
    )
    conn.commit()
    conn.close()


def test_returns_none_with_no_outstanding_supplier_payable(db):
    assert kpi.get_top_supplier_dependency() is None


def test_computes_correct_share_of_total(db):
    big = make_creditor(db, name="Big Supplier", creditor_type="SUPPLIER")
    conn = sqlite3.connect(db)
    cur = conn.execute("INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, 'REF-BIG', 0, 'Draft')", (big,))
    stmt_big = cur.lastrowid
    conn.commit()
    conn.close()
    _make_check(db, big, date(2026, 9, 20), 75000.0, stmt_big)

    small = make_creditor(db, name="Small Supplier", creditor_type="SUPPLIER")
    stmt_small = make_statement(db, small)
    _make_check(db, small, date(2026, 9, 20), 25000.0, stmt_small)

    out = kpi.get_top_supplier_dependency()
    assert out["name"] == "Big Supplier"
    assert out["amount"] == 75000.0
    assert out["total_outstanding"] == 100000.0
    assert out["pct"] == 75.0
