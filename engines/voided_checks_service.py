"""Voided, replaced and spoiled checks in one list (owner, 2026-09-30: "I voided a check, why is it not in the voided checks?").

A voided check used to show only inside its own batch and in the raw audit log; the "Spoiled checks" list holds misprinted leaves only. This is
the single place that answers "which checks did we void, replace or spoil, when, why, and what replaced them" -- and warns when the bank paid a
check we had voided (a number on a bank line that our book says is dead)."""
from __future__ import annotations

from engines.common import get_conn
import os
import sqlite3
from datetime import date

import pandas as pd

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

COLUMNS = ["what", "check_number", "check_date", "amount", "payee", "reference", "happened_at", "reason", "replaced_by",
           "bank_paid_anyway", "handwritten", "check_id"]

_VOIDED_SQL = """
SELECT CASE c.status WHEN 'Replaced' THEN 'Replaced' ELSE 'Voided' END AS what,
       c.check_number, c.check_date, c.amount, cr.creditor_name AS payee,
       COALESCE(os.statement_ref, ln.note_ref) AS reference,
       CASE c.status
            WHEN 'Replaced' THEN datetime(c.replaced_at, '+8 hours')
            ELSE COALESCE(datetime(c.cancelled_at, '+8 hours'),
                          (SELECT datetime(MAX(a.timestamp), '+8 hours') FROM audit_logs a
                            WHERE a.user_action = 'CHECK_CANCELLED' AND a.entity_name = 'checks_issued' AND a.entity_id = c.check_id))
       END AS happened_at,
       CASE c.status WHEN 'Replaced' THEN c.replacement_reason ELSE c.cancel_reason END AS reason,
       COALESCE(rep.check_number, '') AS replaced_by,
       c.bank_cleared_date,
       (SELECT MIN(t.posting_date) FROM bank_transactions t WHERE t.check_number = c.check_number AND t.debit > 0) AS bank_line_date,
       c.is_handwritten AS handwritten, c.check_id, c.creditor_id
FROM checks_issued c
JOIN creditors cr ON cr.creditor_id = c.creditor_id
LEFT JOIN order_statements os ON os.statement_id = c.statement_id
LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
LEFT JOIN checks_issued rep ON rep.check_id = c.replacement_check_id
WHERE c.status IN ('Cancelled', 'Replaced')
"""


def _bank_note(cleared, line_date) -> str:
    when = cleared or line_date
    return f"Yes - bank cleared it {str(when)[:10]}" if when else ""


def voided_checks(date_from: date | None = None, date_to: date | None = None, creditor_id: int | None = None) -> pd.DataFrame:
    """One row per voided, replaced or spoiled check, newest first. Filters: happened between date_from and date_to (inclusive), one payee.
    Spoiled leaves belong to no payee, so a payee filter leaves them out."""
    with get_conn(DB_PATH) as conn:
        voided = pd.read_sql_query(_VOIDED_SQL, conn)
        spoiled = pd.read_sql_query(
            """SELECT 'Spoiled' AS what, s.check_number, NULL AS check_date, NULL AS amount, 'Error Checks Only (for control)' AS payee,
                      '' AS reference, datetime(s.logged_at, '+8 hours') AS happened_at, s.reason, '' AS replaced_by,
                      (SELECT MIN(t.posting_date) FROM bank_transactions t WHERE t.check_number = s.check_number AND t.debit > 0) AS bank_line_date,
                      0 AS handwritten, NULL AS check_id, NULL AS creditor_id
               FROM spoiled_checks s""", conn)
    voided["bank_paid_anyway"] = [_bank_note(c, b) for c, b in zip(voided["bank_cleared_date"], voided["bank_line_date"])]
    spoiled["bank_paid_anyway"] = [_bank_note(None, b) for b in spoiled["bank_line_date"]]
    frames = [f for f in (voided, spoiled) if not f.empty]
    if not frames:
        return pd.DataFrame(columns=COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["reason"] = df["reason"].fillna("")
    df["handwritten"] = df["handwritten"].map(lambda v: "Yes" if v and int(v) else "")
    if creditor_id is not None:
        df = df[df["creditor_id"] == int(creditor_id)]
    day = pd.to_datetime(df["happened_at"], errors="coerce").dt.date
    if date_from is not None:
        df = df[day.reindex(df.index).map(lambda d: d is not pd.NaT and pd.notna(d) and d >= date_from)]
        day = pd.to_datetime(df["happened_at"], errors="coerce").dt.date
    if date_to is not None:
        df = df[day.reindex(df.index).map(lambda d: pd.notna(d) and d <= date_to)]
    df = df.sort_values("happened_at", ascending=False, na_position="last", kind="stable")
    return df[COLUMNS].reset_index(drop=True)
