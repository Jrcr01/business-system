import os
import sqlite3
from datetime import date

import pandas as pd

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

# A statement is expected roughly every ~30 days; flag a card if it's gone noticeably longer
# than that since its last recorded statement, in case one was simply forgotten. Not a hard
# rule (some cards can genuinely go a cycle with no charges) -- just a "check this" prompt.
STATEMENT_GAP_THRESHOLD_DAYS = 35


def get_statement_gap_alerts() -> pd.DataFrame:
    """Flags cards that may be missing a recorded statement: no statement at all, or the most
    recent one's due_date is older than STATEMENT_GAP_THRESHOLD_DAYS. Helps catch "we forgot to
    encode this month's bill" before it's discovered late. Uses due_date as the recency signal --
    the only per-statement date left once statement_date became a card-level property
    (credit_cards.statement_day) instead of something re-typed per statement."""
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT cc.card_id, cc.card_name, cc.card_number_last4, ch.name AS cardholder_name,
                      MAX(cs.due_date) AS last_due_date
               FROM credit_cards cc
               LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id
               LEFT JOIN cc_statements cs ON cc.card_id = cs.card_id
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


_MONTH_LABELS = {0: "This month", 1: "Next month"}


def get_annual_fee_alerts(window_months: int = 2) -> pd.DataFrame:
    """Upcoming annual-fee months within window_months, for requesting a waiver ahead of time.
    Month-only precision (the real anniversary is the card's issuance date, which isn't on file
    for existing cards) -- so this flags the whole month as the risk window rather than a single
    day. Only cards with annual_fee_due_month set are considered."""
    with sqlite3.connect(DB_PATH) as conn:
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
    with sqlite3.connect(DB_PATH) as conn:
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