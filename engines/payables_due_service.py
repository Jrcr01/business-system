"""Payables due (2026-10-02): what is COMMITTED to go out, and when -- with no guess about future deposits (the cash forecast is hidden until the Sales / AR
cycle brings real deposits). It reads the same list the cash forecast uses, cash_insights_service.scheduled_outflows(): checks not yet cleared (on their
check date, or today when that date has passed), bank-loan and card dues from ENTERED statements, and ordered purchase orders on their pay date.

  * next_days(days)       -- what can hit the bank in the next N days, with today's bank balance beside it (no verdict)
  * by_week(weeks)        -- week by week, split into Supplier checks / Lender checks / Bank loans / Credit cards / Purchase orders
  * by_supplier(days)     -- who holds checks that can be deposited, and which orders are coming"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from engines.cash_insights_service import cash_position, scheduled_outflows
from engines.clock import manila_today

KINDS = {"Supplier": "Supplier checks", "Lender": "Lender checks", "Bank Loan": "Bank loans", "Credit Card": "Credit cards", "Purchase order": "Purchase orders"}
COLUMNS = list(KINDS.values())


def _out(start: date, end: date) -> pd.DataFrame:
    out = scheduled_outflows(start, end)
    if out.empty:
        return out.assign(kind=pd.Series(dtype=str))
    return out.assign(kind=out["track"].map(KINDS).fillna(out["track"]))


def next_days(days: int = 7, today: date | None = None) -> dict:
    """{'amount', 'checks', 'orders', 'balance', 'balance_as_of'} for today and the days after it (7 = today and the next 6). The bank balance is shown beside the amount, not judged against it."""
    today = today or manila_today()
    out = _out(today, today + timedelta(days=days - 1))
    pos = cash_position()
    return {"amount": float(out["amount"].sum()) if not out.empty else 0.0,
            "checks": int(out["kind"].isin(["Supplier checks", "Lender checks"]).sum()) if not out.empty else 0,
            "orders": int((out["kind"] == "Purchase orders").sum()) if not out.empty else 0,
            "balance": pos["balance"] if pos else None, "balance_as_of": pos["as_of"] if pos else None}


def by_week(weeks: int = 12, today: date | None = None) -> pd.DataFrame:
    """One row per week (Monday) for the next `weeks` weeks, even weeks with nothing: week, the five kinds, Total."""
    today = today or manila_today()
    first = today - timedelta(days=today.weekday())
    starts = [first + timedelta(weeks=i) for i in range(weeks)]
    out = _out(today, first + timedelta(weeks=weeks) - timedelta(days=1))
    table = pd.DataFrame({"week": starts})
    for col in COLUMNS:
        table[col] = 0.0
    if not out.empty:
        wk = (pd.to_datetime(out["day"]) - pd.to_timedelta(pd.to_datetime(out["day"]).dt.weekday, unit="D")).dt.date
        sums = out.assign(week=wk).pivot_table(index="week", columns="kind", values="amount", aggfunc="sum", fill_value=0.0)
        for col in COLUMNS:
            if col in sums.columns:
                table[col] = table["week"].map(sums[col]).fillna(0.0)
    table["Total"] = table[COLUMNS].sum(axis=1)
    return table


def by_supplier(days: int = 30, today: date | None = None) -> pd.DataFrame:
    """Who is owed money that is already committed: for each payee the checks that can be deposited and the orders coming, biggest first.
    Columns: payee, checks, check_amount, orders, order_amount, total, next_day. Loan and card dues belong to their own pages and are left out."""
    today = today or manila_today()
    out = _out(today, today + timedelta(days=days - 1))
    cols = ["payee", "checks", "check_amount", "orders", "order_amount", "total", "next_day"]
    out = out[out["kind"].isin(["Supplier checks", "Lender checks", "Purchase orders"])] if not out.empty else out
    if out.empty:
        return pd.DataFrame(columns=cols)
    is_check = out["kind"].isin(["Supplier checks", "Lender checks"])
    g = out.assign(c=is_check.astype(int), ca=out["amount"].where(is_check, 0.0), o=(~is_check).astype(int), oa=out["amount"].where(~is_check, 0.0)).groupby("payee").agg(
        checks=("c", "sum"), check_amount=("ca", "sum"), orders=("o", "sum"), order_amount=("oa", "sum"), next_day=("day", "min")).reset_index()
    g["total"] = g["check_amount"] + g["order_amount"]
    return g.sort_values("total", ascending=False).reset_index(drop=True)[cols]
