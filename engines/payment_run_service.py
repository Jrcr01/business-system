"""Payment run page (2026-09-27, per user: "too many actions before the operation cycle ends ... I go back and forth tabs" and
"add quick report like what's due today"). Read-only: gathers what is waiting at each stage of the supplier payment cycle and
today's quick report. No new rules -- every number comes from the same definitions the other screens use:

* due dates / next 7 days: reports_service.get_payment_schedule_all_tracks() (Dashboard "Due in next 7 days" and the Reports
  schedule use the same function; weekend dues move to Monday);
* stages: the Order statement stage rule (Draft / Parked / Issued), check_run_service's pending run and unprinted runs, and the
  "since last check print" range of Print statements in bulk.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, timedelta

import pandas as pd

from engines.memo import until_saved
from engines.check_run_service import get_pending_check_run, get_unprinted_batches
from engines.last_modified_sql import statement_modified_sql
from engines.reports_service import get_payment_schedule_all_tracks, next_banking_day
from engines.common import get_conn
from engines.clock import manila_today

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

TRACKS = ["Supplier", "Lender", "Bank Loan", "Credit Card"]


def _read(sql: str, params: tuple = ()) -> pd.DataFrame:
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(sql, conn, params=params)


# ------------------------------------------------------------------ quick report
def get_quick_report(today: date | None = None) -> dict:
    """{'today', 'bank_day', 'due_today', 'next7', 'next7_total', 'checks_today', 'overdue'} -- DataFrames except the dates/totals.

    due_today    everything still owed that is paid on bank_day -- today, or on a Saturday/Sunday the Monday after (a weekend
                 due date is paid the next banking day, as in Reports), all tracks.
    next7        the Dashboard's "Due in next 7 days" (due today .. today+7, all tracks), one row per payment day, a column per
                 track and a Total -- next7_total equals the Dashboard figure.
    checks_today active checks dated today (supplier and lender) -- what the bank account must cover today.
    overdue      past their due date and not covered: supplier invoices with no check for their balance, loan installments with
                 no check, and credit card statements still owed (the Dashboard's card alerts with a due date before today)."""
    today = today or manila_today()
    bank_day = next_banking_day(today)          # on a Saturday/Sunday, "due today" means the Monday it is actually paid
    sched = get_payment_schedule_all_tracks()
    if sched.empty:
        sched = pd.DataFrame(columns=["due_date", "payee_name", "track", "reference", "batch", "amount", "actual_payment_date"])
    pay_day = pd.to_datetime(sched["actual_payment_date"]).dt.date if not sched.empty else pd.Series([], dtype=object)
    due_today = sched[pay_day == bank_day].reset_index(drop=True)

    # Next 7 days = the Dashboard's "Due in next 7 days" (due date today .. today+7, all tracks), grouped by the day it is paid.
    end = today + timedelta(days=7)
    window = sched[sched["due_date"].astype(str) <= end.isoformat()].copy()
    rows = []
    for d in sorted(set(pd.to_datetime(window["actual_payment_date"]).dt.date)):
        part = window[pd.to_datetime(window["actual_payment_date"]).dt.date == d]
        row = {"day": d}
        for t in TRACKS:
            row[t] = float(part.loc[part["track"] == t, "amount"].sum())
        row["Total"] = float(part["amount"].sum())
        row["items"] = int(len(part))
        rows.append(row)
    next7 = pd.DataFrame(rows, columns=["day", *TRACKS, "Total", "items"])

    checks_today = _read(
        """SELECT c.check_id, COALESCE(c.check_number, '') AS check_number, cr.creditor_name AS payee,
                  CASE WHEN c.statement_id IS NOT NULL THEN 'Supplier' ELSE 'Lender' END AS track,
                  COALESCE(os.statement_ref, ln.note_ref) AS reference, c.amount, c.statement_id, c.loan_note_id,
                  CASE WHEN c.printed_at IS NULL THEN 'Parked' ELSE 'Exported' END AS state
           FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
           LEFT JOIN order_statements os ON os.statement_id = c.statement_id
           LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
           WHERE c.status = 'Active' AND c.check_date = ?
           ORDER BY cr.creditor_name, c.amount""",
        (today.isoformat(),),
    )

    overdue = _read(
        """SELECT 'Supplier invoice' AS kind, cr.creditor_name AS payee, i.doc_ref || COALESCE(' · ' || os.statement_ref, '') AS reference,
                  i.due_date, i.amount - COALESCE((SELECT SUM(a.amount) FROM invoice_check_allocations a
                                                   JOIN checks_issued k ON k.check_id = a.check_id
                                                   WHERE a.invoice_id = i.invoice_id AND k.status = 'Active'), 0) AS amount,
                  i.statement_id, NULL AS loan_note_id
           FROM invoices i JOIN creditors cr ON cr.creditor_id = i.creditor_id
           LEFT JOIN order_statements os ON os.statement_id = i.statement_id
           WHERE i.due_date < ? AND i.status != 'Cancelled' AND COALESCE(os.status, '') != 'Cancelled' AND i.paid_via_card_id IS NULL
             AND i.amount - COALESCE((SELECT SUM(a.amount) FROM invoice_check_allocations a
                                      JOIN checks_issued k ON k.check_id = a.check_id
                                      WHERE a.invoice_id = i.invoice_id AND k.status = 'Active'), 0) > 0.005
           UNION ALL
           SELECT 'Loan installment', cr.creditor_name, ln.note_ref || ' #' || li.installment_no, li.due_date,
                  COALESCE(li.principal, 0) + COALESCE(li.interest, 0), NULL, ln.loan_note_id
           FROM loan_installments li JOIN loan_notes ln ON ln.loan_note_id = li.loan_note_id JOIN creditors cr ON cr.creditor_id = ln.creditor_id
           WHERE li.due_date < ? AND li.check_id IS NULL AND COALESCE(li.status, '') NOT IN ('Paid', 'Cancelled')
             AND COALESCE(ln.status, '') NOT IN ('Cancelled', 'Settled')
           UNION ALL
           SELECT 'Credit card', bank_issuer, card_name || ' - ' || card_number_last4, due_date, total_amount_due, NULL, NULL
           FROM v_cc_due_alerts WHERE due_date < ?
           ORDER BY 4""",
        (today.isoformat(), today.isoformat(), today.isoformat()),
    )
    return {"today": today, "bank_day": bank_day, "due_today": due_today, "next7": next7, "next7_total": float(next7["Total"].sum()) if not next7.empty else 0.0,
            "checks_today": checks_today, "overdue": overdue}


# ------------------------------------------------------------------ cycle stages
def last_check_print_day() -> str | None:
    """The day checks were last exported/printed -- the start of 'Since last check print' (same query as Print statements in bulk)."""
    v = _read("SELECT DATE(MAX(printed_at), '+8 hours') AS d FROM checks_issued")["d"][0]
    return str(v)[:10] if v else None


@until_saved('engines.payment_run_service')
def get_run_stages(light: bool = False) -> dict:
    """What is waiting at each stage of the supplier payment cycle:

    needs_checks  batches (not cancelled) whose net total is not yet fully covered by active checks -- Draft batches with
                  invoices, and batches with checks for part of the balance.
    parked        active checks not yet exported to Chrysanth (the pending check run; supplier and lender).
    unprinted     Chrysanth runs exported but not marked printed.
    to_number     checks in runs marked printed that still have no check number, one row per order statement / loan note
                  (numbers are still assigned one batch at a time, with its own confirmation).
    to_print      order statements created or changed since the last check print day (the default range of Print statements
                  in bulk); last_print_day is that day.
    light=True skips to_print (the slowest part, and not part of the waiting count): the menu badge and the Home tile only need the count."""
    needs_checks = _read(
        """SELECT os.statement_id, os.statement_ref, c.creditor_name, v.net_total,
                  COALESCE((SELECT SUM(k.amount) FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active'), 0) AS checks_total,
                  (SELECT COUNT(*) FROM invoices i WHERE i.statement_id = os.statement_id) AS invoices
           FROM order_statements os JOIN creditors c ON c.creditor_id = os.creditor_id
           JOIN v_batch_net_totals v ON v.statement_id = os.statement_id
           WHERE os.status != 'Cancelled'
             AND EXISTS (SELECT 1 FROM invoices i WHERE i.statement_id = os.statement_id)
             AND v.net_total - COALESCE((SELECT SUM(k.amount) FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active'), 0) > 0.005
           ORDER BY os.statement_id"""
    )
    if not needs_checks.empty:
        needs_checks["balance"] = needs_checks["net_total"] - needs_checks["checks_total"]

    parked = get_pending_check_run()
    unprinted = get_unprinted_batches()

    to_number = _read(
        """SELECT k.statement_id, k.loan_note_id, COALESCE(os.statement_ref, ln.note_ref) AS reference, cr.creditor_name,
                  COUNT(*) AS checks, SUM(k.amount) AS total
           FROM checks_issued k
           JOIN check_print_batches b ON b.print_batch_id = k.print_batch_id AND b.printed_confirmed_at IS NOT NULL AND b.printed_confirmed_at != ''
           JOIN creditors cr ON cr.creditor_id = k.creditor_id
           LEFT JOIN order_statements os ON os.statement_id = k.statement_id
           LEFT JOIN loan_notes ln ON ln.loan_note_id = k.loan_note_id
           WHERE k.status = 'Active' AND (k.check_number IS NULL OR k.check_number = '')
           GROUP BY k.statement_id, k.loan_note_id
           ORDER BY reference"""
    )

    last_day = last_check_print_day()
    since = f"{last_day} 00:00:00" if last_day else "0000-00-00"
    to_print = pd.DataFrame(columns=["statement_id", "statement_ref"]) if light else _read(
        "SELECT os.statement_id, os.statement_ref FROM order_statements os WHERE os.status != 'Cancelled' AND "
        + statement_modified_sql("os") + " >= ? ORDER BY os.statement_ref",
        (since,),
    )
    # Thermal statement printing, step 3: batches changed after their statement was printed (re-locked after an unlock)
    reprint = _read(
        """SELECT os.statement_id, os.statement_ref, c.creditor_name FROM order_statements os JOIN creditors c ON c.creditor_id = os.creditor_id
           WHERE os.needs_reprint = 1 AND os.status != 'Cancelled' ORDER BY os.statement_ref"""
    )
    return {"needs_checks": needs_checks, "parked": parked, "unprinted": unprinted, "to_number": to_number, "needs_reprint": reprint,
            "to_print": to_print, "last_print_day": last_day}


def waiting_count(stages: dict) -> int:
    """How many things are waiting in the cycle (shown beside Payment run in the menu): batches needing checks, parked checks,
    runs to mark printed, and batches whose printed checks need numbers."""
    return int(len(stages["needs_checks"]) + len(stages["parked"]) + len(stages["unprinted"]) + len(stages["to_number"]))
