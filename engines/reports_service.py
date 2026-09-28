import sqlite3

from engines.cc_rules import cc_owed_case, superseded_sql
from datetime import date, timedelta

import pandas as pd

import os
from engines.common import get_conn
from engines.clock import manila_today
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def next_banking_day(d) -> date:
    """A due date that falls on a Saturday or Sunday is actually paid the next banking day
    (Monday) -- reports-only display convention, confirmed explicitly: doesn't change any
    stored due_date or the auto-cleared-by-date rule elsewhere in this app, just how the
    Uncleared Checks / Schedule of Payments reports group and label things. Philippine
    holidays are deliberately out of scope for now (no holiday calendar exists in this system,
    and they change every year) -- weekends only."""
    if isinstance(d, str):
        d = date.fromisoformat(d)
    if d.weekday() == 5:  # Saturday
        return d + timedelta(days=2)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


def get_uncleared_checks_report() -> pd.DataFrame:
    """Every not-yet-cleared check (status='Active', check_date in the future -- same 'cleared'
    convention as v_effective_checks elsewhere in this app), one row per check: due date
    (=check_date), payee, which track it belongs to, check number, amount. Modeled directly on
    a real legacy "All Time Payee Checks" / uncleared-checks report the user shared, adapted to
    this system's shape -- Bank Loan and Credit Card dues are deliberately NOT included here,
    since neither track issues an actual check in this system anymore (both are untracked cash,
    per this session's design); this report is specifically about real check instruments still
    outstanding (checks_issued), matching the reference document's own scope (every row there
    has a real check number). Phase 6 Redux: also requires check_number IS NOT NULL -- printing
    and number-assignment are independent actions now, so the thing that actually makes a row
    "a real check instrument" is having a number, not whether its PDF has been generated. A
    check with no number yet (printed or not) belongs to get_parked_checks_report() instead
    (parked there means "not yet printed", a different, independent axis)."""
    query = """
    SELECT
        c.check_date AS due_date,
        cr.creditor_name AS payee_name,
        CASE WHEN c.statement_id IS NOT NULL THEN 'Supplier' WHEN c.loan_note_id IS NOT NULL THEN 'Lender' ELSE 'Other' END AS track,
        c.check_number,
        c.amount
    FROM checks_issued c
    JOIN creditors cr ON c.creditor_id = cr.creditor_id
    WHERE c.status = 'Active' AND c.check_date >= manila_today() AND c.check_number IS NOT NULL
    ORDER BY c.check_date ASC, cr.creditor_name ASC
    """
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(query, conn)
    if not df.empty:
        df["actual_payment_date"] = df["due_date"].apply(next_banking_day)
    return df


def get_uncleared_checks_daily_totals() -> pd.DataFrame:
    """Per-banking-day count + amount subtotal, mirroring the subtotal line under each date
    section in the reference report -- grouped by actual_payment_date (weekend due dates land
    on the following Monday's total), not the raw due_date, since that's the day money actually
    needs to be ready. Derived from get_uncleared_checks_report() so both stay consistent with
    a single source of the weekend-shift rule."""
    detail = get_uncleared_checks_report()
    if detail.empty:
        return pd.DataFrame(columns=["actual_payment_date", "num_checks", "total_amount"])
    grouped = detail.groupby("actual_payment_date").agg(num_checks=("amount", "count"), total_amount=("amount", "sum")).reset_index()
    return grouped.sort_values("actual_payment_date").reset_index(drop=True)


def get_parked_checks_report() -> pd.DataFrame:
    """Every check already recorded (issued) but not yet printed -- Phase 6 Redux: check_number
    is assigned at PRINT time, so a parked check genuinely has none yet. Distinct from
    get_uncleared_checks_report() (which is about clearance, not print status): a parked check
    can be past its own check_date already (a natural backfill candidate) or postdated into the
    future -- printed_at IS NULL is the only thing that matters here, same definition
    engines.check_run_service.get_pending_check_run() uses for "the current check run", just
    grouped by day for a report export (that function returns one flat list for the live UI)."""
    query = """
    SELECT
        c.check_date AS due_date,
        cr.creditor_name AS payee_name,
        CASE WHEN c.statement_id IS NOT NULL THEN 'Supplier' WHEN c.loan_note_id IS NOT NULL THEN 'Lender' ELSE 'Other' END AS track,
        COALESCE(os.statement_ref, ln.note_ref) AS reference,
        c.amount
    FROM checks_issued c
    JOIN creditors cr ON c.creditor_id = cr.creditor_id
    LEFT JOIN order_statements os ON os.statement_id = c.statement_id
    LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
    WHERE c.status = 'Active' AND c.printed_at IS NULL
    ORDER BY c.check_date ASC, cr.creditor_name ASC
    """
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(query, conn)
    if not df.empty:
        df["actual_payment_date"] = df["due_date"].apply(next_banking_day)
    return df


def get_parked_checks_daily_totals() -> pd.DataFrame:
    """Per-banking-day count + amount subtotal for parked (not-yet-printed) checks -- derived
    from get_parked_checks_report() so both stay consistent with a single source of the
    weekend-shift rule."""
    detail = get_parked_checks_report()
    if detail.empty:
        return pd.DataFrame(columns=["actual_payment_date", "num_checks", "total_amount"])
    grouped = detail.groupby("actual_payment_date").agg(num_checks=("amount", "count"), total_amount=("amount", "sum")).reset_index()
    return grouped.sort_values("actual_payment_date").reset_index(drop=True)


_PAYMENT_SCHEDULE_UNION = """
    -- 2026-09-22: 'today and future' -- an item due TODAY is still to be paid, so >= (it used to start tomorrow).
    SELECT c.check_date AS due_date, cr.creditor_name AS payee_name, 'Supplier' AS track, COALESCE(c.check_number, 'Pending') AS reference, os.statement_ref AS batch, c.amount AS amount
    FROM checks_issued c JOIN creditors cr ON c.creditor_id = cr.creditor_id
    LEFT JOIN order_statements os ON os.statement_id = c.statement_id
    WHERE c.status = 'Active' AND c.statement_id IS NOT NULL AND c.check_date >= manila_today()

    UNION ALL

    -- Lender checks aren't batched (Loan Notes track, not Order Statements) -- batch is NULL.
    SELECT c.check_date AS due_date, cr.creditor_name AS payee_name, 'Lender' AS track, COALESCE(c.check_number, 'Pending') AS reference, NULL AS batch, c.amount AS amount
    FROM checks_issued c JOIN creditors cr ON c.creditor_id = cr.creditor_id
    WHERE c.status = 'Active' AND c.loan_note_id IS NOT NULL AND c.check_date >= manila_today()

    UNION ALL

    -- Bank Loan installments have no batch concept either -- NULL, same as Lender above.
    SELECT bli.due_date AS due_date, cr.creditor_name AS payee_name, 'Bank Loan' AS track,
           CASE WHEN lb.name IS NOT NULL THEN lb.name || ' - ' || bl.loan_ref ELSE bl.loan_ref END AS reference,
           NULL AS batch,
           bli.amount AS amount
    FROM bank_loan_installments bli
    JOIN bank_loans bl ON bli.bank_loan_id = bl.bank_loan_id
    JOIN creditors cr ON bl.creditor_id = cr.creditor_id
    LEFT JOIN loan_beneficiaries lb ON bl.beneficiary_id = lb.beneficiary_id
    WHERE bli.due_date >= manila_today() AND bl.is_cancelled = 0

    UNION ALL

    -- Credit Card statements have no batch concept either -- NULL, same as Lender/Bank Loan above.
    SELECT cs.due_date AS due_date, cr.creditor_name AS payee_name, 'Credit Card' AS track,
           CASE WHEN ch.name IS NOT NULL THEN ch.name || ' - ' || cc.card_name || ' - ' || cc.card_number_last4
                ELSE cc.card_name || ' - ' || cc.card_number_last4 END AS reference,
           NULL AS batch,
           CASE WHEN cs.unpaid_balance_next > 0 THEN cs.unpaid_balance_next ELSE cs.total_amount_due END AS amount
    FROM cc_statements cs
    JOIN credit_cards cc ON cs.card_id = cc.card_id
    JOIN creditors cr ON cc.creditor_id = cr.creditor_id
    LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id
    WHERE cs.status IN ('Unpaid', 'Partially Paid') AND cs.due_date >= manila_today()
      AND NOT (cs.unpaid_balance_next > 0 AND """ + superseded_sql("cs") + """)
"""


PAYMENT_SCHEDULE_TRACK_ORDER = {"Lender": 0, "Bank Loan": 1, "Supplier": 2, "Credit Card": 3}


def get_payment_schedule_all_tracks() -> pd.DataFrame:
    """Combined schedule of payments across all 4 AP tracks -- everything still owed, one row
    per obligation, sorted by due date. Each track uses its own already-established "still
    owed" definition (same as get_combined_ap_summary()): Supplier/Lender checks not yet
    cleared, Bank Loan installments and Credit Card statements not yet past their due date.
    "reference" is whatever identifies that row within its own track (check number for
    Supplier/Lender, loan ref for Bank Loan -- prefixed with the loan's attributable person's
    name when one is on file, e.g. "Emelita Roxas - PENDING-153", so it's clear at a glance
    whose loan a row belongs to -- card name+last4 for Credit Card, same cardholder-prefix
    treatment, e.g. "Rhodora Roxas - Shopmore - 4102", 2026-09-17) -- there's no single concept
    that means the same thing across all four, so the column just holds whichever one applies.
    "batch" is the Order Statement's statement_ref for Supplier rows (2026-09-20, PDF export
    request) -- the only track actually built on batches; NULL for Lender/Bank Loan/Credit Card,
    which have no such concept.
    actual_payment_date shifts a weekend due_date to the following Monday (reports-only display
    convention -- see next_banking_day())."""
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(f"SELECT * FROM ({_PAYMENT_SCHEDULE_UNION}) ORDER BY due_date ASC, track ASC", conn)
    if not df.empty:
        df["actual_payment_date"] = df["due_date"].apply(next_banking_day)
        # Within each payment day: by track (Lender, Bank Loan, Supplier, Credit Card), then smallest amount first.
        df["_track_rank"] = df["track"].map(PAYMENT_SCHEDULE_TRACK_ORDER).fillna(len(PAYMENT_SCHEDULE_TRACK_ORDER))
        df = df.sort_values(["actual_payment_date", "_track_rank", "amount"], kind="stable").drop(columns="_track_rank").reset_index(drop=True)
    return df


def get_payment_schedule_daily_totals() -> pd.DataFrame:
    """Per-banking-day count + amount subtotal across all 4 tracks combined, grouped by
    actual_payment_date (weekend due dates land on the following Monday's total) -- derived
    from get_payment_schedule_all_tracks() so both stay consistent with a single source of the
    weekend-shift rule."""
    detail = get_payment_schedule_all_tracks()
    if detail.empty:
        return pd.DataFrame(columns=["actual_payment_date", "num_items", "total_amount"])
    grouped = detail.groupby("actual_payment_date").agg(num_items=("amount", "count"), total_amount=("amount", "sum")).reset_index()
    return grouped.sort_values("actual_payment_date").reset_index(drop=True)


def get_combined_ap_summary() -> pd.DataFrame:
    """One row per AP track plus a grand total, each using that track's own established
    definition of "still owed": Supplier sums Unpaid/Partially Paid invoices (full amount, no
    per-invoice paid tracking exists); Lender sums Outstanding (uncleared) for live notes only
    (Active/Defaulted -- Cancelled/Settled notes aren't real obligations anymore); Bank Loan
    sums installments whose due date hasn't passed yet (past-due ones are auto-considered paid,
    per this app's standing convention); Credit Card sums statements not yet past due for the
    same reason. Deliberately does not attempt a single cross-track "Overdue" column -- Bank
    Loan and Credit Card both auto-clear by date (no such thing as "overdue" there once that
    convention applies), while Supplier invoices and Lender notes can be genuinely overdue
    (postdated checks not yet covering a due date) -- the concepts don't generalize cleanly
    enough to force into one column without being misleading for half the tracks.
    **Due today = still owed** (owner, 2026-09-28): a lender check dated today and an installment due today still count as owed -- the same
    rule as the Schedule of payments and the Dashboard total."""
    with get_conn(DB_PATH) as conn:
        supplier = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM invoices WHERE status IN ('Unpaid', 'Partially Paid')"
        ).fetchone()[0]
        lender = conn.execute(
            """SELECT COALESCE(SUM(
                   total_payable - COALESCE(
                       (SELECT SUM(amount) FROM checks_issued
                        WHERE loan_note_id = ln.loan_note_id AND status = 'Active' AND check_date < manila_today()), 0
                   )
               ), 0)
               FROM loan_notes ln WHERE status IN ('Active', 'Defaulted')"""
        ).fetchone()[0]
        bank_loan = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM bank_loan_installments WHERE due_date >= manila_today() AND bank_loan_id NOT IN (SELECT bank_loan_id FROM bank_loans WHERE is_cancelled = 1)"
        ).fetchone()[0]
        credit_card = conn.execute(
            f"""SELECT COALESCE(SUM({cc_owed_case('cs')}), 0)
               FROM cc_statements cs WHERE cs.status NOT IN ('Paid', 'Cancelled')"""
        ).fetchone()[0]

    df = pd.DataFrame([
        {"track": "Supplier (Order Statements)", "outstanding": supplier},
        {"track": "Private Lender (Loan Notes)", "outstanding": lender},
        {"track": "Bank Loan", "outstanding": bank_loan},
        {"track": "Credit Card", "outstanding": credit_card},
    ])
    df.loc[len(df)] = {"track": "TOTAL", "outstanding": df["outstanding"].sum()}
    return df


def get_supplier_ap_aging_report() -> pd.DataFrame:
    """Generates supplier AP aging matrix. Includes 'Partially Paid' invoices (using their full
    amount, same as 'Unpaid') alongside 'Unpaid' -- excluding them entirely used to silently
    drop a partially-paid invoice's remaining exposure from every bucket. Invoices don't track a
    per-invoice paid amount (a batch's checks pay the net total, not individual lines), so the
    full amount is the best available signal for "still not fully covered," consistent with how
    Outstanding is computed at the batch level elsewhere in this app."""
    query = """
    SELECT
        c.creditor_name AS supplier_name,
        COUNT(i.invoice_id) AS open_invoices,
        SUM(i.amount) AS total_unpaid,
        SUM(CASE WHEN (JULIANDAY(manila_today()) - JULIANDAY(i.due_date)) <= 0 THEN i.amount ELSE 0 END) AS current_due,
        SUM(CASE WHEN (JULIANDAY(manila_today()) - JULIANDAY(i.due_date)) BETWEEN 1 AND 30 THEN i.amount ELSE 0 END) AS days_1_30,
        SUM(CASE WHEN (JULIANDAY(manila_today()) - JULIANDAY(i.due_date)) BETWEEN 31 AND 60 THEN i.amount ELSE 0 END) AS days_31_60,
        SUM(CASE WHEN (JULIANDAY(manila_today()) - JULIANDAY(i.due_date)) > 60 THEN i.amount ELSE 0 END) AS days_over_60
    FROM creditors c
    JOIN invoices i ON c.creditor_id = i.creditor_id AND i.status IN ('Unpaid', 'Partially Paid')
    WHERE c.creditor_type = 'SUPPLIER'
    GROUP BY c.creditor_id
    """
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(query, conn).fillna(0.0)

def get_private_lender_master_report() -> pd.DataFrame:
    """Generates private lender facility and PDC exposure report. outstanding_uncleared uses the
    same cleared-vs-issued convention as the Loan Notes tab itself (check_date before today; due today = still owed) --
    replaces a stale issuance-based sum that summed every Active check's face value regardless
    of whether it had actually cleared, overstating exposure for any note with postdated checks."""
    query = """
    SELECT
        c.creditor_name AS lender_name,
        ln.note_ref, ln.issue_date, ln.principal_amount, ln.total_interest, ln.total_payable,
        ln.total_payable - COALESCE(
            (SELECT SUM(amount) FROM checks_issued
             WHERE loan_note_id = ln.loan_note_id AND status = 'Active' AND check_date < manila_today()), 0
        ) AS outstanding_uncleared,
        ln.status
    FROM loan_notes ln
    JOIN creditors c ON ln.creditor_id = c.creditor_id
    WHERE c.creditor_type = 'LENDER'
    """
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(query, conn).fillna(0.0)

def get_supplier_history(creditor_id: int):
    """Full account history for one supplier -- every order statement (batch), every invoice
    under it, and every check issued against it. Reports tab drill-down (2026-09-14): the user
    wanted "all batches or checks or invoices related to a supplier" for one supplier at a time,
    to reconcile a single account end-to-end rather than reading it out of the aggregate
    master reports."""
    with get_conn(DB_PATH) as conn:
        statements = pd.read_sql_query(
            "SELECT statement_id, statement_ref, total_amount, status, datetime(created_at, '+8 hours') AS created_at "
            "FROM order_statements WHERE creditor_id = ? ORDER BY created_at",
            conn, params=(creditor_id,),
        )
        invoices = pd.read_sql_query(
            "SELECT i.doc_ref, i.invoice_date, i.due_date, i.gross_amount, i.amount, i.status, "
            "os.statement_ref FROM invoices i LEFT JOIN order_statements os ON os.statement_id = i.statement_id "
            "WHERE i.creditor_id = ? ORDER BY i.invoice_date",
            conn, params=(creditor_id,),
        )
        checks = pd.read_sql_query(
            "SELECT c.check_date, c.check_number, c.amount, c.status, os.statement_ref "
            "FROM checks_issued c LEFT JOIN order_statements os ON os.statement_id = c.statement_id "
            "WHERE c.creditor_id = ? AND c.statement_id IS NOT NULL ORDER BY c.check_date",
            conn, params=(creditor_id,),
        )
    return statements, invoices, checks


def get_payables_rundown(creditor_id: int | None = None, date_from=None, date_to=None) -> pd.DataFrame:
    """One row per supplier batch (order statement), for one supplier or all of them, with how
    much of it is paid and what is still open. Batches are the unit here because that is what is
    actually paid: the migrated history has batches and checks but no invoice lines.

    paid       = Active checks whose date has passed (cleared -- the same rule used everywhere; dated today = still owed)
    scheduled  = Active checks still post-dated (issued, not yet due)
    open       = net total - paid - scheduled (not covered by any check yet)
    status     = Paid (paid covers the net total) / Partially Paid (some paid) / Pending (none paid yet)
    From/To narrow to batches with a check dated inside that window (a batch with no checks yet is
    matched on its created date) -- the amounts shown are always the whole batch.
    Cancelled batches and empty drafts are left out."""
    query = """
    SELECT c.creditor_name AS supplier, b.statement_ref AS batch,
           (SELECT MIN(k.check_date) FROM checks_issued k WHERE k.statement_id = b.statement_id AND k.status = 'Active') AS first_check,
           (SELECT MAX(k.check_date) FROM checks_issued k WHERE k.statement_id = b.statement_id AND k.status = 'Active') AS last_check,
           (SELECT DATE(os.created_at, '+8 hours') FROM order_statements os WHERE os.statement_id = b.statement_id) AS created,
           b.net_total AS net_total,
           COALESCE((SELECT SUM(k.amount) FROM checks_issued k WHERE k.statement_id = b.statement_id
                     AND k.status = 'Active' AND k.check_date < manila_today()), 0) AS paid,
           COALESCE((SELECT SUM(k.amount) FROM checks_issued k WHERE k.statement_id = b.statement_id
                     AND k.status = 'Active' AND k.check_date >= manila_today()), 0) AS scheduled
    FROM v_batch_net_totals b
    JOIN creditors c ON c.creditor_id = b.creditor_id
    WHERE b.status = 'Issued' AND c.creditor_type = 'SUPPLIER'
      AND (? IS NULL OR b.creditor_id = ?)
    ORDER BY c.creditor_name, b.statement_id
    """
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(query, conn, params=(creditor_id, creditor_id))
    df["open"] = (df["net_total"] - df["paid"] - df["scheduled"]).round(2)
    df["balance"] = (df["net_total"] - df["paid"]).round(2)
    df["status"] = df.apply(
        lambda r: "Paid" if r["net_total"] > 0 and r["balance"] <= 0.005 else ("Partially Paid" if r["paid"] > 0.005 else "Pending"),
        axis=1,
    ) if not df.empty else pd.Series(dtype=str)
    return filter_rundown(df, date_from, date_to)


def filter_rundown(df: pd.DataFrame, date_from=None, date_to=None) -> pd.DataFrame:
    """Batches with a check dated inside the window (a batch with no checks yet: on its created date). Split out of get_payables_rundown
    (2026-09-28) so the Reports screen reads the batches once and narrows them in memory."""
    if df.empty or (date_from is None and date_to is None):
        return df
    lo = str(date_from) if date_from else "0000-01-01"
    hi = str(date_to) if date_to else "9999-12-31"
    has_checks = df["first_check"].notna()
    in_window = (df["last_check"].fillna("") >= lo) & (df["first_check"].fillna("9999") <= hi)
    created_in = (df["created"].fillna("") >= lo) & (df["created"].fillna("") <= hi)
    return df[(has_checks & in_window) | (~has_checks & created_in)].copy()


def filter_schedule(df: pd.DataFrame, tracks: list | None = None, date_from=None, date_to=None, payees: list | None = None) -> pd.DataFrame:
    """The schedule of payments narrowed the way the Reports screen shows it (2026-09-28: date range and payee filters added): tracks,
    payment dates (actual_payment_date, inclusive) and payees. The screen, the CSV / Excel and the PDF all use this, so they match."""
    if df.empty:
        return df
    out = df
    if tracks:
        out = out[out["track"].isin(tracks)]
    days = out["actual_payment_date"].astype(str).str[:10]
    if date_from:
        out = out[days >= str(date_from)]
        days = out["actual_payment_date"].astype(str).str[:10]
    if date_to:
        out = out[days <= str(date_to)]
    if payees:
        out = out[out["payee_name"].isin(payees)]
    return out


def get_invoice_rundown(creditor_id: int | None = None, date_from=None, date_to=None) -> pd.DataFrame:
    """One row per invoice (cancelled ones left out), for one supplier or all, with its status
    shown as Paid / Partially Paid / Pending (the stored 'Unpaid'). Date range is on invoice date."""
    query = """
    SELECT c.creditor_name AS supplier, i.doc_ref AS invoice, os.statement_ref AS batch,
           i.invoice_date, i.due_date, i.amount,
           CASE i.status WHEN 'Unpaid' THEN 'Pending' ELSE i.status END AS status
    FROM invoices i
    JOIN creditors c ON c.creditor_id = i.creditor_id
    LEFT JOIN order_statements os ON os.statement_id = i.statement_id
    WHERE i.status != 'Cancelled' AND c.creditor_type = 'SUPPLIER'
      AND (? IS NULL OR i.creditor_id = ?)
      AND (? IS NULL OR i.invoice_date >= ?) AND (? IS NULL OR i.invoice_date <= ?)
    ORDER BY c.creditor_name, i.invoice_date, i.doc_ref
    """
    d1 = str(date_from) if date_from else None
    d2 = str(date_to) if date_to else None
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(query, conn, params=(creditor_id, creditor_id, d1, d1, d2, d2))


def get_lender_history(creditor_id: int):
    """Full account history for one private lender -- every loan note and every check issued
    against each note. Same per-entity drill-down as get_supplier_history(), for the Lender
    track."""
    with get_conn(DB_PATH) as conn:
        notes = pd.read_sql_query(
            """SELECT loan_note_id, note_ref, issue_date, principal_amount, total_interest, total_payable, status,
                      total_payable - COALESCE(
                          (SELECT SUM(amount) FROM checks_issued
                           WHERE loan_note_id = ln.loan_note_id AND status = 'Active' AND check_date < manila_today()), 0
                      ) AS outstanding_uncleared
               FROM loan_notes ln WHERE creditor_id = ? ORDER BY issue_date""",
            conn, params=(creditor_id,),
        )
        checks = pd.read_sql_query(
            "SELECT c.check_date, c.check_number, c.amount, c.status, ln.note_ref "
            "FROM checks_issued c JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id "
            "WHERE ln.creditor_id = ? ORDER BY c.check_date",
            conn, params=(creditor_id,),
        )
    return notes, checks


def get_bank_loan_history(bank_loan_id: int):
    """Header info plus every installment for one bank loan. Same per-entity drill-down as
    get_supplier_history(), for the Bank Loan track -- "paid" is derived the same way as
    get_bank_loan_servicing_report() (due_date in the past = paid), since no separate paid flag
    is stored for this track."""
    with get_conn(DB_PATH) as conn:
        info = pd.read_sql_query(
            """SELECT bl.loan_ref, bl.account_number, bl.start_date, bl.maturity_date, bl.principal_amount,
                      bl.mode_of_payment, bl.status, c.creditor_name AS bank_name, lb.name AS beneficiary
               FROM bank_loans bl
               JOIN creditors c ON bl.creditor_id = c.creditor_id
               LEFT JOIN loan_beneficiaries lb ON bl.beneficiary_id = lb.beneficiary_id
               WHERE bl.bank_loan_id = ?""",
            conn, params=(bank_loan_id,),
        )
        installments = pd.read_sql_query(
            "SELECT installment_no, due_date, amount FROM bank_loan_installments "
            "WHERE bank_loan_id = ? ORDER BY installment_no",
            conn, params=(bank_loan_id,),
        )
    if not installments.empty:
        installments["status"] = installments["due_date"].apply(
            lambda d: "Paid" if date.fromisoformat(d) < manila_today() else "Outstanding"
        )
    return info, installments


def get_credit_card_history(card_id: int):
    """Header info plus every statement for one credit card. Same per-entity drill-down as
    get_supplier_history(), for the Credit Card track."""
    with get_conn(DB_PATH) as conn:
        info = pd.read_sql_query(
            """SELECT cc.card_name, cc.card_number_last4, cc.credit_limit, cc.annual_fee_due_month, cc.statement_day,
                      c.creditor_name AS issuer, ch.name AS cardholder
               FROM credit_cards cc
               JOIN creditors c ON cc.creditor_id = c.creditor_id
               LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id
               WHERE cc.card_id = ?""",
            conn, params=(card_id,),
        )
        statements = pd.read_sql_query(
            "SELECT due_date, total_amount_due, status, payment_method, payment_date "
            "FROM cc_statements WHERE card_id = ? ORDER BY due_date",
            conn, params=(card_id,),
        )
    return info, statements


def get_cardholder_all_cards_history(cardholder_id: int | None):
    """Combined statement history across every card belonging to one cardholder (or every card
    with no cardholder assigned, when cardholder_id is None) -- the "All cards" option in the
    Reports tab's Credit Card drill-down, for a cardholder who has more than one card and the
    user wants one combined Statement of Account rather than picking cards one at a time."""
    holder_clause = "cc.cardholder_id IS NULL" if cardholder_id is None else "cc.cardholder_id = ?"
    params = () if cardholder_id is None else (cardholder_id,)
    with get_conn(DB_PATH) as conn:
        cards = pd.read_sql_query(
            f"""SELECT cc.card_id, cc.card_name, cc.card_number_last4, cc.credit_limit, c.creditor_name AS issuer
                FROM credit_cards cc JOIN creditors c ON c.creditor_id = cc.creditor_id
                WHERE {holder_clause} ORDER BY cc.card_name""",
            conn, params=params,
        )
        statements = pd.read_sql_query(
            f"""SELECT cs.due_date, cs.total_amount_due, cs.status, cs.payment_method, cs.payment_date,
                       cc.card_name, cc.card_number_last4
                FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id
                WHERE {holder_clause} ORDER BY cs.due_date""",
            conn, params=params,
        )
    return cards, statements


def get_bank_loan_servicing_report() -> pd.DataFrame:
    """Generates bank loan servicing report. Just Due Date + Amount per installment -- nothing
    is computed by this system except "cleared" (due_date before today), the same standing
    convention as a check's cleared status elsewhere in this app. outstanding sums whatever
    hasn't cleared yet. beneficiary/beneficiary_type surface who the loan is actually
    attributable to (a person or affiliate the company is paying for), separate from the bank."""
    query = """
    SELECT
        c.creditor_name AS bank_name, bl.loan_ref, bl.account_number, bl.start_date, bl.maturity_date,
        bl.principal_amount,
        CASE WHEN bl.is_cancelled = 1 THEN 0 ELSE COALESCE(
            (SELECT SUM(amount) FROM bank_loan_installments
             WHERE bank_loan_id = bl.bank_loan_id AND due_date >= manila_today()), 0
        ) END AS outstanding,
        bl.mode_of_payment, CASE WHEN bl.is_cancelled = 1 THEN 'Cancelled' ELSE bl.status END AS status, lb.name AS beneficiary, lb.beneficiary_type
    FROM bank_loans bl
    JOIN creditors c ON bl.creditor_id = c.creditor_id
    LEFT JOIN loan_beneficiaries lb ON bl.beneficiary_id = lb.beneficiary_id
    WHERE c.creditor_type = 'BANK_LOAN'
    """
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(query, conn)