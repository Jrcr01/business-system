"""Cash insights from imported bank statements (2026-09-27, owner: "AI business intelligence to provide insights"; decided: local first --
everything here is computed on this laptop, nothing is sent anywhere). Built on engines/bank_statement_service.py's bank lines plus the
app's own schedule of what is owed.

  * cash_position()    -- the latest balance the bank showed, and its date.
  * forecast()         -- a day-by-day projected balance from today: latest bank balance + expected money in (average deposits per weekday
                          over the recent imported weeks) - money out (our checks not yet cleared by the bank, on their check date -- or the
                          next banking day if already past; bank-loan and credit-card dues from the payment schedule). Days under the
                          owner's cash buffer are flagged. This is the base the future check-date planner builds on.
  * float_summary()    -- our checks the bank has not cleared: past their date (should have cleared -- by age) vs dated ahead.
  * inflow_trends()    -- money in by week / month and by type, with the weekly average and slow weeks.
  * unusual_items()    -- returned (bounced) deposited checks, bank debits that are not our checks, unusually large lines, cleared
                          amounts that differ from ours.
  * working_capital()  -- cash vs payables (owner, 2026-09-27: receivables / inventory later): cash cover for what is due in 7 / 30 / 60 / 90
                          days, net cash after it, days of cash, and the average days suppliers wait (DPO, same figure as the Dashboard).
All money is PHP; dates are the bank's posting dates.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta

import pandas as pd

from engines.reports_service import get_payment_schedule_all_tracks, next_banking_day

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")
BUFFER_KEY = "cash_buffer"
INFLOW_WEEKS = 8                       # recent weeks of deposits used for the expected-inflow estimate
DEPOSIT_TYPES = [("CSH DEP", "Cash deposits"), ("CHK DEP", "Check deposits"), ("IBFT", "Bank transfers"), ("WA CR", "Other credits (WA CR)"),
                 ("MO CM", "Bank credit memos")]


def _read(sql: str, params: tuple = ()) -> pd.DataFrame:
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def get_buffer() -> float:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (BUFFER_KEY,)).fetchone()
    try:
        return float(row[0]) if row and row[0] not in (None, "") else 0.0
    except ValueError:
        return 0.0


def set_buffer(amount: float) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        if conn.execute("SELECT 1 FROM settings WHERE key = ?", (BUFFER_KEY,)).fetchone():
            conn.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?", (str(float(amount)), BUFFER_KEY))
        else:
            conn.execute("INSERT INTO settings (key, value, category, description) VALUES (?, ?, 'cash', ?)",
                         (BUFFER_KEY, str(float(amount)), "Lowest bank balance the owner wants to keep (cash forecast warning line)"))


def deposit_type(description: str) -> str:
    d = (description or "").upper()
    return next((label for key, label in DEPOSIT_TYPES if key in d), "Other money in")


# ------------------------------------------------------------------ position and forecast
def cash_position() -> dict | None:
    """{'balance', 'as_of'} from the last bank line on file (by posting date, then line order), or None when nothing is imported."""
    df = _read("SELECT posting_date, balance FROM bank_transactions WHERE balance IS NOT NULL ORDER BY posting_date DESC, txn_id DESC LIMIT 1")
    if df.empty:
        return None
    return {"balance": float(df["balance"][0]), "as_of": date.fromisoformat(df["posting_date"][0])}


def expected_daily_inflow(weeks: int = INFLOW_WEEKS) -> dict[int, float]:
    """Average money in per weekday (0 = Monday) over the last `weeks` weeks of imported bank lines (returned checks are not money in)."""
    pos = cash_position()
    if pos is None:
        return {}
    start = pos["as_of"] - timedelta(weeks=weeks)
    df = _read("SELECT posting_date, credit FROM bank_transactions WHERE kind = 'DEPOSIT' AND posting_date > ?", (start.isoformat(),))
    first = _read("SELECT MIN(posting_date) AS d FROM bank_transactions")["d"][0]
    span_start = max(start + timedelta(days=1), date.fromisoformat(first)) if first else start
    days = pd.date_range(span_start, pos["as_of"])
    count = pd.Series(days.weekday).value_counts()
    if df.empty:
        return {wd: 0.0 for wd in range(7)}
    df["wd"] = pd.to_datetime(df["posting_date"]).dt.weekday
    total = df.groupby("wd")["credit"].sum()
    return {wd: float(total.get(wd, 0.0)) / int(count.get(wd, 1)) for wd in range(7)}


def scheduled_outflows(start: date, end: date) -> pd.DataFrame:
    """Money going out per day between start and end (inclusive): our active checks the bank has not cleared yet -- on their check date,
    or on `start` if that date is already past (the bank can take them any day) -- moved to the next banking day on weekends; plus
    bank-loan installments and credit-card dues from the payment schedule. Columns: day, track, payee, reference, amount."""
    checks = _read("""SELECT c.check_date, c.amount, cr.creditor_name AS payee, COALESCE(c.check_number, 'no number') AS reference,
                              CASE WHEN c.statement_id IS NOT NULL THEN 'Supplier' ELSE 'Lender' END AS track
                       FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
                       WHERE c.status = 'Active' AND c.bank_cleared_date IS NULL""")
    rows = []
    for _, r in checks.iterrows():
        d = max(next_banking_day(str(r["check_date"])[:10]), next_banking_day(start))
        if d <= end:
            rows.append({"day": d, "track": r["track"], "payee": r["payee"], "reference": r["reference"], "amount": float(r["amount"])})
    sched = get_payment_schedule_all_tracks()
    if not sched.empty:
        other = sched[sched["track"].isin(["Bank Loan", "Credit Card"])]
        for _, r in other.iterrows():
            d = next_banking_day(str(r["due_date"])[:10])
            if start <= d <= end:
                rows.append({"day": d, "track": r["track"], "payee": r["payee_name"], "reference": r["reference"], "amount": float(r["amount"])})
    return pd.DataFrame(rows, columns=["day", "track", "payee", "reference", "amount"])


def forecast(days: int = 60, today: date | None = None) -> dict:
    """{'start', 'opening', 'as_of', 'stale_days', 'buffer', 'daily': DataFrame(day, money_in, money_out, balance, below_buffer),
    'lowest': (day, balance), 'first_below': day | None}. Starts today from the latest bank balance."""
    today = today or date.today()
    pos = cash_position()
    if pos is None:
        return {}
    end = today + timedelta(days=days)
    inflow = expected_daily_inflow()
    out = scheduled_outflows(today, end)
    per_day = out.groupby("day")["amount"].sum() if not out.empty else pd.Series(dtype=float)
    buffer = get_buffer()
    bal, rows = pos["balance"], []
    for i in range(days + 1):
        d = today + timedelta(days=i)
        money_in = inflow.get(d.weekday(), 0.0) if i > 0 else 0.0             # today's deposits are not known yet: count from tomorrow
        money_out = float(per_day.get(d, 0.0))
        bal = bal + money_in - money_out
        rows.append({"day": d, "money_in": round(money_in, 2), "money_out": round(money_out, 2), "balance": round(bal, 2), "below_buffer": bal < buffer})
    daily = pd.DataFrame(rows)
    low = daily.loc[daily["balance"].idxmin()]
    below = daily[daily["below_buffer"]]
    return {"start": today, "opening": pos["balance"], "as_of": pos["as_of"], "stale_days": (today - pos["as_of"]).days, "buffer": buffer,
            "daily": daily, "outflows": out, "lowest": (low["day"], float(low["balance"])), "first_below": below["day"].iloc[0] if not below.empty else None}


# ------------------------------------------------------------------ float, inflows, unusual items, working capital
def float_summary(today: date | None = None) -> dict:
    """Our active checks not cleared by the bank: 'overdue' (dated on or before the last imported bank date -- they should have cleared)
    with age buckets and per payee, and 'ahead' (dated after it)."""
    pos = cash_position()
    if pos is None:
        return {}
    df = _read("""SELECT c.check_number, c.check_date, c.amount, cr.creditor_name AS payee FROM checks_issued c
                  JOIN creditors cr ON cr.creditor_id = c.creditor_id WHERE c.status = 'Active' AND c.bank_cleared_date IS NULL""")
    if df.empty:
        return {"overdue": df, "ahead": df, "buckets": pd.DataFrame(columns=["age", "checks", "amount"]), "by_payee": pd.DataFrame(), "as_of": pos["as_of"]}
    df["check_date"] = pd.to_datetime(df["check_date"]).dt.date
    overdue = df[df["check_date"] <= pos["as_of"]].copy()
    ahead = df[df["check_date"] > pos["as_of"]].copy()
    overdue["days"] = overdue["check_date"].apply(lambda d: (pos["as_of"] - d).days)
    bins = [(-1, 7, "0-7 days"), (7, 30, "8-30 days"), (30, 90, "31-90 days"), (90, 10**6, "Over 90 days")]
    buckets = pd.DataFrame([{"age": lab, "checks": int(((overdue["days"] > lo) & (overdue["days"] <= hi)).sum()),
                             "amount": float(overdue.loc[(overdue["days"] > lo) & (overdue["days"] <= hi), "amount"].sum())} for lo, hi, lab in bins])
    by_payee = overdue.groupby("payee")["amount"].agg(["count", "sum"]).reset_index().rename(columns={"count": "checks", "sum": "amount"}).sort_values("amount", ascending=False)
    return {"overdue": overdue.sort_values("days", ascending=False), "ahead": ahead, "buckets": buckets, "by_payee": by_payee, "as_of": pos["as_of"]}


def inflow_trends() -> dict:
    """Money in: by week and month (with type split), the weekly average and slow weeks (under 75% of the average)."""
    df = _read("SELECT posting_date, description, credit FROM bank_transactions WHERE kind = 'DEPOSIT'")
    if df.empty:
        return {}
    df["day"] = pd.to_datetime(df["posting_date"])
    df["type"] = df["description"].apply(deposit_type)
    weekly = df.groupby(pd.Grouper(key="day", freq="W-SUN"))["credit"].sum().reset_index().rename(columns={"day": "week_ending", "credit": "money_in"})
    # a week only partly covered by the imported statements (at either end) is shown but not used for the average or the slow flag
    cov = _read("SELECT MIN(posting_date) AS a, MAX(posting_date) AS b FROM bank_transactions").iloc[0]
    first, last = pd.Timestamp(cov["a"]), pd.Timestamp(cov["b"])
    # partial = the statements start after that week's Monday, or end before its Friday (no deposits post on weekends)
    weekly["partial"] = ((weekly["week_ending"] - pd.Timedelta(days=6)) < first) | ((weekly["week_ending"] - pd.Timedelta(days=2)) > last)
    full = weekly[~weekly["partial"]]
    avg = float(full["money_in"].mean()) if not full.empty else float(weekly["money_in"].mean()) if not weekly.empty else 0.0
    weekly["slow"] = (~weekly["partial"]) & (weekly["money_in"] < 0.75 * avg)
    weekly["label"] = weekly["week_ending"].dt.strftime("%b %d") + weekly["partial"].map({True: " (part)", False: ""})
    monthly = df.groupby([df["day"].dt.to_period("M").astype(str), "type"])["credit"].sum().unstack(fill_value=0.0)
    by_type = df.groupby("type")["credit"].agg(["count", "sum"]).reset_index().rename(columns={"count": "lines", "sum": "amount"}).sort_values("amount", ascending=False)
    return {"weekly": weekly, "weekly_average": avg, "monthly": monthly, "by_type": by_type, "total": float(df["credit"].sum())}


def unusual_items(big_factor: float = 5.0) -> dict:
    """Returned deposited checks; bank debits that are not our checks (debit memos, bills payments, other); lines far larger than usual for
    their kind (more than `big_factor` x the median); checks cleared with an amount different from ours."""
    df = _read("SELECT txn_id, posting_date, check_number, description, debit, credit, kind, match_status, reviewed FROM bank_transactions")
    if df.empty:
        return {}
    df["amount"] = df["debit"] + df["credit"]
    returned = df[df["kind"] == "RETURNED_CHECK"]
    debits = df[df["kind"].isin(["DEBIT_MEMO", "BILLS_PAYMENT", "OTHER"]) & (df["debit"] > 0)]
    # compare like with like: cash deposits with cash deposits, transfers with transfers, bank debits with bank debits
    df["group"] = [deposit_type(d) if k == "DEPOSIT" else k for d, k in zip(df["description"], df["kind"])]
    med = df.groupby("group")["amount"].transform("median")
    big = df[(df["amount"] > big_factor * med) & (df["kind"].isin(["DEPOSIT", "DEBIT_MEMO", "BILLS_PAYMENT", "OTHER"]))]
    mismatch = df[df["match_status"].isin(["amount_mismatch", "cancelled_check_cleared"])]
    debits_by_month = debits.assign(month=pd.to_datetime(debits["posting_date"]).dt.to_period("M").astype(str)).groupby(["month", "kind"])["debit"].sum().unstack(fill_value=0.0) if not debits.empty else pd.DataFrame()
    return {"returned": returned, "debits": debits, "debits_by_month": debits_by_month, "big": big.sort_values("amount", ascending=False), "mismatch": mismatch}


def working_capital(today: date | None = None) -> dict:
    """Cash vs payables: for 7 / 30 / 60 / 90 days ahead -- what is due (same outflows as the forecast), cash cover (balance / due) and
    what is left; days of cash (balance / average daily outflow over the next 30 days); DPO (average days suppliers wait, as on the
    Dashboard)."""
    today = today or date.today()
    pos = cash_position()
    if pos is None:
        return {}
    out = scheduled_outflows(today, today + timedelta(days=90))
    rows = []
    for n in (7, 30, 60, 90):
        due = float(out.loc[out["day"] <= today + timedelta(days=n), "amount"].sum()) if not out.empty else 0.0
        rows.append({"window": f"Next {n} days", "due": due, "cover": (pos["balance"] / due) if due else None, "left": pos["balance"] - due})
    due30 = rows[1]["due"]
    from engines.dashboard_kpi_service import get_average_dpo_suppliers

    return {"balance": pos["balance"], "as_of": pos["as_of"], "windows": pd.DataFrame(rows),
            "days_of_cash": (pos["balance"] / (due30 / 30.0)) if due30 else None, "dpo": get_average_dpo_suppliers(),
            "by_track": out.groupby("track")["amount"].sum().to_dict() if not out.empty else {}}
