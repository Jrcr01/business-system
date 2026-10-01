"""Decision reports (plan G): each returns a table and a plain one-line 'what this says'."""
import sqlite3
from datetime import date, timedelta

from conftest import make_creditor
from engines.decision_reports_service import (
    QUESTION_GUIDE, business_vs_family, card_usage, cost_of_borrowing, memos_by_supplier, monthly_outflow, payables_after_month_end, supplier_payment_performance,
)
from engines.handwritten_check_service import record_handwritten_checks
from engines.statement_rules import add_invoice, create_order_statement


def _paid_invoice(db, name, invoice_date, due, check_date, amount, number):
    sup = make_creditor(db, name=name)
    sid = create_order_statement(sup)
    add_invoice(sup, f"INV-{number}", invoice_date, due, amount, [], sid)
    ok, msg, _ = record_handwritten_checks(sup, [{"number": number, "date": check_date, "amount": amount}], statement_id=sid)
    assert ok, msg
    return sup, sid


def test_every_report_runs_on_an_empty_database(db):
    today = date.today()
    for r in (supplier_payment_performance(today - timedelta(days=30), today), payables_after_month_end(3), monthly_outflow(today), cost_of_borrowing(), card_usage(),
              business_vs_family(), memos_by_supplier(today - timedelta(days=30), today)):
        assert "says" in r and isinstance(r["says"], str) and "table" in r


def test_payment_performance_splits_on_time_from_late(db):
    today = date.today()
    _paid_invoice(db, "OnTime Co", today - timedelta(days=40), today - timedelta(days=10), today - timedelta(days=12), 1000.0, "3992000001")
    _paid_invoice(db, "Late Co", today - timedelta(days=40), today - timedelta(days=20), today - timedelta(days=10), 3000.0, "3992000002")

    r = supplier_payment_performance(today - timedelta(days=60), today)
    t = r["table"].set_index("supplier")

    assert t.loc["OnTime Co", "within_terms_pct"] == 100.0 and t.loc["Late Co", "within_terms_pct"] == 0.0
    assert t.loc["Late Co", "avg_days_late"] == 10.0 and r["table"].iloc[0]["supplier"] == "Late Co"
    assert "25%" in r["says"] and "Late Co" in r["says"]


def test_payment_performance_ignores_beginning_balance_lines(db):
    today = date.today()
    sup, sid = _paid_invoice(db, "Sup", today - timedelta(days=40), today - timedelta(days=20), today - timedelta(days=10), 500.0, "3992000003")
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE invoices SET is_opening_balance = 1")
    assert supplier_payment_performance(today - timedelta(days=60), today)["table"].empty


def test_monthly_outflow_compares_with_last_month(db):
    today = date.today()
    this_month_start = today.replace(day=1)
    last = this_month_start - timedelta(days=15)
    while last.weekday() >= 5:
        last -= timedelta(days=1)
    _paid_invoice(db, "Sup", last - timedelta(days=5), last, last, 800.0, "3992000011")

    r = monthly_outflow(last.replace(day=1))

    assert r["table"].set_index("track").loc["Supplier", "this_month"] == 800.0 and r["top"].iloc[0]["payee_name"] == "Sup"
    assert "800.00" in r["says"]


def test_cost_of_borrowing_ranks_interest_paid_and_to_pay(db):
    bank = make_creditor(db, name="BDO", creditor_type="BANK_LOAN")
    with sqlite3.connect(db) as conn:
        bl = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref, status) VALUES (?, 'L1', 'Active')", (bank,)).lastrowid
        conn.execute("INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount, interest_portion) VALUES (?, 1, ?, 1000, 100)", (bl, str(date.today() - timedelta(days=30))))
        conn.execute("INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount, interest_portion) VALUES (?, 2, ?, 1000, 80)", (bl, str(date.today() + timedelta(days=30))))

    r = cost_of_borrowing()

    row = r["table"].iloc[0]
    assert (row["interest_paid"], row["interest_to_pay"], row["interest_total"]) == (100.0, 80.0, 180.0) and "180.00" in r["says"]


def test_business_vs_family_flags_loans_with_no_for_whom(db):
    bank = make_creditor(db, name="BDO", creditor_type="BANK_LOAN")
    with sqlite3.connect(db) as conn:
        bl = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref, status) VALUES (?, 'L1', 'Active')", (bank,)).lastrowid
        conn.execute("INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (?, 1, ?, 5000)", (bl, str(date.today() + timedelta(days=10))))
    r = business_vs_family()
    assert r["table"].iloc[0]["for_whom"] == "Not set" and "no 'for whom'" in r["says"]


def test_credit_memos_by_supplier_shows_the_share_of_purchases(db):
    today = date.today()
    sup, sid = _paid_invoice(db, "Returns Co", today - timedelta(days=5), today + timedelta(days=20), today + timedelta(days=20), 1000.0, "3992000021")
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO credit_memos (statement_id, memo_ref, memo_date, amount) VALUES (?, 'CM1', ?, 100)", (sid, str(today - timedelta(days=2))))
    r = memos_by_supplier(today - timedelta(days=30), today)
    assert r["table"].iloc[0]["memo_pct"] == 10.0


def test_the_question_guide_points_at_real_reports():
    assert len(QUESTION_GUIDE) >= 10 and all(len(q) == 2 for q in QUESTION_GUIDE)
