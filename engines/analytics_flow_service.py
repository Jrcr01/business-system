"""Numbers behind the rebuilt Analytics page (plan H / G, 2026-10-01): past AND future periods, the bank's real clearing data, plain names.
Every function takes a period (start, end) or a count of months / weeks and returns a DataFrame ready to chart and to download."""
from __future__ import annotations

from engines.clock import manila_today
from engines.common import get_conn
import os
import sqlite3
from datetime import date, timedelta

import pandas as pd

from engines.reports_service import get_payment_calendar_items

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

PERIODS = ["This month", "Last 3 months", "Last 12 months", "Next 3 months", "Next 12 months", "Last 12 + next 12 months"]


def _first_of_month(d: date, months: int = 0) -> date:
    y, m = d.year, d.month + months
    y += (m - 1) // 12
    m = (m - 1) % 12 + 1
    return date(y, m, 1)


def period_range(choice: str, today: date | None = None) -> tuple[date, date]:
    """(first day, last day) of a named period; whole months, so a chart bar is never half a month."""
    today = today or manila_today()
    this = _first_of_month(today)
    table = {
        "This month": (this, _first_of_month(this, 1) - timedelta(days=1)),
        "Last 3 months": (_first_of_month(this, -3), this - timedelta(days=1)),
        "Last 12 months": (_first_of_month(this, -12), this - timedelta(days=1)),
        "Next 3 months": (this, _first_of_month(this, 3) - timedelta(days=1)),
        "Next 12 months": (this, _first_of_month(this, 12) - timedelta(days=1)),
        "Last 12 + next 12 months": (_first_of_month(this, -12), _first_of_month(this, 12) - timedelta(days=1)),
    }
    return table[choice]


def money_flow(start: date, end: date) -> pd.DataFrame:
    """What was paid (days that have passed) and what is still scheduled (today and later), per month and track: columns month, track, state, amount, items."""
    items = get_payment_calendar_items()
    cols = ["month", "track", "state", "amount", "items"]
    if items.empty:
        return pd.DataFrame(columns=cols)
    day = pd.to_datetime(items["actual_payment_date"]).dt.date
    items = items[(day >= start) & (day <= end)].copy()
    if items.empty:
        return pd.DataFrame(columns=cols)
    items["month"] = pd.to_datetime(items["actual_payment_date"]).dt.to_period("M").dt.to_timestamp().dt.date
    g = items.groupby(["month", "track", "state"]).agg(amount=("amount", "sum"), items=("amount", "count")).reset_index()
    return g.sort_values(["month", "track"]).reset_index(drop=True)


def _spend(start: date, end: date) -> pd.DataFrame:
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(
            """SELECT cr.creditor_name AS supplier, COALESCE(SUM(i.amount), 0) AS spend, COUNT(*) AS invoices
               FROM invoices i JOIN creditors cr ON cr.creditor_id = i.creditor_id
               WHERE i.status != 'Cancelled' AND i.is_opening_balance = 0 AND i.invoice_date BETWEEN ? AND ?
               GROUP BY cr.creditor_id ORDER BY spend DESC""", conn, params=(str(start), str(end)))


def supplier_spend(start: date, end: date) -> pd.DataFrame:
    """Purchases (invoices, net of their less %; beginning-balance lines and cancelled invoices left out) per supplier in the period, with the share of the total
    and the change against the period of the same length just before it."""
    cur = _spend(start, end)
    days = (end - start).days + 1
    prev = _spend(start - timedelta(days=days), start - timedelta(days=1)).rename(columns={"spend": "previous", "invoices": "prev_invoices"})
    out = cur.merge(prev[["supplier", "previous"]], on="supplier", how="outer").fillna({"spend": 0.0, "invoices": 0, "previous": 0.0})
    total = float(out["spend"].sum())
    out["share_pct"] = out["spend"] / total * 100 if total else 0.0
    out["change_pct"] = out.apply(lambda r: (r["spend"] - r["previous"]) / r["previous"] * 100 if r["previous"] else None, axis=1)
    out["invoices"] = out["invoices"].astype(int)
    return out.sort_values("spend", ascending=False).reset_index(drop=True)[["supplier", "spend", "share_pct", "previous", "change_pct", "invoices"]]


def cleared_by_week(start: date, end: date) -> pd.DataFrame:
    """Checks the bank cleared, per week (Monday), with the average days from the check's date to the bank clearing it: week, checks, amount, avg_days."""
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT bank_cleared_date, check_date, amount FROM checks_issued
               WHERE status = 'Active' AND bank_cleared_date IS NOT NULL AND bank_cleared_date BETWEEN ? AND ?""", conn, params=(str(start), str(end)))
    if df.empty:
        return pd.DataFrame(columns=["week", "checks", "amount", "avg_days"])
    cleared = pd.to_datetime(df["bank_cleared_date"])
    df["week"] = (cleared - pd.to_timedelta(cleared.dt.weekday, unit="D")).dt.date
    df["days"] = (cleared - pd.to_datetime(df["check_date"])).dt.days
    g = df.groupby("week").agg(checks=("amount", "count"), amount=("amount", "sum"), avg_days=("days", "mean")).reset_index()
    return g.sort_values("week").reset_index(drop=True)


def voids_by_month(start: date, end: date) -> pd.DataFrame:
    """Checks voided (cancelled) and replaced per month, by the date the void / replacement was recorded: month, voided, replaced. Only checks voided since the dates
    were kept (2026-09-30) are counted."""
    with get_conn(DB_PATH) as conn:
        voided = pd.read_sql_query("SELECT substr(cancelled_at, 1, 7) AS m, COUNT(*) AS n FROM checks_issued WHERE cancelled_at BETWEEN ? AND ? GROUP BY m",
                                   conn, params=(str(start), str(end) + " 23:59:59"))
        replaced = pd.read_sql_query("SELECT substr(replaced_at, 1, 7) AS m, COUNT(*) AS n FROM checks_issued WHERE status = 'Replaced' AND replaced_at BETWEEN ? AND ? GROUP BY m",
                                     conn, params=(str(start), str(end) + " 23:59:59"))
    months = sorted(set(voided["m"]) | set(replaced["m"]))
    rows = [{"month": m, "voided": int(voided.loc[voided["m"] == m, "n"].sum()), "replaced": int(replaced.loc[replaced["m"] == m, "n"].sum())} for m in months]
    return pd.DataFrame(rows, columns=["month", "voided", "replaced"])


def debt_due_by_month(months: int = 12, today: date | None = None) -> pd.DataFrame:
    """Debt falling due each month for the next `months` months (loan notes, bank loans, credit cards; suppliers are not debt): month, source, amount."""
    today = today or manila_today()
    start, end = _first_of_month(today), _first_of_month(today, months) - timedelta(days=1)
    flow = money_flow(start, end)
    if flow.empty:
        return pd.DataFrame(columns=["month", "source", "amount"])
    flow = flow[(flow["state"] == "Due") & (flow["track"] != "Supplier")]
    names = {"Lender": "Private loans", "Bank Loan": "Bank loans", "Credit Card": "Credit cards"}
    out = flow.assign(source=flow["track"].map(names))[["month", "source", "amount"]]
    out = out.groupby(["month", "source"], as_index=False)["amount"].sum()
    # rent paid to lessors is checked like a loan but is not debt: take it out of "Private loans"
    with get_conn(DB_PATH) as conn:
        rent = pd.read_sql_query(
            """SELECT substr(k.check_date, 1, 7) AS m, SUM(k.amount) AS rent FROM checks_issued k JOIN loan_notes n ON n.loan_note_id = k.loan_note_id
               WHERE n.is_lease = 1 AND k.status = 'Active' AND k.check_date >= ? AND k.check_date <= ? AND k.settled_outside_at IS NULL GROUP BY m""", conn, params=(str(start), str(end)))
    for r in rent.itertuples():
        hit = (out["source"] == "Private loans") & (pd.to_datetime(out["month"]).dt.strftime("%Y-%m") == r.m)
        out.loc[hit, "amount"] = (out.loc[hit, "amount"] - r.rent).clip(lower=0)
    return out[out["amount"] > 0.005].reset_index(drop=True)


def cash_ahead_weeks(weeks: int = 13, today: date | None = None) -> pd.DataFrame:
    """The cash forecast by week for the next `weeks` weeks: week, money_in, money_out, end_balance, lowest_balance, short (the balance goes under the buffer). Empty
    when no recent bank statement is on file."""
    from engines.cash_insights_service import forecast_if_shown

    f = forecast_if_shown(weeks * 7, today)
    cols = ["week", "money_in", "money_out", "end_balance", "lowest_balance", "short"]
    if not f or f.get("daily") is None or f["daily"].empty:
        return pd.DataFrame(columns=cols)
    d = f["daily"].copy()
    day = pd.to_datetime(d["day"])
    d["week"] = (day - pd.to_timedelta(day.dt.weekday, unit="D")).dt.date
    g = d.groupby("week").agg(money_in=("money_in", "sum"), money_out=("money_out", "sum"), end_balance=("balance", "last"), lowest_balance=("balance", "min"),
                              short=("below_buffer", "max")).reset_index()
    g["short"] = g["short"].astype(bool)
    return g[cols]
