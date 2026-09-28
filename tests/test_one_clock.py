"""Phase F1.3 (2026-09-28): one clock -- the business date is always Manila's. The clock is pinned to 07:30 Manila on Jan 15, 2030
(= 23:30 UTC the day BEFORE, when a UTC server would still say Jan 14) -- far from the real date, so anything still reading the computer's
clock would disagree. Checks the due-today rule (views + SQL), the aging buckets and the planner, and that no code reads the clock itself."""
import pathlib
import re
from datetime import date, datetime, timedelta

import app
from conftest import make_creditor, make_statement
from engines import check_date_planner as cp
from engines.bank_loan_service import CLEARED_SQL
from engines.clock import manila_now, manila_today, set_fake_now
from engines.common import get_conn
from engines.reports_service import get_supplier_ap_aging_report

ROOT = pathlib.Path(__file__).resolve().parent.parent
FAKE = datetime(2030, 1, 15, 7, 30)                 # Tuesday, 07:30 Manila = 2030-01-14 23:30 UTC
TODAY, YESTERDAY = date(2030, 1, 15), date(2030, 1, 14)


def _pin():
    set_fake_now(FAKE)


def test_python_and_sql_use_the_same_manila_clock(db):
    _pin()
    try:
        assert manila_today() == TODAY and manila_now() == FAKE
        with get_conn(db) as conn:
            assert conn.execute("SELECT manila_today(), manila_now()").fetchone() == ("2030-01-15", "2030-01-15 07:30:00")
    finally:
        set_fake_now(None)


def test_due_today_is_still_owed_at_0730_manila(db):
    _pin()
    try:
        sup = make_creditor(db, name="Clock Supplier")
        sid = make_statement(db, sup)
        app.add_invoice(sup, "INV-CLK", TODAY - timedelta(days=30), TODAY, 2000.0, [], sid)
        _, _, today_check = app.issue_check(sup, 1000.0, TODAY, statement_id=sid)
        _, _, past_check = app.issue_check(sup, 1000.0, YESTERDAY, statement_id=sid)
        with get_conn(db) as conn:
            status = dict(conn.execute("SELECT check_id, effective_status FROM v_effective_checks").fetchall())
            loan = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref) VALUES (?, 'BL-CLK')", (sup,)).lastrowid
            for n, d in ((1, YESTERDAY), (2, TODAY)):
                conn.execute("INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (?, ?, ?, 1)",
                             (loan, n, d.isoformat()))
            cleared = [r[0] for r in conn.execute(f"SELECT installment_no FROM bank_loan_installments WHERE {CLEARED_SQL}")]
        assert status[today_check] == "Active" and status[past_check] == "Cleared"
        assert cleared == [1]
    finally:
        set_fake_now(None)


def test_aging_and_planner_follow_manila_today(db):
    _pin()
    try:
        sup = make_creditor(db, name="Aging Clock")
        sid = make_statement(db, sup)
        for ref, due, amt in (("A", TODAY, 100.0), ("B", YESTERDAY, 200.0)):
            app.add_invoice(sup, f"AG-{ref}", due - timedelta(days=30), due, amt, [], sid)
        with get_conn(db) as conn:
            for ref, due in (("A", TODAY), ("B", YESTERDAY)):
                conn.execute("UPDATE invoices SET due_date = ?, status = 'Unpaid' WHERE doc_ref = ?", (due.isoformat(), f"AG-{ref}"))
        row = get_supplier_ap_aging_report().iloc[0]
        assert (row["current_due"], row["days_1_30"]) == (100.0, 200.0)          # due today = Current; yesterday = 1-30 days
        p = cp.plan(start_cash=1_000_000, inflow_per_day=0,
                    settings=cp.PlanSettings(max_days_late=7, daily_limit=0.0, daily_max_checks=0, priority=set(), buffer=0.0))
        by_doc = {r.doc_ref: r for r in p.items.itertuples()}
        assert by_doc["AG-A"].suggested_date == TODAY and by_doc["AG-A"].status == "On time"
        assert by_doc["AG-B"].status == "Overdue"
    finally:
        set_fake_now(None)


def test_no_code_reads_the_computer_clock():
    """engines / views / app use engines.clock only (time.monotonic() stopwatches are fine)."""
    bad = []
    for p in list((ROOT / "engines").glob("*.py")) + list((ROOT / "views").glob("*.py")) + [ROOT / "app.py"]:
        if p.name == "clock.py":
            continue
        text = p.read_text(encoding="utf-8")
        for rx in (r"\bdate\.today\(", r"\bdatetime\.now\(", r"\bdatetime\.today\(", r"'localtime'", r"'now'"):
            if re.search(rx, text):
                bad.append(f"{p.name}: {rx}")
    assert bad == []
