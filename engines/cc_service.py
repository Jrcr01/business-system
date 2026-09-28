import os
import sqlite3
from datetime import date

import pandas as pd

from engines.cc_dates import statement_date_for, statement_month_for

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

# A statement is expected roughly every ~30 days; flag a card if it's gone noticeably longer
# than that since its last recorded statement, in case one was simply forgotten. Not a hard
# rule (some cards can genuinely go a cycle with no charges) -- just a "check this" prompt.
STATEMENT_GAP_THRESHOLD_DAYS = 35

# When a card has no Statement day set, its statement is assumed to arrive this many days before
# last month's due day (used by get_statement_coverage()).
STATEMENT_ARRIVAL_LEAD_DAYS = 14


def get_statement_gap_alerts(today: date | None = None) -> pd.DataFrame:
    """The "a card statement may have been overlooked" warning on Payment run, the Dashboard and the Credit cards page.

    ONE rule since 2026-09-29 (owner: "did we arrive at same logic?" -- the warning used its own older rule, "more than
    STATEMENT_GAP_THRESHOLD_DAYS since the last due date", and disagreed with the Credit cards coverage table): it now comes straight
    from get_statement_coverage(), so the warning names exactly the cards the table marks "Missing this month" (only once the
    card's Expected-by day has passed) or "Missing both months" -> alert "Possibly missing a statement"; "No statement ever" ->
    alert "No statement on file yet" (the quiet note). Columns kept as before for the screens: card_id, card_name,
    card_number_last4, cardholder_name, last_due_date, alert -- plus status and expected_by."""
    cov = get_statement_coverage(today)
    if cov.empty:
        return pd.DataFrame(columns=["card_id", "card_name", "card_number_last4", "cardholder_name", "last_due_date", "alert", "status",
                                     "expected_by"])
    alert = cov["status"].map({"Missing this month": "Possibly missing a statement", "Missing both months": "Possibly missing a statement",
                               "No statement ever": "No statement on file yet"})
    out = cov.assign(alert=alert, cardholder_name=cov["cardholder"].where(cov["cardholder"] != "(no holder)", None))
    return out.loc[out["alert"].notna(), ["card_id", "card_name", "card_number_last4", "cardholder_name", "last_due_date", "alert", "status",
                                          "expected_by"]].reset_index(drop=True)


NO_DUE_PREFIX = "ccnodue|"


def no_due_marks() -> set:
    """{(card_id, 'YYYY-MM')} the owner marked "No due this month" (owner, 2026-09-29: "there are also times that recording statement
    is not necessary because it has no due so I opt not to record this"). Kept in duplicate_dismissals with a 'ccnodue|' key -- no
    new table -- and never adds anything to payables."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute("SELECT dup_key FROM duplicate_dismissals WHERE dup_key LIKE ?", (NO_DUE_PREFIX + "%",)).fetchall()
    out = set()
    for (k,) in rows:
        _, card_id, month = k.split("|", 2)
        out.add((int(card_id), month))
    return out


def mark_no_due(card_id: int, month: str, note: str = "") -> None:
    """Mark a card's statement month ('YYYY-MM') as having nothing due, so the missing-statement warning stops. Audited."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR IGNORE INTO duplicate_dismissals (dup_key, note) VALUES (?, ?)", (f"{NO_DUE_PREFIX}{int(card_id)}|{month}", note or None))
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CC_NO_DUE_MARKED', 'credit_cards', ?, ?)",
                     (int(card_id), f"No due for {month}" + (f": {note}" if note else "")))


def undo_no_due(card_id: int, month: str) -> None:
    """Take a "No due" mark back (the warning returns if the statement is still not recorded). Audited."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM duplicate_dismissals WHERE dup_key = ?", (f"{NO_DUE_PREFIX}{int(card_id)}|{month}",))
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CC_NO_DUE_UNDONE', 'credit_cards', ?, ?)",
                     (int(card_id), f"No due for {month} taken back"))


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
    good as the history behind it.
    Which month a statement belongs to (owner, 2026-09-29): the month of its STATEMENT DATE (engines/cc_dates.py -- the card's
    statement day 10-45 days before the due date; usually the month before the due month, sometimes the same month). The typed
    statement_month is NOT used: most old statements were labelled with their due month (160 of 200 on 2026-09-29), which made
    cards look OK while the current statement was not recorded yet. Cancelled statements are ignored."""
    today = today or date.today()
    this_start = today.replace(day=1)
    prev_start = date(this_start.year - (1 if this_start.month == 1 else 0), 12 if this_start.month == 1 else this_start.month - 1, 1)
    next_start = date(this_start.year + (1 if this_start.month == 12 else 0), 1 if this_start.month == 12 else this_start.month + 1, 1)
    prev_key, this_key = prev_start.strftime("%Y-%m"), this_start.strftime("%Y-%m")
    with sqlite3.connect(DB_PATH) as conn:
        cards = pd.read_sql_query(
            """SELECT cc.card_id, COALESCE(ch.name, '(no holder)') AS cardholder, cr.creditor_name AS issuer,
                      cc.card_name, cc.card_number_last4, cc.statement_day
               FROM credit_cards cc
               JOIN creditors cr ON cc.creditor_id = cr.creditor_id
               LEFT JOIN cardholders ch ON cc.cardholder_id = ch.cardholder_id
               WHERE cc.is_active = 1""", conn)
        stmts = pd.read_sql_query(
            "SELECT card_id, due_date, total_amount_due FROM cc_statements WHERE status != 'Cancelled' AND due_date IS NOT NULL", conn)
    sd_by_card = dict(zip(cards["card_id"], cards["statement_day"]))
    stmts["stmt_month"] = [statement_month_for(d, None if pd.isna(sd_by_card.get(c)) else sd_by_card.get(c))
                           for c, d in zip(stmts["card_id"], stmts["due_date"])]
    rows = []
    for _, cr in cards.iterrows():
        mine = stmts[stmts["card_id"] == cr["card_id"]]
        prev, this = mine[mine["stmt_month"] == prev_key], mine[mine["stmt_month"] == this_key]
        rows.append({**cr.to_dict(),
                     "prev_month_due": prev["due_date"].max() if not prev.empty else None,
                     "prev_month_amount": float(prev["total_amount_due"].sum()) if not prev.empty else None,
                     "this_month_due": this["due_date"].max() if not this.empty else None,
                     "this_month_amount": float(this["total_amount_due"].sum()) if not this.empty else None,
                     "last_due_date": mine["due_date"].max() if not mine.empty else None})
    df = pd.DataFrame(rows, columns=["card_id", "cardholder", "issuer", "card_name", "card_number_last4", "statement_day", "prev_month_due",
                                     "prev_month_amount", "this_month_due", "this_month_amount", "last_due_date"])
    if df.empty:
        df["status"] = pd.Series(dtype=str)
        return df

    no_due = no_due_marks()

    def status(r):
        if pd.notna(r["this_month_due"]):
            return "OK"
        if (int(r["card_id"]), this_key) in no_due:
            return "No due"                                  # owner marked: nothing due this month, so nothing to record
        if pd.notna(r["prev_month_due"]) or (int(r["card_id"]), prev_key) in no_due:
            return "Missing this month"
        if pd.notna(r["last_due_date"]):
            return "Missing both months"
        return "No statement ever"

    def expected_by(r):
        # Day of THIS month the statement should have arrived by; None when nothing to estimate from.
        last_day = (next_start - date.resolution).day
        if pd.notna(r["statement_day"]) and int(r["statement_day"]) > 0:
            day = int(r["statement_day"])
        elif pd.notna(r["prev_month_due"]):          # no statement day on file: last month's statement date, same day this month
            day = statement_date_for(r["prev_month_due"], None).day
        else:
            return None
        return date(this_start.year, this_start.month, min(day, last_day))

    df["status"] = df.apply(status, axis=1)
    df["expected_by"] = df.apply(expected_by, axis=1)
    not_yet = (df["status"] != "OK") & df["expected_by"].notna() & df["expected_by"].apply(lambda d: d is not None and today < d)
    df.loc[not_yet & (df["status"] == "Missing this month"), "status"] = "Not yet expected"
    df["expected_by"] = df["expected_by"].apply(lambda d: str(d) if d is not None and not pd.isna(d) else None)
    order = {"Missing this month": 0, "Missing both months": 1, "No statement ever": 2, "Not yet expected": 3, "No due": 4, "OK": 5}
    return df.assign(_o=df["status"].map(order)).sort_values(["_o", "cardholder", "issuer", "card_name"]).drop(columns="_o").reset_index(drop=True)


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