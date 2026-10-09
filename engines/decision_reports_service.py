"""Reports for DECISIONS (plan G, 2026-10-01): each answers one question the owner may be asked, with a plain one-line "what this says".
Reused from elsewhere (not repeated here): supplier spend ranking = analytics_flow_service.supplier_spend; debt maturity ladder = analytics_flow_service.debt_due_by_month;
cash needs vs available = analytics_flow_service.cash_ahead_weeks. Every function returns {"table": DataFrame, "says": str, ...} so the screen only draws it."""
from __future__ import annotations

from engines.clock import manila_today
from engines.common import get_conn
import os
import sqlite3
from datetime import date, timedelta

import pandas as pd

from engines.analytics_flow_service import _first_of_month, money_flow
from engines.common import php

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def _read(sql: str, params: tuple = ()) -> pd.DataFrame:
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(sql, conn, params=params)


# ------------------------------------------------------------------ G2 supplier payment performance
def supplier_payment_performance(start: date, end: date) -> dict:
    """Are we paying late? Per supplier: of the money paid by checks dated in the period, how much was on or before the invoice's due date, and how late the rest was.
    An invoice's due date is its own due date; beginning-balance lines (no real invoice) are left out."""
    df = _read("""SELECT cr.creditor_name AS supplier, a.amount, k.check_date, i.due_date
                  FROM invoice_check_allocations a JOIN checks_issued k ON k.check_id = a.check_id JOIN invoices i ON i.invoice_id = a.invoice_id
                  JOIN creditors cr ON cr.creditor_id = i.creditor_id
                  WHERE k.status = 'Active' AND i.is_opening_balance = 0 AND i.due_date IS NOT NULL AND k.check_date BETWEEN ? AND ?""", (str(start), str(end)))
    cols = ["supplier", "paid", "within_terms_pct", "late_amount", "avg_days_late"]
    if df.empty:
        return {"table": pd.DataFrame(columns=cols), "says": "No checks against real invoices in this period yet."}
    df["late_days"] = (pd.to_datetime(df["check_date"]) - pd.to_datetime(df["due_date"])).dt.days
    df["is_late"] = df["late_days"] > 0
    rows = []
    for supplier, g in df.groupby("supplier"):
        paid = float(g["amount"].sum())
        late = g[g["is_late"]]
        rows.append({"supplier": supplier, "paid": paid, "within_terms_pct": 100 * float(g.loc[~g["is_late"], "amount"].sum()) / paid,
                     "late_amount": float(late["amount"].sum()),
                     "avg_days_late": float((late["late_days"] * late["amount"]).sum() / late["amount"].sum()) if not late.empty else 0.0})
    out = pd.DataFrame(rows, columns=cols).sort_values("late_amount", ascending=False).reset_index(drop=True)
    total, late_total = float(out["paid"].sum()), float(out["late_amount"].sum())
    worst = out.iloc[0]
    says = (f"{100 * (total - late_total) / total:.0f}% of {php(total)} paid in this period was on or before the due date. "
            + (f"Most paid late: {worst['supplier']} ({php(worst['late_amount'])}, about {worst['avg_days_late']:.0f} days late)." if late_total > 0 else "Nothing was paid late."))
    return {"table": out, "says": says}


# ------------------------------------------------------------------ G3 payables still to pay after each month end
def payables_after_month_end(months: int = 12, today: date | None = None) -> dict:
    """How much will still be owed at the end of each coming month if nothing new is added: per month end and track, everything scheduled after that date."""
    today = today or manila_today()
    flow = money_flow(today, _first_of_month(today, months + 24) - timedelta(days=1))
    flow = flow[flow["state"] == "Due"] if not flow.empty else flow
    rows = []
    for m in range(months):
        month_end = _first_of_month(today, m + 1) - timedelta(days=1)
        later = flow[flow["month"] > month_end.replace(day=1)] if not flow.empty else flow
        for track in ("Supplier", "Lender", "Bank Loan", "Credit Card"):
            rows.append({"month_end": month_end, "track": track, "still_to_pay": float(later.loc[later["track"] == track, "amount"].sum()) if not later.empty else 0.0})
    out = pd.DataFrame(rows, columns=["month_end", "track", "still_to_pay"])
    first, last = out[out["month_end"] == out["month_end"].min()]["still_to_pay"].sum(), out[out["month_end"] == out["month_end"].max()]["still_to_pay"].sum()
    says = f"If nothing new is added, what is owed falls from {php(first)} at the end of this month to {php(last)} at the end of month {months}." if first else "Nothing is scheduled."
    return {"table": out, "says": says}


# ------------------------------------------------------------------ G4 monthly outflow summary
def monthly_outflow(month: date) -> dict:
    """Where the money went in one month, per kind of payee and the top 10 payees, against the month before and the same month a year earlier."""
    month = month.replace(day=1)
    prev, last_year = _first_of_month(month, -1), _first_of_month(month, -12)

    def totals(m: date) -> pd.Series:
        f = money_flow(m, _first_of_month(m, 1) - timedelta(days=1))
        return f.groupby("track")["amount"].sum() if not f.empty else pd.Series(dtype=float)

    cur, p, y = totals(month), totals(prev), totals(last_year)
    tracks = ["Supplier", "Lender", "Bank Loan", "Credit Card"]
    t = pd.DataFrame({"track": tracks, "this_month": [float(cur.get(k, 0)) for k in tracks], "last_month": [float(p.get(k, 0)) for k in tracks],
                      "same_month_last_year": [float(y.get(k, 0)) for k in tracks]})
    t["change_pct"] = t.apply(lambda r: (r["this_month"] - r["last_month"]) / r["last_month"] * 100 if r["last_month"] else None, axis=1)
    from engines.reports_service import get_payment_calendar_items
    items = get_payment_calendar_items()
    top = pd.DataFrame(columns=["payee_name", "amount"])
    if not items.empty:
        d = pd.to_datetime(items["actual_payment_date"]).dt.date
        in_month = items[(d >= month) & (d < _first_of_month(month, 1))]
        top = in_month.groupby("payee_name", as_index=False)["amount"].sum().sort_values("amount", ascending=False).head(10).reset_index(drop=True)
    total, last = float(t["this_month"].sum()), float(t["last_month"].sum())
    says = f"{month:%B %Y}: {php(total)} " + ("(nothing to compare with last month)." if not last else f"({(total - last) / last * 100:+.0f}% against {prev:%B}, {php(last)}).")
    return {"table": t, "top": top, "says": says}


# ------------------------------------------------------------------ G5 cost of borrowing
def cost_of_borrowing(today: date | None = None) -> dict:
    """Interest paid so far and still to pay, per loan note, bank loan and card, biggest first -- which debt is the most expensive to keep."""
    today = (today or manila_today()).isoformat()
    lenders = _read("""SELECT 'Private loan' AS source, cr.creditor_name AS name, n.note_ref AS reference, n.monthly_interest_rate AS monthly_rate,
                              COALESCE(SUM(CASE WHEN i.due_date < ? THEN i.interest END), 0) AS interest_paid, COALESCE(SUM(CASE WHEN i.due_date >= ? THEN i.interest END), 0) AS interest_to_pay
                       FROM loan_notes n JOIN creditors cr ON cr.creditor_id = n.creditor_id LEFT JOIN loan_installments i ON i.loan_note_id = n.loan_note_id
                       WHERE n.status != 'Cancelled' GROUP BY n.loan_note_id""", (today, today))
    banks = _read("""SELECT 'Bank loan' AS source, cr.creditor_name AS name, b.loan_ref AS reference, NULL AS monthly_rate,
                            COALESCE(SUM(CASE WHEN (i.cleared_at IS NOT NULL OR i.installment_id IN (SELECT matched_installment_id FROM bank_transactions WHERE matched_installment_id IS NOT NULL)) THEN i.interest_portion END), 0) AS interest_paid, COALESCE(SUM(CASE WHEN NOT (i.cleared_at IS NOT NULL OR i.installment_id IN (SELECT matched_installment_id FROM bank_transactions WHERE matched_installment_id IS NOT NULL)) THEN i.interest_portion END), 0) AS interest_to_pay
                     FROM bank_loans b JOIN creditors cr ON cr.creditor_id = b.creditor_id LEFT JOIN bank_loan_installments i ON i.bank_loan_id = b.bank_loan_id
                     WHERE b.is_cancelled = 0 GROUP BY b.bank_loan_id""")             # a bank-loan payment is paid when it clears (2026-10-09), not by its date
    cards = _read("""SELECT 'Credit card' AS source, cr.creditor_name AS name, cc.card_name || ' ' || cc.card_number_last4 AS reference, NULL AS monthly_rate,
                            COALESCE(SUM(cs.interest_charged), 0) AS interest_paid, 0 AS interest_to_pay
                     FROM credit_cards cc JOIN creditors cr ON cr.creditor_id = cc.creditor_id LEFT JOIN cc_statements cs ON cs.card_id = cc.card_id AND cs.status != 'Cancelled'
                     GROUP BY cc.card_id""")
    out = pd.concat([lenders, banks, cards], ignore_index=True)
    out = out[(out["interest_paid"] > 0) | (out["interest_to_pay"] > 0)].copy()
    out["interest_total"] = out["interest_paid"] + out["interest_to_pay"]
    out = out.sort_values("interest_total", ascending=False).reset_index(drop=True)
    if out.empty:
        return {"table": out, "says": "No interest is recorded on any loan or card yet."}
    by_source = out.groupby("source")["interest_total"].sum()
    says = f"Interest on record: {php(float(out['interest_total'].sum()))} ({', '.join(f'{k} {php(float(v))}' for k, v in by_source.items())}). Costliest: {out.iloc[0]['name']} {out.iloc[0]['reference']}."
    return {"table": out, "says": says}


# ------------------------------------------------------------------ G7 credit card usage
def card_usage(today: date | None = None) -> dict:
    """Per card over the last 12 months: charges, interest, the latest amount due and how much of the limit that is."""
    since = ((today or manila_today()) - timedelta(days=365)).isoformat()
    df = _read("""SELECT c.creditor_name AS issuer, cc.card_name, cc.card_number_last4 AS last4, COALESCE(ch.name, '') AS cardholder, cc.credit_limit,
                         COALESCE(SUM(CASE WHEN cs.due_date >= ? THEN cs.new_charges END), 0) AS charges, COALESCE(SUM(CASE WHEN cs.due_date >= ? THEN cs.interest_charged END), 0) AS interest,
                         (SELECT x.total_amount_due FROM cc_statements x WHERE x.card_id = cc.card_id AND x.status != 'Cancelled' ORDER BY x.due_date DESC LIMIT 1) AS latest_due
                  FROM credit_cards cc JOIN creditors c ON c.creditor_id = cc.creditor_id LEFT JOIN cardholders ch ON ch.cardholder_id = cc.cardholder_id
                  LEFT JOIN cc_statements cs ON cs.card_id = cc.card_id AND cs.status != 'Cancelled' WHERE cc.is_active = 1 GROUP BY cc.card_id""", (since, since))
    df["utilization_pct"] = df.apply(lambda r: r["latest_due"] / r["credit_limit"] * 100 if r["credit_limit"] and pd.notna(r["latest_due"]) else None, axis=1)
    df = df.sort_values("charges", ascending=False).reset_index(drop=True)
    if df.empty or (df["charges"].sum() == 0 and df["interest"].sum() == 0):
        return {"table": df, "says": "No card charges or interest recorded in the last 12 months."}
    top = df.iloc[0]
    says = f"Charges in the last 12 months: {php(float(df['charges'].sum()))}; interest paid: {php(float(df['interest'].sum()))}. Most used: {top['issuer']} {top['card_name']} ({php(float(top['charges']))})."
    return {"table": df, "says": says}


# ------------------------------------------------------------------ G9 business vs family
def business_vs_family(today: date | None = None) -> dict:
    """Bank loans by whom they are for (a person or an affiliate), with amounts and interest -- how much of the debt is for the family or affiliates."""
    today = (today or manila_today()).isoformat()
    df = _read("""SELECT COALESCE(lb.name, 'Not set') AS for_whom, COALESCE(lb.beneficiary_type, '') AS type, COUNT(DISTINCT b.bank_loan_id) AS loans,
                         COALESCE(SUM(CASE WHEN NOT (i.cleared_at IS NOT NULL OR i.installment_id IN (SELECT matched_installment_id FROM bank_transactions WHERE matched_installment_id IS NOT NULL)) THEN i.amount END), 0) AS still_to_pay, COALESCE(SUM(i.interest_portion), 0) AS interest
                  FROM bank_loans b LEFT JOIN loan_beneficiaries lb ON lb.beneficiary_id = b.beneficiary_id LEFT JOIN bank_loan_installments i ON i.bank_loan_id = b.bank_loan_id
                  WHERE b.is_cancelled = 0 GROUP BY COALESCE(lb.name, 'Not set') ORDER BY still_to_pay DESC""")
    if df.empty:
        return {"table": df, "says": "No bank loans on file."}
    total = float(df["still_to_pay"].sum())
    unset = float(df.loc[df["for_whom"] == "Not set", "still_to_pay"].sum())
    says = f"Bank loans still to pay: {php(total)}" + (f", of which {php(unset)} has no 'for whom' yet." if unset else ".")
    return {"table": df, "says": says}


# ------------------------------------------------------------------ G10 credit memos by supplier
def memos_by_supplier(start: date, end: date) -> dict:
    """Returns / corrections: per supplier the credit memos in the period as a share of what was bought from them."""
    df = _read("""SELECT cr.creditor_name AS supplier, COUNT(*) AS memos, SUM(m.amount) AS memo_total FROM credit_memos m JOIN order_statements o ON o.statement_id = m.statement_id
                  JOIN creditors cr ON cr.creditor_id = o.creditor_id WHERE m.memo_date BETWEEN ? AND ? GROUP BY cr.creditor_id""", (str(start), str(end)))
    bought = _read("""SELECT cr.creditor_name AS supplier, SUM(i.amount) AS purchases FROM invoices i JOIN creditors cr ON cr.creditor_id = i.creditor_id
                      WHERE i.status != 'Cancelled' AND i.is_opening_balance = 0 AND i.invoice_date BETWEEN ? AND ? GROUP BY cr.creditor_id""", (str(start), str(end)))
    out = df.merge(bought, on="supplier", how="left").fillna({"purchases": 0.0})
    out["memo_pct"] = out.apply(lambda r: r["memo_total"] / r["purchases"] * 100 if r["purchases"] else None, axis=1)
    out = out.sort_values("memo_total", ascending=False).reset_index(drop=True)
    if out.empty:
        return {"table": out, "says": "No credit memos in this period."}
    top = out.iloc[0]
    return {"table": out, "says": f"{php(float(out['memo_total'].sum()))} in credit memos from {len(out)} supplier(s). Most: {top['supplier']} ({php(float(top['memo_total']))})."}


# ------------------------------------------------------------------ the question guide
QUESTION_GUIDE = [
    ("Who are our suppliers / lenders / banks?", "Reports > Lists > Suppliers, Private lenders, Banks"),
    ("Who do we buy from most, and is it growing?", "Decision reports > Supplier spend ranking (or Analytics > Where the money goes)"),
    ("Are we paying late? Which supplier are we stretching?", "Decision reports > Supplier payment performance"),
    ("Is our debt going up or down?", "Decision reports > What is still owed after each month end"),
    ("Where did the money go this month?", "Decision reports > Monthly outflow summary"),
    ("Which debt is the most expensive?", "Decision reports > Cost of borrowing"),
    ("When is the heavy debt month?", "Decision reports > Debt falling due by month"),
    ("Who uses the cards most? Are we paying card interest?", "Decision reports > Credit card usage"),
    ("Can we cover the next weeks?", "Decision reports > Cash needs vs available (13 weeks)"),
    ("How much of our debt is for the family?", "Decision reports > Business vs family"),
    ("Which supplier has the most returns?", "Decision reports > Credit memos by supplier"),
    ("What is due and when?", "Reports > Schedule of payments"),
    ("Which checks are voided or replaced?", "Checks and checkbook > Voided, replaced and spoiled checks"),
]
