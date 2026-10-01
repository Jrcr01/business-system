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
    bank-loan installments and credit-card dues from the payment schedule; plus ESTIMATED card dues for the months whose statement is
    not in yet (engines/cc_forecast_service.py, owner 2026-09-29 -- track 'Credit Card (estimated)'). Columns: day, track, payee,
    reference, amount."""
    checks = _read("""SELECT c.check_date, c.amount, cr.creditor_name AS payee, COALESCE(c.check_number, 'no number') AS reference,
                              CASE WHEN c.statement_id IS NOT NULL THEN 'Supplier' ELSE 'Lender' END AS track
                       FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
                       WHERE c.status = 'Active' AND c.bank_cleared_date IS NULL AND c.settled_outside_at IS NULL""")
    rows = []
    for _, r in checks.iterrows():
        d = max(next_banking_day(str(r["check_date"])[:10]), next_banking_day(start))
        if d <= end:
            rows.append({"day": d, "track": r["track"], "payee": r["payee"], "reference": r["reference"], "amount": float(r["amount"])})
    sched = get_payment_schedule_all_tracks()
    if not sched.empty:
        sched, _ = split_card_rows(sched)                # cards paid outside the bank do not take from this balance
        other = sched[sched["track"].isin(["Bank Loan", "Credit Card"])]
        for _, r in other.iterrows():
            d = next_banking_day(str(r["due_date"])[:10])
            if start <= d <= end:
                rows.append({"day": d, "track": r["track"], "payee": r["payee_name"], "reference": r["reference"], "amount": float(r["amount"])})
    rows += estimated_outflows(start, end)
    return pd.DataFrame(rows, columns=["day", "track", "payee", "reference", "amount"])


def _card_ids_by_reference() -> dict:
    """{'Cardholder - Card - 1234': card_id} -- the label the payment schedule uses for a card statement."""
    df = _read("""SELECT cc.card_id, CASE WHEN ch.name IS NOT NULL THEN ch.name || ' - ' || cc.card_name || ' - ' || cc.card_number_last4
                                        ELSE cc.card_name || ' - ' || cc.card_number_last4 END AS reference
                  FROM credit_cards cc LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id""")
    return dict(zip(df["reference"], df["card_id"])) if not df.empty else {}


def split_card_rows(sched: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(schedule without the credit-card rows of cards NOT paid from the Metrobank account, those rows). Owner, 2026-09-29: the planner and
    the Cash forecast plan against the Metrobank account only; a card counts against it only when marked 'paid from Metrobank'."""
    from engines.cc_service import paid_from_bank_ids

    if sched.empty or "Credit Card" not in set(sched["track"]):
        return sched, sched.iloc[0:0]
    bank_cards, ref = paid_from_bank_ids(), _card_ids_by_reference()
    outside = (sched["track"] == "Credit Card") & ~sched["reference"].map(lambda r: ref.get(r) in bank_cards)
    return sched[~outside], sched[outside]


def outside_card_dues(start: date, end: date) -> dict:
    """Card dues in the window that are paid outside the Metrobank account (not deducted from its balance):
    {'recorded': on statements, 'estimated': not yet on a statement, 'cards': how many cards}."""
    from engines.cc_forecast_service import estimated_card_dues
    from engines.cc_service import paid_from_bank_ids

    bank_cards, ref = paid_from_bank_ids(), _card_ids_by_reference()
    recorded, cards = 0.0, set()
    sched = get_payment_schedule_all_tracks()
    if not sched.empty:
        _, gone = split_card_rows(sched)
        gone = gone[(pd.to_datetime(gone["due_date"]).dt.date >= start) & (pd.to_datetime(gone["due_date"]).dt.date <= end)]
        recorded = float(gone["amount"].sum()) if not gone.empty else 0.0
        cards |= {ref.get(r) for r in gone["reference"] if ref.get(r) is not None}
    est = estimated_card_dues(start, end, today=start)
    est = est[~est["card_id"].isin(bank_cards)] if not est.empty else est
    cards |= set(est["card_id"]) if not est.empty else set()
    return {"recorded": recorded, "estimated": float(est["amount"].sum()) if not est.empty else 0.0, "cards": len(cards)}


def estimated_outflows(start: date, end: date) -> list[dict]:
    """Estimated card dues (not yet on a statement) as outflow rows, on the next banking day like the other dues -- only for cards paid
    from the Metrobank account (owner, 2026-09-29)."""
    from engines.cc_forecast_service import estimated_card_dues
    from engines.cc_service import paid_from_bank_ids

    rows = []
    est = estimated_card_dues(start, end, today=start)
    if not est.empty:
        est = est[est["card_id"].isin(paid_from_bank_ids())]
    for _, r in est.iterrows():
        d = next_banking_day(str(r["due"])[:10])
        if start <= d <= end:
            rows.append({"day": d, "track": r["track"], "payee": r["payee"], "reference": r["reference"], "amount": float(r["amount"])})
    return rows


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
                  JOIN creditors cr ON cr.creditor_id = c.creditor_id WHERE c.status = 'Active' AND c.bank_cleared_date IS NULL AND c.settled_outside_at IS NULL""")
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
    mismatch = df[df["match_status"].isin(["cleared_twice", "amount_mismatch", "cancelled_check_cleared"])]
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


# ------------------------------------------------------------------ summaries for the Dashboard and the overall report (2026-09-28)
FORECAST_FRESH_DAYS = 14               # an older bank balance is too stale to forecast from


def dashboard_bank_summary(today: date | None = None) -> dict | None:
    """The Dashboard's Bank and cash panel: the latest bank balance and its date; the share of our numbered active checks dated within the
    imported statements that the bank has confirmed cleared; the checks past their date the bank has not cleared (float); bank lines still
    needing a look; the lowest forecast balance in 30 days vs the buffer (only while the balance is at most 14 days old). None when no
    statement is imported."""
    from engines.bank_statement_service import exceptions, not_yet_cleared, statement_coverage

    today = today or date.today()
    pos = cash_position()
    if pos is None:
        return None
    first, last = statement_coverage()
    cov = _read("""SELECT COUNT(*) AS n, COALESCE(SUM(CASE WHEN bank_cleared_date IS NOT NULL THEN 1 ELSE 0 END), 0) AS cleared FROM checks_issued
                   WHERE status = 'Active' AND check_number IS NOT NULL AND check_number != '' AND check_date BETWEEN ? AND ?""",
                (first.isoformat(), last.isoformat())).iloc[0]
    waiting = not_yet_cleared()
    age = (today - pos["as_of"]).days
    fc = forecast(30, today) if age <= FORECAST_FRESH_DAYS else {}
    return {"balance": pos["balance"], "as_of": pos["as_of"], "age_days": age,
            "checks_in_period": int(cov["n"]), "checks_cleared": int(cov["cleared"]),
            "pct_cleared": (float(cov["cleared"]) / int(cov["n"]) * 100.0) if int(cov["n"]) else None,
            "float_count": len(waiting), "float_amount": float(sum(w["amount"] for w in waiting)),
            "open_items": sum(1 for e in exceptions() if not e["reviewed"]),
            "lowest": fc["lowest"] if fc else None, "first_below": fc.get("first_below") if fc else None, "buffer": get_buffer()}


def period_bank_summary(start: date, end: date, prev_start: date, prev_end: date) -> dict:
    """Bank and cash for a report period (overall report): whether imported statements cover it (fully / partly / not at all), the bank
    balance at the period end (the last bank line on or before it), money in vs the previous period, checks the bank cleared in the period
    vs checks dated in it, the float at the period end (numbered active checks dated on or before it, not cleared by then), bounced deposits,
    and bank lines still needing a look. When nothing covers the period only 'covered' = 'none' and the coverage dates are returned."""
    from engines.bank_statement_service import EXCEPTION_STATUSES

    # coverage = the statements' own periods (a statement to Feb 28 covers February even when its last bank line is on Friday the 27th)
    cov = _read("SELECT MIN(period_from) AS a, MAX(period_to) AS b FROM bank_statement_imports").iloc[0]
    first = date.fromisoformat(str(cov["a"])[:10]) if cov["a"] else None
    last = date.fromisoformat(str(cov["b"])[:10]) if cov["b"] else None
    if first is None or first > end or last < start:
        return {"covered": "none", "first": first, "last": last}
    covered = "full" if first <= start and last >= end else "part"
    as_of = min(end, last)
    bal = _read("SELECT posting_date, balance FROM bank_transactions WHERE balance IS NOT NULL AND posting_date <= ? ORDER BY posting_date DESC, txn_id DESC LIMIT 1",
                (as_of.isoformat(),))

    def money_in(a: date, b: date) -> float:
        return float(_read("SELECT COALESCE(SUM(credit), 0) AS t FROM bank_transactions WHERE kind = 'DEPOSIT' AND posting_date BETWEEN ? AND ?",
                           (a.isoformat(), b.isoformat()))["t"][0])

    prev_covered = first <= prev_start and last >= prev_end
    cleared = _read("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS t FROM checks_issued WHERE bank_cleared_date BETWEEN ? AND ?",
                    (start.isoformat(), end.isoformat())).iloc[0]
    issued = _read("SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS t FROM checks_issued WHERE status = 'Active' AND check_date BETWEEN ? AND ?",
                   (start.isoformat(), end.isoformat())).iloc[0]
    flt = _read("""SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS t FROM checks_issued
                   WHERE status = 'Active' AND check_number IS NOT NULL AND check_number != '' AND check_date <= ?
                     AND (bank_cleared_date IS NULL OR bank_cleared_date > ?) AND (settled_outside_at IS NULL OR settled_outside_at > ?)""",
                (as_of.isoformat(), as_of.isoformat(), as_of.isoformat())).iloc[0]
    bounced = _read("SELECT COUNT(*) AS n, COALESCE(SUM(debit), 0) AS t FROM bank_transactions WHERE kind = 'RETURNED_CHECK' AND posting_date BETWEEN ? AND ?",
                    (start.isoformat(), end.isoformat())).iloc[0]
    open_n = int(_read("SELECT COUNT(*) AS n FROM bank_transactions WHERE reviewed = 0 AND posting_date BETWEEN ? AND ? AND match_status IN (%s)"
                       % ",".join("?" * len(EXCEPTION_STATUSES)), (start.isoformat(), end.isoformat()) + EXCEPTION_STATUSES)["n"][0])
    return {"covered": covered, "first": first, "last": last, "as_of": as_of,
            "balance": float(bal["balance"][0]) if not bal.empty else None, "balance_date": date.fromisoformat(bal["posting_date"][0]) if not bal.empty else None,
            "money_in": money_in(start, as_of), "money_in_prev": money_in(prev_start, prev_end) if prev_covered else None,
            "cleared_n": int(cleared["n"]), "cleared_amount": float(cleared["t"]), "issued_n": int(issued["n"]), "issued_amount": float(issued["t"]),
            "float_n": int(flt["n"]), "float_amount": float(flt["t"]), "bounced_n": int(bounced["n"]), "bounced_amount": float(bounced["t"]),
            "open_items": open_n}
