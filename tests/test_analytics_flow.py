"""Numbers behind the rebuilt Analytics page: periods, money flow (paid + scheduled), supplier spend with change vs the previous period, bank clearing per week,
voids per month, debt due by month."""
import sqlite3
from datetime import date, timedelta

from conftest import make_creditor
from engines.analytics_flow_service import (
    PERIODS, cleared_by_week, debt_due_by_month, money_flow, period_range, supplier_spend, voids_by_month,
)
from engines.statement_rules import add_invoice, create_order_statement


def _weekday(d, back=True):
    while d.weekday() >= 5:
        d += timedelta(days=-1 if back else 1)
    return d


def _check(db, creditor, statement, number, d, amount, **extra):
    cols = {"bank_account_id": 1, "creditor_id": creditor, "statement_id": statement, "check_number": number, "check_date": str(d), "amount": amount, "status": "Active", **extra}
    with sqlite3.connect(db) as conn:
        conn.execute(f"INSERT INTO checks_issued ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", list(cols.values()))


def test_periods_are_whole_months_and_cover_past_and_future():
    today = date(2026, 10, 15)
    assert period_range("This month", today) == (date(2026, 10, 1), date(2026, 10, 31))
    assert period_range("Last 3 months", today) == (date(2026, 7, 1), date(2026, 9, 30))
    assert period_range("Next 12 months", today) == (date(2026, 10, 1), date(2027, 9, 30))
    assert period_range("Last 12 + next 12 months", today) == (date(2025, 10, 1), date(2027, 9, 30))
    assert all(period_range(p, today)[0] <= period_range(p, today)[1] for p in PERIODS)


def test_money_flow_splits_paid_and_scheduled_by_month_and_track(db):
    sup = make_creditor(db, name="Sup")
    sid = create_order_statement(sup)
    past, future = _weekday(date.today() - timedelta(days=40)), _weekday(date.today() + timedelta(days=40), back=False)
    _check(db, sup, sid, "3992000001", past, 1000.0)
    _check(db, sup, sid, "3992000002", future, 2500.0)

    flow = money_flow(date.today() - timedelta(days=90), date.today() + timedelta(days=90))

    assert set(flow["state"]) == {"Paid", "Due"}
    assert flow[flow["state"] == "Paid"]["amount"].sum() == 1000.0 and flow[flow["state"] == "Due"]["amount"].sum() == 2500.0
    assert set(flow["track"]) == {"Supplier"}
    assert money_flow(date(2000, 1, 1), date(2000, 12, 31)).empty


def test_supplier_spend_ranks_shares_and_compares_with_the_previous_period(db):
    big, small = make_creditor(db, name="Big"), make_creditor(db, name="Small")
    today = date.today()
    cur_start, cur_end = today - timedelta(days=29), today
    for who, amount, when in ((big, 6000.0, today - timedelta(days=5)), (small, 4000.0, today - timedelta(days=6)), (big, 3000.0, today - timedelta(days=40))):
        add_invoice(who, f"I{amount}", when, when + timedelta(days=30), amount, [], create_order_statement(who))
    with sqlite3.connect(db) as conn:                                # a beginning-balance line must never count as a purchase
        conn.execute("UPDATE invoices SET is_opening_balance = 1 WHERE doc_ref = 'I4000.0'")

    out = supplier_spend(cur_start, cur_end)

    assert list(out["supplier"]) == ["Big"]                                       # Small's only invoice is a beginning balance
    row = out.iloc[0]
    assert row["spend"] == 6000.0 and row["share_pct"] == 100.0 and row["previous"] == 3000.0 and round(row["change_pct"]) == 100


def test_cleared_by_week_averages_days_to_clear(db):
    sup = make_creditor(db, name="Sup")
    sid = create_order_statement(sup)
    monday = date.today() - timedelta(days=date.today().weekday() + 7)
    _check(db, sup, sid, "3992000011", monday - timedelta(days=2), 100.0, bank_cleared_date=str(monday))
    _check(db, sup, sid, "3992000012", monday - timedelta(days=4), 300.0, bank_cleared_date=str(monday + timedelta(days=1)))
    _check(db, sup, sid, "3992000013", monday, 50.0)                                   # not cleared: ignored

    out = cleared_by_week(monday, monday + timedelta(days=6))

    assert len(out) == 1 and out.iloc[0]["week"] == monday
    assert out.iloc[0]["checks"] == 2 and out.iloc[0]["amount"] == 400.0 and out.iloc[0]["avg_days"] == 3.5
    assert cleared_by_week(date(2000, 1, 1), date(2000, 2, 1)).empty


def test_voids_by_month_counts_voided_and_replaced(db):
    sup = make_creditor(db, name="Sup")
    sid = create_order_statement(sup)
    today = date.today()
    _check(db, sup, sid, "3992000021", today, 10.0, status="Cancelled", cancelled_at=f"{today} 10:00:00")
    _check(db, sup, sid, "3992000022", today, 10.0, status="Replaced", cancelled_at=f"{today} 10:00:00", replaced_at=f"{today} 10:00:00")

    out = voids_by_month(today - timedelta(days=30), today)

    assert out["voided"].sum() == 2 and out["replaced"].sum() == 1


def test_debt_due_by_month_has_lenders_banks_cards_but_never_suppliers(db):
    sup = make_creditor(db, name="Sup")
    sid = create_order_statement(sup)
    _check(db, sup, sid, "3992000031", _weekday(date.today() + timedelta(days=20), back=False), 777.0)
    assert debt_due_by_month().empty
