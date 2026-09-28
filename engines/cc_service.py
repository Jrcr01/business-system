import os
import sqlite3
from datetime import date

import pandas as pd
from engines.common import get_conn

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

# A statement is expected roughly every ~30 days; flag a card if it's gone noticeably longer
# than that since its last recorded statement, in case one was simply forgotten. Not a hard
# rule (some cards can genuinely go a cycle with no charges) -- just a "check this" prompt.
STATEMENT_GAP_THRESHOLD_DAYS = 35

# When a card has no Statement day set, its statement is assumed to arrive this many days before
# last month's due day (used by get_statement_coverage()).
STATEMENT_ARRIVAL_LEAD_DAYS = 14


def get_statement_gap_alerts() -> pd.DataFrame:
    """Flags cards that may be missing a recorded statement: no statement at all, or the most
    recent one's due_date is older than STATEMENT_GAP_THRESHOLD_DAYS. Helps catch "we forgot to
    encode this month's bill" before it's discovered late. Uses due_date as the recency signal --
    the only per-statement date left once statement_date became a card-level property
    (credit_cards.statement_day) instead of something re-typed per statement."""
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT cc.card_id, cc.card_name, cc.card_number_last4, ch.name AS cardholder_name,
                      MAX(cs.due_date) AS last_due_date
               FROM credit_cards cc
               LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id
               LEFT JOIN cc_statements cs ON cc.card_id = cs.card_id
               WHERE cc.is_active = 1
               GROUP BY cc.card_id""",
            conn,
        )
    if df.empty:
        return df
    today = date.today()

    def days_since(d):
        return None if pd.isna(d) else (today - date.fromisoformat(d)).days

    df["days_since_last_due_date"] = df["last_due_date"].apply(days_since)
    df["alert"] = df["days_since_last_due_date"].apply(
        lambda d: "No statement on file yet" if pd.isna(d) else ("Possibly missing a statement" if d > STATEMENT_GAP_THRESHOLD_DAYS else None)
    )
    return df[df["alert"].notna()].reset_index(drop=True)


def get_statement_coverage(today: date | None = None) -> pd.DataFrame:
    """Month-over-month "did we miss a card?" check (2026-09-19, user request): for every ACTIVE
    card, whether a statement is recorded (any due date) in the previous month and in the current
    month, so a card that had one last month but has none yet this month stands out.

    Status, most urgent first:
      "Missing this month"  had a statement last month, none recorded for this month, and it's
                            already the day it should have arrived (see below)
      "Not yet expected"    same, but this card's statement isn't due to arrive yet this month
      "Missing both months" older history exists, but nothing last month or this month
      "No statement ever"   nothing on file at all (new/unused card, or history never entered)
      "OK"                  has a statement recorded for this month
    "Expected by" is the card's Statement day when one is set; otherwise it is estimated as 14
    days before last month's due day (statements typically issue ~2-3 weeks ahead of the due date),
    so a card only turns up as missing once it's actually overdue for follow-up, not on the 1st.
    Depends on statements actually being on file -- last month only shows up once past
    statements have been entered (or backfilled), which is why an OK/Missing verdict is only as
    good as the history behind it."""
    today = today or date.today()
    this_start = today.replace(day=1)
    prev_start = date(this_start.year - (1 if this_start.month == 1 else 0), 12 if this_start.month == 1 else this_start.month - 1, 1)
    next_start = date(this_start.year + (1 if this_start.month == 12 else 0), 1 if this_start.month == 12 else this_start.month + 1, 1)
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT cc.card_id, COALESCE(ch.name, '(no holder)') AS cardholder, cr.creditor_name AS issuer,
                      cc.card_name, cc.card_number_last4, cc.statement_day,
                      MAX(CASE WHEN COALESCE(s.statement_month, SUBSTR(s.due_date, 1, 7)) = :prev THEN s.due_date END) AS prev_month_due,
                      SUM(CASE WHEN COALESCE(s.statement_month, SUBSTR(s.due_date, 1, 7)) = :prev THEN s.total_amount_due END) AS prev_month_amount,
                      MAX(CASE WHEN COALESCE(s.statement_month, SUBSTR(s.due_date, 1, 7)) = :this THEN s.due_date END) AS this_month_due,
                      SUM(CASE WHEN COALESCE(s.statement_month, SUBSTR(s.due_date, 1, 7)) = :this THEN s.total_amount_due END) AS this_month_amount,
                      MAX(s.due_date) AS last_due_date
               FROM credit_cards cc
               JOIN creditors cr ON cc.creditor_id = cr.creditor_id
               LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id
               LEFT JOIN cc_statements s ON cc.card_id = s.card_id
               WHERE cc.is_active = 1
               GROUP BY cc.card_id""",
            conn, params={"prev": prev_start.strftime("%Y-%m"), "this": this_start.strftime("%Y-%m")},
        )
    if df.empty:
        df["status"] = pd.Series(dtype=str)
        return df

    def status(r):
        if pd.notna(r["this_month_due"]):
            return "OK"
        if pd.notna(r["prev_month_due"]):
            return "Missing this month"
        if pd.notna(r["last_due_date"]):
            return "Missing both months"
        return "No statement ever"

    def expected_by(r):
        # Day of THIS month the statement should have arrived by; None when nothing to estimate from.
        last_day = (next_start - date.resolution).day
        if pd.notna(r["statement_day"]) and int(r["statement_day"]) > 0:
            day = int(r["statement_day"])
        elif pd.notna(r["prev_month_due"]):
            day = max(1, int(str(r["prev_month_due"])[8:10]) - STATEMENT_ARRIVAL_LEAD_DAYS)
        else:
            return None
        return date(this_start.year, this_start.month, min(day, last_day))

    df["status"] = df.apply(status, axis=1)
    df["expected_by"] = df.apply(expected_by, axis=1)
    not_yet = (df["status"] != "OK") & df["expected_by"].notna() & df["expected_by"].apply(lambda d: d is not None and today < d)
    df.loc[not_yet & (df["status"] == "Missing this month"), "status"] = "Not yet expected"
    df["expected_by"] = df["expected_by"].apply(lambda d: str(d) if d is not None and not pd.isna(d) else None)
    order = {"Missing this month": 0, "Missing both months": 1, "No statement ever": 2, "Not yet expected": 3, "OK": 4}
    return df.assign(_o=df["status"].map(order)).sort_values(["_o", "cardholder", "issuer", "card_name"]).drop(columns="_o").reset_index(drop=True)


_MONTH_LABELS = {0: "This month", 1: "Next month"}


def get_annual_fee_alerts(window_months: int = 2) -> pd.DataFrame:
    """Upcoming annual-fee months within window_months, for requesting a waiver ahead of time.
    Month-only precision (the real anniversary is the card's issuance date, which isn't on file
    for existing cards) -- so this flags the whole month as the risk window rather than a single
    day. Only cards with annual_fee_due_month set are considered."""
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT cc.card_id, cc.card_name, cc.card_number_last4, ch.name AS cardholder_name,
                      cc.annual_fee_due_month
               FROM credit_cards cc
               LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id
               WHERE cc.annual_fee_due_month IS NOT NULL""",
            conn,
        )
    if df.empty:
        return df
    today = date.today()
    df["months_until_due"] = df["annual_fee_due_month"].apply(lambda m: (int(m) - today.month) % 12)
    df = df[df["months_until_due"] <= window_months].sort_values("months_until_due").reset_index(drop=True)
    df["when"] = df["months_until_due"].apply(lambda n: _MONTH_LABELS.get(n, f"In {n} months"))
    return df

def settle_cc_statement_via_cash(
    cc_statement_id: int,
    payment_amount: float,
    payment_date: str,
    bank_account_id: int = 1,
    payment_method: str = "Cash / Online"
) -> tuple[bool, str]:
    """Settles a credit card statement using direct Cash/Online Transfer, updating balance and status.

    Also zeroes unpaid_balance_next and sets amount_paid = total_amount_due (2026-09-15,
    Unified Debt Interest Tracking) -- this is a full settlement (status becomes 'Paid'), so the
    rolling-balance figures need to agree with that, otherwise a statement could show 'Paid' here
    while still reporting a real unpaid balance rolling into next month's previous_balance."""
    with get_conn(DB_PATH) as conn:
        cursor = conn.cursor()

        cursor.execute("SELECT total_amount_due, status FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,))
        row = cursor.fetchone()
        if not row:
            return False, "Statement record not found."

        total_due, status = row
        if status == 'Paid':
            return False, "Statement is already marked as Paid."

        cursor.execute("""
            UPDATE cc_statements
            SET status = 'Paid',
                payment_method = ?,
                payment_date = ?,
                amount_paid = total_amount_due,
                unpaid_balance_next = 0
            WHERE cc_statement_id = ?
        """, (payment_method, payment_date, cc_statement_id))

        cursor.execute("""
            INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values)
            VALUES ('CC_STATEMENT_CASH_SETTLEMENT', 'cc_statements', ?, ?)
        """, (cc_statement_id, f"Settled PHP {payment_amount:,.2f} via {payment_method} on {payment_date}",))

        conn.commit()

    return True, f"Successfully paid statement via {payment_method} for PHP {payment_amount:,.2f}."