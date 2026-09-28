"""CPA-facing Analytics (2026-09-17): composition/trend, payment behavior, and cost-of-debt
queries for the Analytics tab -- deliberately kept separate from dashboard_kpi_service.py's
daily-glance KPIs, since this tab is for periodic deep-dives, not a morning check-in.

Scope note: this system tracks the AP side only -- checks, invoices, loans, credit cards. There
is no revenue, no general ledger, no balance sheet, so classic ratios that need those (current
ratio, quick ratio, DSO, debt-to-equity) aren't computable here -- there's nothing on the other
side of the division. Everything below is grounded in what this system actually records.

Interest-rate data (get_debt_cost_comparison) is real but currently sparse for legacy-migrated
lenders/bank loans/credit cards -- Phase 8a (Sep 2026) imported them as placeholders with no
rate on file (loan_notes.status='Pending Setup', bank_loans.principal_amount NULL, no cc_
statements at all). A source with nothing entered is simply left out of the comparison, not
shown as a fabricated 0%, and will populate on its own as real terms get entered post-launch.
"""
import sqlite3

from engines.cc_rules import cc_owed_case
from datetime import date

import pandas as pd

from engines.dashboard_kpi_service import _period_label, get_payables_trend, get_supplier_concentration, get_top_suppliers

import os
from engines.common import get_conn
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

_AP_COMPOSITION_UNION = """
    SELECT c.check_date AS d, c.amount AS amount, 'Supplier' AS track FROM checks_issued c
    WHERE c.status = 'Active' AND c.statement_id IS NOT NULL

    UNION ALL

    SELECT c.check_date AS d, c.amount AS amount, 'Lender' AS track FROM checks_issued c
    WHERE c.status = 'Active' AND c.loan_note_id IS NOT NULL

    UNION ALL

    SELECT bli.due_date AS d, bli.amount AS amount, 'Bank Loan' AS track FROM bank_loan_installments bli
    WHERE bli.bank_loan_id NOT IN (SELECT bank_loan_id FROM bank_loans WHERE is_cancelled = 1)

    UNION ALL

    SELECT cs.due_date AS d, cs.total_amount_due AS amount, 'Credit Card' AS track FROM cc_statements cs
    WHERE cs.status != 'Cancelled'
"""


def get_ap_composition_by_track(granularity: str = "month", start_date: date | None = None, end_date: date | None = None) -> pd.DataFrame:
    """Total payables per period, broken out by track (Supplier/Lender/Bank Loan/Credit Card)
    instead of collapsed into one number -- get_payables_trend()'s all-tracks total, split back
    out for a stacked bar/area chart of how the AP mix shifts over time. Same full-history scope
    as get_payables_trend() (past-cleared and future-scheduled together, not just what's still
    owed), same granularity options ("month"/"quarter"/"year"), same optional start_date/end_date
    window (see get_payables_trend()'s docstring for why -- a handful of bank loan amortization
    schedules run years past everything else, thinning the chart out to mostly noise that far
    out unless bounded)."""
    with get_conn(DB_PATH) as conn:
        query = f"SELECT * FROM ({_AP_COMPOSITION_UNION})"
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
        return pd.DataFrame(columns=["period", "track", "amount"])
    dates = pd.to_datetime(df["d"])
    if granularity == "year":
        period = dates.dt.strftime("%Y")
    elif granularity == "quarter":
        period = dates.dt.year.astype(str) + "-Q" + dates.dt.quarter.astype(str)
    else:
        period = dates.dt.strftime("%Y-%m")
    out = df.assign(period=period).groupby(["period", "track"], as_index=False)["amount"].sum()
    out = out.sort_values("period").reset_index(drop=True)
    out["period"] = out["period"].map(lambda p: _period_label(p, granularity))
    return out


def get_ap_growth_rate(granularity: str = "month", start_date: date | None = None, end_date: date | None = None) -> pd.DataFrame:
    """get_payables_trend()'s all-tracks total with a period-over-period percent-change column
    added -- month-over-month/quarter-over-quarter/year-over-year AP growth, whichever
    granularity is picked. First period has no prior period to compare against, so its
    pct_change is NaN, not 0 (0 would falsely read as "no change"). start_date/end_date: see
    get_payables_trend() -- a growth-rate reading against a near-empty far-future base (some
    bank loan schedules run to 2031) is mostly noise, not a real trend, so bounding the window
    matters even more here than for the composition chart."""
    df = get_payables_trend(granularity, start_date=start_date, end_date=end_date)
    if df.empty:
        return df.assign(pct_change=pd.Series(dtype=float))
    df = df.copy()
    df["pct_change"] = df["total_amount"].pct_change() * 100
    return df


def get_supplier_pareto(limit: int = 20) -> pd.DataFrame:
    """Top suppliers by current outstanding payable, with a running cumulative-percent-of-total
    column for a Pareto chart (bars + cumulative line) -- "which suppliers make up 80% of what
    we owe." The cumulative percent is against the TRUE all-supplier total (same query
    get_supplier_concentration() uses), not just the sum of the `limit` rows shown, so it reads
    correctly even when a business has more suppliers than fit on the chart."""
    df = get_top_suppliers("outstanding", limit=limit)
    if df.empty:
        return df
    concentration = get_supplier_concentration(limit=1)
    grand_total = concentration["total_outstanding"]
    df = df.copy()
    df["cumulative_pct"] = (df["value"].cumsum() / grand_total * 100) if grand_total > 0 else 0.0
    return df


def get_lender_pareto(limit: int = 10) -> pd.DataFrame:
    """Same Pareto shape as get_supplier_pareto() (rank by currently-outstanding checks, running
    cumulative % of the track's own true total) for the Lender track -- checks_issued rows tied
    to a loan_note_id instead of a statement_id."""
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT cr.creditor_name AS name, COALESCE(SUM(c.amount), 0) AS value
               FROM checks_issued c JOIN creditors cr ON c.creditor_id = cr.creditor_id
               WHERE c.loan_note_id IS NOT NULL AND c.status = 'Active' AND c.check_date >= DATE('now', 'localtime')
               GROUP BY cr.creditor_id ORDER BY value DESC LIMIT ?""",
            conn, params=(limit,),
        )
        total_row = pd.read_sql_query(
            """SELECT COALESCE(SUM(amount), 0) AS total FROM checks_issued
               WHERE loan_note_id IS NOT NULL AND status = 'Active' AND check_date >= DATE('now', 'localtime')""",
            conn,
        )
    if df.empty:
        return df
    grand_total = float(total_row["total"][0])
    df = df.copy()
    df["cumulative_pct"] = (df["value"].cumsum() / grand_total * 100) if grand_total > 0 else 0.0
    return df


def get_bank_loan_pareto(limit: int = 10) -> pd.DataFrame:
    """Same shape, for the Bank Loan track -- ranked by each facility's future (not-yet-due)
    installment total, one row per bank_loans facility (a single bank/creditor commonly has
    several facilities, e.g. two separate BDO loans, so "name" folds in the loan_ref to stay
    unambiguous rather than showing a bare bank name twice)."""
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT cr.creditor_name AS bank_name, bl.loan_ref AS reference, COALESCE(SUM(bli.amount), 0) AS value
               FROM bank_loan_installments bli
               JOIN bank_loans bl ON bli.bank_loan_id = bl.bank_loan_id
               JOIN creditors cr ON bl.creditor_id = cr.creditor_id
               WHERE bli.due_date >= DATE('now', 'localtime') AND bl.is_cancelled = 0
               GROUP BY bl.bank_loan_id ORDER BY value DESC LIMIT ?""",
            conn, params=(limit,),
        )
        total_row = pd.read_sql_query(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM bank_loan_installments WHERE due_date >= DATE('now', 'localtime') AND bank_loan_id NOT IN (SELECT bank_loan_id FROM bank_loans WHERE is_cancelled = 1)",
            conn,
        )
    if df.empty:
        return df
    grand_total = float(total_row["total"][0])
    df = df.copy()
    df["name"] = df["bank_name"] + " — " + df["reference"]
    df["cumulative_pct"] = (df["value"].cumsum() / grand_total * 100) if grand_total > 0 else 0.0
    return df.drop(columns=["bank_name", "reference"])


def get_credit_card_pareto(limit: int = 10) -> pd.DataFrame:
    """Same Pareto shape as the other single-track variants, for the Credit Card track --
    currently returns empty for every real business this app has migrated, since none of them
    have any cc_statements on file yet (Phase 8a deliberately skipped importing historical
    statement data). Kept as its own function anyway rather than left out, so it self-populates
    the moment a real statement gets entered instead of needing anyone to remember to add it
    later -- same not-yet-cleared "still owed" definition the Dashboard's Total AP outstanding
    KPI already uses for this track."""
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(
            f"""SELECT (cc.card_name || ' - ' || cc.card_number_last4) AS name,
                      COALESCE(SUM({cc_owed_case('cs')}), 0) AS value
               FROM cc_statements cs JOIN credit_cards cc ON cs.card_id = cc.card_id
               WHERE cs.status NOT IN ('Paid', 'Cancelled')
               GROUP BY cc.card_id
               HAVING value > 0
               ORDER BY value DESC LIMIT ?""",
            conn, params=(limit,),
        )
        total_row = pd.read_sql_query(
            f"""SELECT COALESCE(SUM({cc_owed_case('cs')}), 0) AS total
               FROM cc_statements cs WHERE cs.status NOT IN ('Paid', 'Cancelled')""",
            conn,
        )
    if df.empty:
        return df
    grand_total = float(total_row["total"][0])
    df = df.copy()
    df["cumulative_pct"] = (df["value"].cumsum() / grand_total * 100) if grand_total > 0 else 0.0
    return df


def get_all_tracks_pareto(limit: int = 15) -> pd.DataFrame:
    """Same Pareto shape as the single-track variants, but ranking every payee/facility across
    all four tracks together in one list -- "regardless of track, who/what do I owe the most to,
    overall." Each row keeps a "track" column so the table can show what kind of obligation it
    is; a bank loan's name already carries its loan_ref (get_bank_loan_pareto's own
    disambiguation), so nothing here collides across tracks even though a bank name like "BDO"
    could otherwise appear as both a Bank Loan facility and, in principle, a Credit Card issuer."""
    with get_conn(DB_PATH) as conn:
        supplier_df = pd.read_sql_query(
            """SELECT cr.creditor_name AS name, 'Supplier' AS track, COALESCE(SUM(c.amount), 0) AS value
               FROM checks_issued c JOIN creditors cr ON c.creditor_id = cr.creditor_id
               WHERE c.statement_id IS NOT NULL AND c.status = 'Active' AND c.check_date >= DATE('now', 'localtime')
               GROUP BY cr.creditor_id""",
            conn,
        )
        lender_df = pd.read_sql_query(
            """SELECT cr.creditor_name AS name, 'Lender' AS track, COALESCE(SUM(c.amount), 0) AS value
               FROM checks_issued c JOIN creditors cr ON c.creditor_id = cr.creditor_id
               WHERE c.loan_note_id IS NOT NULL AND c.status = 'Active' AND c.check_date >= DATE('now', 'localtime')
               GROUP BY cr.creditor_id""",
            conn,
        )
        bank_df = pd.read_sql_query(
            """SELECT cr.creditor_name || ' — ' || bl.loan_ref AS name, 'Bank Loan' AS track,
                      COALESCE(SUM(bli.amount), 0) AS value
               FROM bank_loan_installments bli
               JOIN bank_loans bl ON bli.bank_loan_id = bl.bank_loan_id
               JOIN creditors cr ON bl.creditor_id = cr.creditor_id
               WHERE bli.due_date >= DATE('now', 'localtime') AND bl.is_cancelled = 0
               GROUP BY bl.bank_loan_id""",
            conn,
        )
        # Same unpaid_balance_next-aware "still owed" definition the Dashboard's Total AP
        # outstanding KPI already uses for the Credit Card leg (app.py) -- kept in lockstep
        # rather than re-deriving a simpler one here.
        cc_df = pd.read_sql_query(
            f"""SELECT (cc.card_name || ' - ' || cc.card_number_last4) AS name, 'Credit Card' AS track,
                      COALESCE(SUM({cc_owed_case('cs')}), 0) AS value
               FROM cc_statements cs JOIN credit_cards cc ON cs.card_id = cc.card_id
               WHERE cs.status NOT IN ('Paid', 'Cancelled')
               GROUP BY cc.card_id""",
            conn,
        )
    combined = pd.concat([supplier_df, lender_df, bank_df, cc_df], ignore_index=True)
    combined = combined[combined["value"] > 0]
    if combined.empty:
        return combined
    combined = combined.sort_values("value", ascending=False).reset_index(drop=True)
    grand_total = float(combined["value"].sum())
    top = combined.head(limit).copy()
    top["cumulative_pct"] = (top["value"].cumsum() / grand_total * 100) if grand_total > 0 else 0.0
    return top


def get_check_status_breakdown() -> pd.DataFrame:
    """Count + amount per EFFECTIVE check status (v_effective_checks, schema.sql) across every
    track that uses checks_issued (Supplier + Lender) -- Active/Cleared/Stale/Cancelled/
    Replaced/Spoiled. Effective, not recorded, status: this app never actually stamps a check
    'Cleared' in the database (no bank reconciliation feature), it stays 'Active' forever and
    the view reclassifies it live once its check_date arrives -- querying recorded_status
    instead would never show a single cleared check, which would be misleading, not honest."""
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT effective_status, COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM v_effective_checks GROUP BY effective_status",
            conn,
        )
    if df.empty:
        return df
    grand_total_n = df["n"].sum()
    df["pct_of_count"] = df["n"] / grand_total_n * 100
    return df.sort_values("n", ascending=False).reset_index(drop=True)


_AGING_BUCKETS = [
    (None, 0, "Overdue (not yet marked cleared)"),
    (0, 7, "Due in 0-7 days"),
    (8, 30, "Due in 8-30 days"),
    (31, 60, "Due in 31-60 days"),
    (61, 90, "Due in 61-90 days"),
    (91, None, "Due in 90+ days"),
]


def get_check_aging_distribution() -> pd.DataFrame:
    """Every still-genuinely-outstanding check (effective_status='Active' -- excludes anything
    the app already treats as effectively cleared or stale), bucketed by days until its
    check_date. Doesn't need per-invoice due dates the way DPO/aging-by-due-date does, so this
    reads correctly even for Phase 8c's legacy-migrated checks that have no itemized invoice
    behind them -- just the check's own date, which every check has."""
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(
            "SELECT check_date, amount FROM v_effective_checks WHERE effective_status = 'Active'",
            conn,
        )
    if df.empty:
        return pd.DataFrame(columns=["bucket", "count", "amount"])
    today = date.today()
    days_out = (pd.to_datetime(df["check_date"]).dt.date - today).apply(lambda d: d.days)

    def _bucket_for(days: int) -> str:
        for lo, hi, label in _AGING_BUCKETS:
            if (lo is None or days >= lo) and (hi is None or days <= hi):
                return label
        return _AGING_BUCKETS[-1][2]

    df = df.assign(bucket=days_out.map(_bucket_for))
    out = df.groupby("bucket", as_index=False).agg(count=("amount", "count"), amount=("amount", "sum"))
    order = [b[2] for b in _AGING_BUCKETS]
    out["bucket"] = pd.Categorical(out["bucket"], categories=order, ordered=True)
    return out.sort_values("bucket").reset_index(drop=True)


def get_debt_cost_comparison() -> pd.DataFrame:
    """One row per debt facility that has a real, entered interest figure -- private loans
    (loan_notes.monthly_interest_rate, only where a note has moved past the 'Pending Setup'
    placeholder), bank loans (interest_portion actually typed against at least one installment,
    against a principal that's actually on file), credit cards (interest_charged against a real
    previous_balance on at least one statement). A facility with nothing entered simply isn't a
    row here -- no fabricated 0%, since "no rate on file" and "genuinely 0% interest" are not
    the same fact and shouldn't look identical on a chart."""
    with get_conn(DB_PATH) as conn:
        private_df = pd.read_sql_query(
            """SELECT cr.creditor_name AS name, ln.note_ref AS reference,
                      'Private Loan' AS source, ln.monthly_interest_rate AS monthly_rate_pct
               FROM loan_notes ln JOIN creditors cr ON ln.creditor_id = cr.creditor_id
               WHERE ln.monthly_interest_rate IS NOT NULL""",
            conn,
        )
        bank_df = pd.read_sql_query(
            """SELECT cr.creditor_name AS name, bl.loan_ref AS reference, 'Bank Loan' AS source,
                      SUM(bli.interest_portion) * 100.0 / bl.principal_amount AS monthly_rate_pct
               FROM bank_loans bl
               JOIN creditors cr ON bl.creditor_id = cr.creditor_id
               JOIN bank_loan_installments bli ON bli.bank_loan_id = bl.bank_loan_id
               WHERE bl.principal_amount IS NOT NULL AND bl.principal_amount > 0 AND bl.is_cancelled = 0
               GROUP BY bl.bank_loan_id
               HAVING SUM(bli.interest_portion) > 0""",
            conn,
        )
        cc_df = pd.read_sql_query(
            """SELECT cr.creditor_name AS name, (cc.card_name || ' - ' || cc.card_number_last4) AS reference,
                      'Credit Card' AS source, AVG(cs.interest_charged * 100.0 / cs.previous_balance) AS monthly_rate_pct
               FROM cc_statements cs
               JOIN credit_cards cc ON cs.card_id = cc.card_id
               JOIN creditors cr ON cc.creditor_id = cr.creditor_id
               WHERE cs.previous_balance > 0 AND cs.interest_charged > 0
               GROUP BY cc.card_id""",
            conn,
        )
    combined = pd.concat([private_df, bank_df, cc_df], ignore_index=True)
    if combined.empty:
        return combined
    combined["annualized_rate_pct"] = combined["monthly_rate_pct"] * 12
    return combined.sort_values("monthly_rate_pct", ascending=False).reset_index(drop=True)


def get_missing_debt_terms(limit: int = 10) -> pd.DataFrame:
    """Lenders and bank loan facilities that carry real outstanding money but have no interest
    rate/principal on file -- get_debt_cost_comparison() correctly excludes them from the cost
    comparison (there's no rate to compare), but that silence isn't itself actionable on its own.
    This turns it into a to-do list, ranked by how much is actually outstanding on each facility,
    so entering real terms starts with whichever one matters most rather than whichever's easiest
    to find. A facility with a rate already on file, or with nothing currently outstanding,
    doesn't appear here."""
    with get_conn(DB_PATH) as conn:
        lenders_df = pd.read_sql_query(
            """SELECT cr.creditor_name AS name, ln.note_ref AS reference, 'Private Loan' AS source,
                      COALESCE(SUM(c.amount), 0) AS outstanding
               FROM creditors cr
               JOIN loan_notes ln ON ln.creditor_id = cr.creditor_id
               LEFT JOIN checks_issued c
                 ON c.loan_note_id = ln.loan_note_id AND c.status = 'Active' AND c.check_date >= DATE('now', 'localtime')
               WHERE ln.monthly_interest_rate IS NULL
               GROUP BY ln.loan_note_id
               HAVING outstanding > 0""",
            conn,
        )
        bank_df = pd.read_sql_query(
            """SELECT cr.creditor_name AS name, bl.loan_ref AS reference, 'Bank Loan' AS source,
                      COALESCE(SUM(bli.amount), 0) AS outstanding
               FROM bank_loans bl
               JOIN creditors cr ON bl.creditor_id = cr.creditor_id
               LEFT JOIN bank_loan_installments bli
                 ON bli.bank_loan_id = bl.bank_loan_id AND bli.due_date >= DATE('now', 'localtime')
               WHERE bl.principal_amount IS NULL AND bl.is_cancelled = 0
               GROUP BY bl.bank_loan_id
               HAVING outstanding > 0""",
            conn,
        )
    combined = pd.concat([lenders_df, bank_df], ignore_index=True)
    if combined.empty:
        return combined
    return combined.sort_values("outstanding", ascending=False).head(limit).reset_index(drop=True)
