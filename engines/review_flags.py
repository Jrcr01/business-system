"""The Review list (2026-10-03): controls flag, they do not block. A step that breaks a duty rule goes through at once and is recorded here; the System admin or an owner looks
at the list when they want and ticks each line OK. Only four things are ever refused (a check above the batch balance, the same invoice number twice for a supplier, a
check number used twice, releasing a check that was never printed); everything else lands here.

  * flag(conn, kind, ref_type, ref_id, detail)   -- inside the caller's write_tx: add one line (the same open line is not added twice)
  * sod_flag(...)                                -- same person did two linked steps on one record? returns the plain-words detail, else None
  * open_flags() / open_count()                  -- what is waiting
  * acknowledge(flag_id)                         -- tick it OK (needs 'review.use'); keeps who and when"""
from __future__ import annotations

import pandas as pd

from engines.acting_user import acting_email
from engines.common import get_conn, write_tx
from engines.permissions import require as require_permission

SOD_RULES = {                           # kind: what it means in words
    "SOD_ORDERED_RECEIVED": "Ordered it and received it",
    "SOD_RECEIVED_MATCHED": "Received the goods and matched the invoice",
    "SOD_PARKED_PRINTED": "Parked the check and made the Chrysanth file",
    "SOD_PRINTED_RELEASED": "Printed the check and released it",
}
KINDS = {**SOD_RULES, "PRICE_OVER_PO": "Invoice price well above the order", "NEW_PAYEE": "New supplier or lender", "PO_HIGH_DAY": "Order placed on a high-payables day",
         "CLEARED_NOT_ON_BANK": "Ticked cleared, but not on the bank statement", "CHECK_NUMBER_SKIPPED": "Check numbers skipped in a booklet",
         "CC_PAYMENT_MISMATCH": "Card payments recorded daily differ from the SOA",
         "PO_MORE_THAN_ONE_DR": "Order kept open for another DR", "PO_RECEIVED_MORE": "More received than ordered",
         "PO_ITEM_ADDED_AT_DELIVERY": "An item came that was not on the order", "PO_RECEIPT_UNDONE": "A delivery was undone",
         # the revenue cycle, stage A (owner, 2026-10-11): flags, never blocks
         "STOCK_NEGATIVE": "Stock reads below zero", "STOCK_ADJUSTED": "Stock corrected outside a count", "COUNT_DIFFERENCE": "A count differed from the system",
         "AFFILIATE_LONG_OPEN": "Lent or borrowed tires open a long time", "SALE_BELOW_COST": "Sold below cost", "SALE_NO_CREDIT": "Charged to a customer without credit allowed",
         "SI_SKIPPED": "A sales invoice number was skipped", "SALE_TYPED_LATE": "A sale typed after its date", "SALE_VOIDED": "A sales invoice was voided",
         "SALE_RETURN": "An item was returned by a customer", "RELEASE_OPEN": "Goods released and not invoiced the same day",
         "SALE_NOT_SCANNED": "Sold without a scan", "AR_OVERDUE": "A charge invoice past its terms", "AR_WRITTEN_OFF": "An invoice written off",
         "AR_RECOVERED": "A written-off invoice is being paid", "AR_COLLECTION_VOIDED": "A collection receipt taken back", "AR_CHECK_BOUNCED": "A customer's check bounced", "AUTHENTICATOR_SET_UP": "An authenticator was set up", "NEW_DEVICE_SIGN_IN": "Signed in on a device not used before",
         "BOOKLET_SEQUENCE_GAP": "Check numbers in no booklet"}
PRICE_OVER_PCT = 10.0                   # an invoice price this far above the order's net price is flagged


def same_person(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and str(a).strip().lower() == str(b).strip().lower()


def sod_flag(kind: str, earlier_actor: str | None, record: str) -> str | None:
    """The detail text when the person acting now is the same one who did the earlier linked step on this record; None when it is someone else (or nobody is known)."""
    me = acting_email()
    if same_person(earlier_actor, me):
        return f"{SOD_RULES[kind]}: {record} (by {me})"
    return None


def flag(conn, kind: str, ref_type: str, ref_id: int | None, detail: str) -> None:
    if conn.execute("SELECT 1 FROM review_flags WHERE kind = ? AND ref_type IS ? AND ref_id IS ? AND detail = ? AND reviewed_at IS NULL", (kind, ref_type, ref_id, detail)).fetchone():
        return
    conn.execute("INSERT INTO review_flags (kind, ref_type, ref_id, detail, raised_by) VALUES (?, ?, ?, ?, acting_user())", (kind, ref_type, ref_id, detail))


def open_flags() -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query("SELECT flag_id, kind, detail, raised_by, raised_at FROM review_flags WHERE reviewed_at IS NULL ORDER BY flag_id", conn)


def open_count() -> int:
    with get_conn() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM review_flags WHERE reviewed_at IS NULL").fetchone()[0])


def acknowledge(flag_id: int) -> tuple[bool, str]:
    ok, why = require_permission("review.use")
    if not ok:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT kind, detail, reviewed_at FROM review_flags WHERE flag_id = ?", (int(flag_id),)).fetchone()
        if not row:
            return False, "That line is not on the list."
        if row[2]:
            return True, "Already ticked."
        conn.execute("UPDATE review_flags SET reviewed_by = acting_user(), reviewed_at = CURRENT_TIMESTAMP WHERE flag_id = ?", (int(flag_id),))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'REVIEW_OK', 'review_flags', ?, ?)",
                     (int(flag_id), f"{row[0]}: {row[1]}"))
    return True, "Ticked OK."
