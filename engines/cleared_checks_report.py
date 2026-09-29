"""Cleared checks, from the bank reconciliation (owner, 2026-09-29: "create additional standard report for cleared checks from bank recon").

Every check the imported bank statements show as cleared (checks_issued.bank_cleared_date), with the bank's date, days from our check date
to the bank's clearing date, how the bank line was matched, and any difference between the bank's amount and our record. The bank is the
truth: a check's clearing date here is the bank's, never inferred from its check date passing. Totals by payee and by week for
reconciling and for planning the cash of post-dated checks.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta

import pandas as pd

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

COLUMNS = ["cleared_date", "check_number", "payee", "amount", "check_date", "days_to_clear", "reference", "bank_amount", "difference",
           "matched_how", "status", "check_id", "creditor_id"]


def _how(match_status, resolution) -> str:
    if match_status == "matched":
        return "Matched automatically"
    if resolution in ("Adjusted to the bank", "Corrected to the bank"):
        return "Adjusted to the bank"
    if resolution == "Suggested match accepted":
        return "Suggested match accepted"
    return "Linked by hand"


def cleared_checks(date_from: date | None = None, date_to: date | None = None, creditor_id: int | None = None) -> pd.DataFrame:
    """One row per cleared check, oldest clearing first. Filters: cleared between date_from and date_to (inclusive), one payee."""
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT c.check_id, c.creditor_id, cr.creditor_name AS payee, c.check_number, c.check_date, c.amount, c.status,
                      c.bank_cleared_date AS cleared_date, t.debit AS bank_amount, t.match_status, t.resolution,
                      COALESCE(os.statement_ref, ln.note_ref) AS reference
               FROM checks_issued c
               JOIN creditors cr ON cr.creditor_id = c.creditor_id
               LEFT JOIN bank_transactions t ON t.txn_id = c.bank_txn_id
               LEFT JOIN order_statements os ON os.statement_id = c.statement_id
               LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
               WHERE c.bank_cleared_date IS NOT NULL
               ORDER BY c.bank_cleared_date, cr.creditor_name, c.check_number""", conn)
    if df.empty:
        return pd.DataFrame(columns=COLUMNS)
    df["cleared_date"] = pd.to_datetime(df["cleared_date"]).dt.date
    df["check_date"] = pd.to_datetime(df["check_date"]).dt.date
    df["days_to_clear"] = [(a - b).days for a, b in zip(df["cleared_date"], df["check_date"])]
    df["bank_amount"] = df["bank_amount"].where(df["bank_amount"].notna(), df["amount"])
    df["difference"] = (df["bank_amount"] - df["amount"]).round(2)
    df["matched_how"] = [_how(m, r) for m, r in zip(df["match_status"], df["resolution"])]
    if date_from is not None:
        df = df[df["cleared_date"] >= date_from]
    if date_to is not None:
        df = df[df["cleared_date"] <= date_to]
    if creditor_id is not None:
        df = df[df["creditor_id"] == int(creditor_id)]
    return df[COLUMNS].reset_index(drop=True)


def cleared_span() -> tuple[date, date] | None:
    """(earliest, latest) bank clearing date on file, or None."""
    with sqlite3.connect(DB_PATH) as conn:
        a, b = conn.execute("SELECT MIN(bank_cleared_date), MAX(bank_cleared_date) FROM checks_issued WHERE bank_cleared_date IS NOT NULL").fetchone()
    return (date.fromisoformat(a), date.fromisoformat(b)) if a else None


def by_payee(df: pd.DataFrame) -> pd.DataFrame:
    """What cleared per payee: number of checks, total, average days to clear. Biggest first."""
    if df.empty:
        return pd.DataFrame(columns=["payee", "checks", "total", "avg_days"])
    g = df.groupby("payee").agg(checks=("check_id", "size"), total=("amount", "sum"), avg_days=("days_to_clear", "mean")).reset_index()
    return g.sort_values(["total", "payee"], ascending=[False, True]).reset_index(drop=True)


def by_week(df: pd.DataFrame) -> pd.DataFrame:
    """What the bank cleared per week (weeks start on Monday): number of checks and total."""
    if df.empty:
        return pd.DataFrame(columns=["week_start", "checks", "total"])
    weeks = [d - timedelta(days=d.weekday()) for d in df["cleared_date"]]
    g = df.assign(week_start=weeks).groupby("week_start").agg(checks=("check_id", "size"), total=("amount", "sum")).reset_index()
    return g.sort_values("week_start").reset_index(drop=True)
