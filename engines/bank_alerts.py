"""Bank and cash alerts (2026-09-28, reconciliation v2, owner-approved). One place that decides what to warn about, shown on Payment run and
the Dashboard:

  * stale deposits   -- our checks not cleared by the bank N days after their check date (setting `stale_deposit_days`, default 7: the
                        owner uploads the bank statement weekly; first asked for 1 day "because we are always short of cash", then 7).
                        Judged against the last imported bank date, since the days after it are not known yet.
  * large debits     -- a bank debit that is not our check, matched nothing and is not resolved yet, more than `recon_debit_alert_factor`
                        (default 5) times the usual size of that kind of debit (the unusual-items rule).
  * low cash ahead   -- the cash forecast drops under the owner's cash buffer within 7 days (only while the bank balance is recent enough
                        to forecast from: at most FORECAST_MAX_AGE days old).
  * old statement    -- the last bank statement was imported more than 7 days ago.
Every alert is a dict: key, level (error / warning / info), message, count, amount.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta

import pandas as pd

from engines.settings_store import get_number, set_setting

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")
STALE_DAYS_KEY, DEFAULT_STALE_DAYS = "stale_deposit_days", 7
DEBIT_FACTOR_KEY, DEFAULT_DEBIT_FACTOR = "recon_debit_alert_factor", 5.0
STATEMENT_MAX_AGE = 7            # days since the last import before reminding to download a new statement
FORECAST_DAYS = 7                # how far ahead the low-cash alert looks
FORECAST_MAX_AGE = 14            # an older bank balance is too stale to forecast from (the old-statement alert covers it)


def stale_days() -> int:
    return max(0, int(get_number(STALE_DAYS_KEY, DEFAULT_STALE_DAYS)))


def debit_factor() -> float:
    return max(1.0, get_number(DEBIT_FACTOR_KEY, DEFAULT_DEBIT_FACTOR))


def set_alert_settings(days: int, factor: float) -> None:
    set_setting(STALE_DAYS_KEY, int(days), "bank", "Alert when a check is not cleared by the bank this many days after its check date")
    set_setting(DEBIT_FACTOR_KEY, float(factor), "bank", "Alert when an unexpected bank debit is this many times the usual size")


def stale_deposits(days: int | None = None) -> list[dict]:
    """Our numbered active checks still not cleared by the bank `days` or more days after their check date (to the last bank date)."""
    from engines.bank_statement_service import not_yet_cleared

    n = stale_days() if days is None else int(days)
    return [r for r in not_yet_cleared() if r["days_waiting"] >= n]


def large_debits(factor: float | None = None) -> pd.DataFrame:
    """Unresolved bank debits that are not our checks and matched nothing, more than `factor` x the median debit of the same kind."""
    f = debit_factor() if factor is None else float(factor)
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query("""SELECT txn_id, posting_date, description, kind, debit, match_status, reviewed FROM bank_transactions
                                  WHERE kind IN ('DEBIT_MEMO', 'BILLS_PAYMENT', 'OTHER') AND debit > 0""", conn)
    if df.empty:
        return df
    df["usual"] = df.groupby("kind")["debit"].transform("median")
    big = df[(df["match_status"] == "unmatched_debit") & (df["reviewed"] == 0) & (df["debit"] > f * df["usual"])]
    return big.sort_values("debit", ascending=False)


def low_cash_ahead(today: date | None = None) -> dict | None:
    """{'day', 'lowest', 'buffer', 'as_of'} when the forecast goes under the cash buffer within FORECAST_DAYS days, else None."""
    from engines.cash_insights_service import forecast

    today = today or date.today()
    f = forecast(days=FORECAST_DAYS, today=today)
    if not f or f["stale_days"] > FORECAST_MAX_AGE or f["first_below"] is None:
        return None
    return {"day": f["first_below"], "lowest": f["lowest"][1], "buffer": f["buffer"], "as_of": f["as_of"]}


def statement_age(today: date | None = None) -> dict | None:
    """{'age', 'period_to'} when the last statement was imported more than STATEMENT_MAX_AGE days ago, else None."""
    from engines.bank_statement_service import last_import

    li = last_import()
    if not li:
        return None
    age = ((today or date.today()) - date.fromisoformat(str(li["imported_at"])[:10])).days
    return {"age": age, "period_to": li["period_to"]} if age > STATEMENT_MAX_AGE else None


def all_alerts(today: date | None = None) -> list[dict]:
    """Every bank and cash alert that applies now, most urgent first. Empty when nothing is imported yet (or the tables are missing)."""
    out = []
    try:
        low = low_cash_ahead(today)
        if low:
            out.append({"key": "low_cash", "level": "error", "count": 1, "amount": low["lowest"],
                        "message": f"Cash may drop under your buffer (PHP {low['buffer']:,.2f}) on {low['day']:%a, %b %d} — lowest forecast "
                                   f"PHP {low['lowest']:,.2f} in the next {FORECAST_DAYS} days (from the bank balance of {low['as_of']:%b %d})."})
        big = large_debits()
        if not big.empty:
            out.append({"key": "large_debit", "level": "warning", "count": len(big), "amount": float(big["debit"].sum()),
                        "message": f"{len(big)} large bank debit(s) not explained yet, PHP {big['debit'].sum():,.2f} — biggest "
                                   f"PHP {big['debit'].iloc[0]:,.2f} on {big['posting_date'].iloc[0]} ({big['description'].iloc[0]}). "
                                   "Tag them under Bank and cash → Reconciliation."})
        stale = stale_deposits()
        if stale:
            total = sum(float(r["amount"]) for r in stale)
            out.append({"key": "stale_deposit", "level": "warning", "count": len(stale), "amount": total,
                        "message": f"{len(stale)} check(s) not cleared by the bank {stale_days()}+ days after their check date, PHP {total:,.2f} "
                                   f"— oldest: {stale[0]['check_number']} to {stale[0]['creditor_name']} ({stale[0]['days_waiting']} days). "
                                   "The supplier may not have deposited it yet."})
        old = statement_age(today)
        if old:
            out.append({"key": "old_statement", "level": "info", "count": 1, "amount": 0.0,
                        "message": f"The last bank statement was imported {old['age']} days ago (covers up to {old['period_to']}). Download the "
                                   "latest one from MBOS so cleared checks and your cash position stay current."})
    except sqlite3.OperationalError:          # a database without the bank tables yet
        return []
    return out
