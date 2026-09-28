"""Unified Debt Interest Tracking (2026-09-15) -- one place to see how much interest is
accumulating across all three debt sources: Private Loans (loan_notes/loan_installments,
already tracks a real interest figure per installment), Bank Loans (bank_loan_installments,
now carries an optional interest_portion per installment -- "still simplified," not a full
amortization requirement), and Credit Cards (cc_statements, now carries interest_charged per
statement as part of its rolling balance).

"Paid this month" uses each track's own already-established convention rather than inventing a
new one:
  - Private loans: the installment's linked check has cleared (status='Active', check_date in
    the target month) -- same cleared-vs-issued rule used everywhere else checks are involved.
  - Bank loans: due_date has passed, in the target month -- same "no manual Paid flag, implied
    by date" convention Phase 3 already established (bank_loan_service.CLEARED_SQL).
  - Credit cards: the statement's due_date falls in the target month -- interest_charged is
    counted for the billing cycle it was charged in, regardless of whether amount_paid has
    caught up with it yet (unpaid interest is still real interest incurred that period).

Per the user's explicit scope: private loans show their full real history (the data already
exists and is accurate); bank loans and credit cards naturally only have data from whenever
interest tracking actually starts being recorded for them.
"""
import sqlite3

import pandas as pd

import os
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def _private_loans_interest(conn: sqlite3.Connection, year: int, month: int) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT c.creditor_name AS name, SUM(li.interest) AS interest
           FROM loan_installments li
           JOIN loan_notes ln ON ln.loan_note_id = li.loan_note_id
           JOIN creditors c ON c.creditor_id = ln.creditor_id
           JOIN checks_issued ck ON ck.check_id = li.check_id
           WHERE li.status = 'Active' AND ck.status = 'Active'
             AND CAST(strftime('%Y', ck.check_date) AS INTEGER) = ?
             AND CAST(strftime('%m', ck.check_date) AS INTEGER) = ?
           GROUP BY c.creditor_id
           ORDER BY c.creditor_name""",
        conn, params=(year, month),
    )


def _bank_loans_interest(conn: sqlite3.Connection, year: int, month: int) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT c.creditor_name AS name, SUM(bli.interest_portion) AS interest
           FROM bank_loan_installments bli
           JOIN bank_loans bl ON bl.bank_loan_id = bli.bank_loan_id
           JOIN creditors c ON c.creditor_id = bl.creditor_id
           WHERE bl.is_cancelled = 0 AND bli.due_date < DATE('now', 'localtime')
             AND CAST(strftime('%Y', bli.due_date) AS INTEGER) = ?
             AND CAST(strftime('%m', bli.due_date) AS INTEGER) = ?
           GROUP BY c.creditor_id
           ORDER BY c.creditor_name""",
        conn, params=(year, month),
    )


def _credit_cards_interest(conn: sqlite3.Connection, year: int, month: int) -> pd.DataFrame:
    return pd.read_sql_query(
        """SELECT (cc.card_name || ' - ' || cc.card_number_last4) AS name, SUM(cs.interest_charged) AS interest
           FROM cc_statements cs
           JOIN credit_cards cc ON cc.card_id = cs.card_id
           WHERE cs.status != 'Cancelled'
             AND CAST(strftime('%Y', cs.due_date) AS INTEGER) = ?
             AND CAST(strftime('%m', cs.due_date) AS INTEGER) = ?
           GROUP BY cc.card_id
           ORDER BY name""",
        conn, params=(year, month),
    )


def _source_dict(df: pd.DataFrame) -> dict:
    result = {row["name"]: float(row["interest"] or 0) for _, row in df.iterrows()}
    result["subtotal"] = round(sum(result.values()), 2)
    result["count"] = len(df)
    return result


def get_monthly_interest_summary(year: int, month: int) -> dict:
    """Unified interest summary for one month, broken down by source. Every subtotal defaults
    to 0 when there's simply no data yet -- not an error, just nothing recorded for that source
    that month."""
    with sqlite3.connect(DB_PATH) as conn:
        private_df = _private_loans_interest(conn, year, month)
        bank_df = _bank_loans_interest(conn, year, month)
        cc_df = _credit_cards_interest(conn, year, month)

    private_loans = _source_dict(private_df)
    bank_loans = _source_dict(bank_df)
    credit_cards = _source_dict(cc_df)
    total_interest_paid = round(private_loans["subtotal"] + bank_loans["subtotal"] + credit_cards["subtotal"], 2)
    sources_with_data = sum(1 for s in (private_loans, bank_loans, credit_cards) if s["count"] > 0)

    return {
        "year": year,
        "month": month,
        "period": f"{_MONTH_NAMES[month - 1]} {year}",
        "private_loans": private_loans,
        "bank_loans": bank_loans,
        "credit_cards": credit_cards,
        "total_interest_paid": total_interest_paid,
        "notes": f"{sources_with_data} source(s) with interest this period" if sources_with_data else "No interest recorded for this period",
    }


def get_yearly_interest_trend(year: int) -> pd.DataFrame:
    """One row per month (1-12) for the given year, with each source's subtotal plus a combined
    total -- feeds the trend chart. Calling get_monthly_interest_summary() per month rather than
    one big query keeps this in lockstep with the single-month summary's own definitions."""
    rows = []
    for month in range(1, 13):
        summary = get_monthly_interest_summary(year, month)
        rows.append({
            "month": month,
            "month_name": _MONTH_NAMES[month - 1],
            "private_loans_interest": summary["private_loans"]["subtotal"],
            "bank_loans_interest": summary["bank_loans"]["subtotal"],
            "credit_cards_interest": summary["credit_cards"]["subtotal"],
            "total_interest": summary["total_interest_paid"],
        })
    return pd.DataFrame(rows).sort_values("month").reset_index(drop=True)
