"""Dashboard KPI/visual queries (Phase 5). Scoped 2026-09-06 against the user's own request:
a supplier-specific overdue-check count, average Days Payable Outstanding for suppliers, a
top-suppliers ranking, and a payables trend line switchable between month/quarter/year.

Two of the four original asks didn't map onto anything that exists in this system and were
clarified via AskUserQuestion before building:
- "Check returns due to long overdue" -- this app had no bank-return/NSF concept at the time (no
  reconciliation then; bounced deposits now show on Bank and cash). Confirmed with the user this means checks that were
  cancelled/replaced (voided) while already sitting well past their obligation's due date --
  computed purely from stored dates, since there's no manual "reason" field on a plain cancel
  to query directly.
- "Heatmap of suppliers" -- with only ~4 months of real check data across 8 suppliers, a true
  supplier x month grid would render mostly empty. Confirmed a ranked bar chart instead.
"""
import sqlite3
from datetime import date, timedelta

import pandas as pd

from engines.reports_service import get_payment_schedule_all_tracks

import os
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

_SUPPLIER_CHECK_LAG_SQL = """
SELECT
    c.check_id, c.check_number, c.check_date, c.status, c.amount, cr.creditor_name,
    JULIANDAY(c.check_date) - w.weighted_due_julian AS lag_days
FROM checks_issued c
JOIN creditors cr ON c.creditor_id = cr.creditor_id
JOIN (
    SELECT statement_id, SUM(JULIANDAY(due_date) * amount) / SUM(amount) AS weighted_due_julian
    FROM invoices
    GROUP BY statement_id
) w ON w.statement_id = c.statement_id
WHERE c.statement_id IS NOT NULL
"""


def get_supplier_check_lag() -> pd.DataFrame:
    """One row per supplier check (any status) with lag_days = check_date minus the batch's
    invoice-amount-weighted average due date. A check pays a batch's net total, not one
    invoice, so there's no single due date per check to compare against -- the weighted
    average across that batch's own invoices is the best available anchor. Positive lag_days
    means paid later than the batch's blended due date (the normal case, since checks are
    usually postdated); negative means paid early. Backs both get_average_dpo_suppliers() and
    get_overdue_cancelled_checks() so both share one definition of "how late."""
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(_SUPPLIER_CHECK_LAG_SQL, conn)


def get_average_dpo_suppliers() -> float | None:
    """Average Days Payable Outstanding for suppliers: mean lag between a batch's
    invoice-weighted due date and the check_date of its actually-cleared checks (check_date
    before today, the same cleared convention used everywhere else in this app -- due today = still owed, 2026-09-28 -- a postdated
    Active check whose date hasn't passed yet isn't "paid" so it doesn't count toward how
    long payment actually took). Returns None if there's no cleared supplier check yet."""
    df = get_supplier_check_lag()
    if df.empty:
        return None
    today_str = date.today().isoformat()
    cleared = df[(df["status"] == "Active") & (df["check_date"] < today_str)]
    if cleared.empty:
        return None
    return float(cleared["lag_days"].mean())


def get_overdue_cancelled_checks(threshold_days: int = 30) -> pd.DataFrame:
    """Supplier checks that were cancelled or replaced (voided) while already sitting more
    than threshold_days past the batch's blended due date -- a proxy for "check returns due
    to being long overdue" since this system tracks neither bank returns nor a cancellation
    reason. threshold_days is adjustable in the UI, not hardcoded, matching this app's
    "smart default, stays overridable" convention used for suggested interest, etc."""
    df = get_supplier_check_lag()
    if df.empty:
        return df
    flagged = df[df["status"].isin(["Cancelled", "Replaced"]) & (df["lag_days"] > threshold_days)]
    return flagged.sort_values("lag_days", ascending=False).reset_index(drop=True)


def get_top_suppliers(metric: str = "outstanding", limit: int = 10) -> pd.DataFrame:
    """Ranked suppliers for a bar chart -- either "outstanding" (still-owed payable) or "paid"
    (total of their actually-cleared checks, all-time). Covers both halves of "highest paid
    supplier or with highest payable to" via one toggle rather than two separate charts.

    "outstanding" sums not-yet-cleared checks (status='Active', check_date still in the future --
    same convention as get_uncleared_checks_report()), not invoices.status, so it counts a
    supplier's payable whether the batch behind it has itemized invoices rows or not. Phase 8c's
    legacy migration (Sep 2026) consolidated each supplier's migrated history into one
    order_statements batch without reconstructing per-invoice line items, so an invoices-based
    sum saw 0 for every migrated supplier despite tens of millions of pesos in real, still-
    outstanding checks sitting in checks_issued."""
    with sqlite3.connect(DB_PATH) as conn:
        if metric == "paid":
            df = pd.read_sql_query(
                """SELECT cr.creditor_name AS supplier_name, COALESCE(SUM(c.amount), 0) AS value
                   FROM checks_issued c
                   JOIN creditors cr ON c.creditor_id = cr.creditor_id
                   WHERE c.statement_id IS NOT NULL AND c.status = 'Active' AND c.check_date < DATE('now', 'localtime')
                   GROUP BY cr.creditor_name
                   ORDER BY value DESC
                   LIMIT ?""",
                conn, params=(limit,),
            )
        else:
            df = pd.read_sql_query(
                """SELECT cr.creditor_name AS supplier_name, COALESCE(SUM(c.amount), 0) AS value
                   FROM checks_issued c
                   JOIN creditors cr ON c.creditor_id = cr.creditor_id
                   WHERE c.statement_id IS NOT NULL AND c.status = 'Active' AND c.check_date >= DATE('now', 'localtime')
                   GROUP BY cr.creditor_name
                   ORDER BY value DESC
                   LIMIT ?""",
                conn, params=(limit,),
            )
    return df


_PAYABLES_TREND_UNION = """
    SELECT c.check_date AS d, c.amount AS amount FROM checks_issued c
    WHERE c.status = 'Active' AND c.statement_id IS NOT NULL

    UNION ALL

    SELECT c.check_date AS d, c.amount AS amount FROM checks_issued c
    WHERE c.status = 'Active' AND c.loan_note_id IS NOT NULL

    UNION ALL

    SELECT bli.due_date AS d, bli.amount AS amount FROM bank_loan_installments bli
    WHERE bli.bank_loan_id NOT IN (SELECT bank_loan_id FROM bank_loans WHERE is_cancelled = 1)

    UNION ALL

    SELECT cs.due_date AS d, cs.total_amount_due AS amount FROM cc_statements cs
    WHERE cs.status != 'Cancelled'
"""

# 4 periods, not 3 -- chosen to mirror the business's longest usual credit term (120 days ~= 4
# months) rather than an arbitrary smoothing window. Only lines up with a real 120-day span at
# "month" granularity; at "quarter" (4 quarters = 1 year) or "year" (4 years) it's still 4
# periods of trailing history, not 120 days -- same tradeoff get_payables_trend() already
# documents (a period count, not a calendar span, for a window that works unchanged across all
# three granularities).
_SMA_WINDOW = 4


def _period_label(period_key: str, granularity: str) -> str:
    """Converts a sortable period key ("2026-09", "2026-Q3", "2026") into a friendlier display
    label ("Sep 2026", "Q3 2026", "2026") for chart axes. Kept as a separate step from the
    sortable key itself -- grouping/sorting/rolling all happen on the unambiguous YYYY-first
    key, and this is applied only at the very end, so the label is purely cosmetic and never
    affects chronological order."""
    if granularity == "year":
        return period_key
    if granularity == "quarter":
        year, q = period_key.split("-Q")
        return f"Q{q} {year}"
    year, month = period_key.split("-")
    return date(int(year), int(month), 1).strftime("%b %Y")


def get_payables_trend(granularity: str = "month", start_date: date | None = None, end_date: date | None = None) -> pd.DataFrame:
    """Total payables across all 4 tracks, grouped by period for a trend chart -- covers the
    full history (past-cleared and future-scheduled together), not just what's still owed, so
    the chart shows the actual cash-outflow story over time rather than only the forward
    schedule (get_payment_schedule_all_tracks() deliberately excludes already-cleared items,
    which is right for "what's still owed" but wrong for a payables trend). granularity is
    "month" (YYYY-MM), "quarter" (YYYY-Qn), or "year" (YYYY). Also returns a "moving_avg" column
    -- a 4-period trailing simple moving average (min_periods=1, so it still draws from the very
    first period rather than waiting for 4 periods of history) -- meant to be rendered as a line
    layered over a bar chart of total_amount. The window is a period count, not a calendar span,
    so it works unchanged across all three granularities.

    start_date/end_date (2026-09-17): optional bounds on the underlying dates, applied before
    grouping -- some bank loans carry amortization schedules running years out (one legacy
    facility to 2031), which is real scheduled debt but makes a trend/growth chart mostly noise
    that far out (a handful of installments per period against an otherwise-empty base). Callers
    that want the full history can leave both None, unchanged from before this parameter existed."""
    with sqlite3.connect(DB_PATH) as conn:
        query = f"SELECT * FROM ({_PAYABLES_TREND_UNION})"
        conditions, params = [], []
        if start_date is not None:
            conditions.append("d >= ?")
            params.append(start_date.isoformat())
        if end_date is not None:
            conditions.append("d <= ?")
            params.append(end_date.isoformat())
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        df = pd.read_sql_query(query, conn, params=params)
    if df.empty:
        return pd.DataFrame(columns=["period", "total_amount", "moving_avg"])
    dates = pd.to_datetime(df["d"])
    if granularity == "year":
        period = dates.dt.strftime("%Y")
    elif granularity == "quarter":
        period = dates.dt.year.astype(str) + "-Q" + dates.dt.quarter.astype(str)
    else:
        period = dates.dt.strftime("%Y-%m")
    out = df.assign(period=period).groupby("period", as_index=False)["amount"].sum()
    out.columns = ["period", "total_amount"]
    out = out.sort_values("period").reset_index(drop=True)
    out["moving_avg"] = out["total_amount"].rolling(window=_SMA_WINDOW, min_periods=1).mean()
    out["period"] = out["period"].map(lambda p: _period_label(p, granularity))
    return out


def get_upcoming_cash_obligations(days: int = 7) -> float:
    """Total amount due across all 4 tracks within the next `days` days, reusing
    get_payment_schedule_all_tracks()'s own per-track "still owed, not yet cleared" definition
    rather than re-deriving it -- that function already excludes already-cleared/past-due items
    per track, so filtering its output by a due-date cutoff is enough."""
    df = get_payment_schedule_all_tracks()
    if df.empty:
        return 0.0
    cutoff = (date.today() + timedelta(days=days)).isoformat()
    return float(df[df["due_date"] <= cutoff]["amount"].sum())


def get_supplier_concentration(limit: int = 5) -> dict:
    """Top-`limit` suppliers' share of current outstanding payable -- same not-yet-cleared-check
    "still owed" definition get_top_suppliers("outstanding") uses. Answers "who am I most exposed
    to today," not historical payment volume. Returns a dict with the ranked supplier dataframe,
    the top-N total, the all-supplier outstanding total, and the resulting percentage (0 if
    there's no outstanding payable at all, to avoid a divide-by-zero)."""
    top_df = get_top_suppliers("outstanding", limit=limit)
    with sqlite3.connect(DB_PATH) as conn:
        total_row = pd.read_sql_query(
            """SELECT COALESCE(SUM(amount), 0) AS total FROM checks_issued
               WHERE statement_id IS NOT NULL AND status = 'Active' AND check_date >= DATE('now', 'localtime')""",
            conn,
        )
    total_outstanding = float(total_row["total"][0])
    top_total = float(top_df["value"].sum()) if not top_df.empty else 0.0
    pct = (top_total / total_outstanding * 100) if total_outstanding > 0 else 0.0
    return {
        "top_df": top_df,
        "top_total": top_total,
        "total_outstanding": total_outstanding,
        "pct": pct,
    }


def get_top_supplier_dependency() -> dict | None:
    """The single largest supplier's share of total outstanding supplier payable -- a sharper,
    single-name version of get_supplier_concentration()'s top-5 figure, for a Dashboard alert
    banner (2026-09-17, per user request) that names the one relationship carrying the most
    concentration risk rather than a blended percentage across five. Returns None when there's
    no outstanding supplier payable at all, so the caller can skip the banner instead of dividing
    by zero."""
    top_df = get_top_suppliers("outstanding", limit=1)
    if top_df.empty:
        return None
    concentration = get_supplier_concentration(limit=1)
    total = concentration["total_outstanding"]
    if total <= 0:
        return None
    amount = float(top_df["value"].iloc[0])
    return {
        "name": str(top_df["supplier_name"].iloc[0]),
        "amount": amount,
        "total_outstanding": total,
        "pct": amount / total * 100,
    }


def get_total_ap_outstanding() -> float:
    """Dashboard "Total AP outstanding": Supplier + Lender active checks not yet dated past, bank-loan installments not yet past due,
    credit-card statements still owed (cc_owed_case). **Due today = still owed** (owner, 2026-09-28): a check dated today or an
    installment due today has not left the bank yet, so it counts -- the same rule as the Schedule of payments. Queries checks_issued
    directly (not invoices / loan_notes status) so legacy checks count whichever path wrote them."""
    from engines.cc_rules import cc_owed_case

    with sqlite3.connect(DB_PATH) as conn:
        checks = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM checks_issued WHERE status = 'Active' AND check_date >= DATE('now', 'localtime')").fetchone()[0]
        loans = conn.execute("""SELECT COALESCE(SUM(amount), 0) FROM bank_loan_installments WHERE due_date >= DATE('now', 'localtime')
                                AND bank_loan_id NOT IN (SELECT bank_loan_id FROM bank_loans WHERE is_cancelled = 1)""").fetchone()[0]
        cards = conn.execute(f"SELECT COALESCE(SUM({cc_owed_case('cs')}), 0) FROM cc_statements cs WHERE cs.status NOT IN ('Paid', 'Cancelled')").fetchone()[0]
    return float(checks) + float(loans) + float(cards)


def get_stale_checks(days: int = 180) -> dict:
    """Active checks dated more than `days` ago that the bank has NOT confirmed cleared (bank_cleared_date empty) -- probably lost or never
    deposited. A check the imported bank statement shows as cleared is not stale, however old (2026-09-28: was check-date only)."""
    with sqlite3.connect(DB_PATH) as conn:
        n, total = conn.execute("""SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM checks_issued WHERE status = 'Active' AND bank_cleared_date IS NULL
                                   AND check_date < DATE('now', 'localtime', ?)""", (f"-{int(days)} days",)).fetchone()
    return {"n": int(n), "total": float(total)}
