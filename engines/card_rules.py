"""Credit cards: cardholders, cards and card statements.

Moved out of app.py unchanged (2026-09-27) so the rules can be read and tested without the screens; app.py imports
every name back, so app.<name> keeps working."""
from __future__ import annotations

import sqlite3

from datetime import date
from engines.validators import money_amount_error
from engines.common import get_conn, php, q_live


def add_cardholder(name: str, department: str, contact_number: str) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO cardholders (name, department, contact_number) VALUES (?, ?, ?)",
            (name, department or None, contact_number or None),
        )
        conn.commit()
        return cur.lastrowid


def edit_cardholder(cardholder_id: int, name: str, department: str, contact_number: str) -> tuple[bool, str]:
    if not name.strip():
        return False, "Name can't be blank."
    with get_conn() as conn:
        conn.execute(
            "UPDATE cardholders SET name = ?, department = ?, contact_number = ? WHERE cardholder_id = ?",
            (name.strip(), department or None, contact_number or None, cardholder_id),
        )
        conn.commit()
    return True, "Cardholder updated."


def deactivate_cardholder(cardholder_id: int) -> None:
    """Reversible is_active toggle -- a cardholder who's left stops appearing as pickable for new
    cards/statements but keeps their existing history intact (mirrors deactivate_creditor())."""
    with get_conn() as conn:
        conn.execute("UPDATE cardholders SET is_active = 0 WHERE cardholder_id = ?", (cardholder_id,))
        conn.commit()


def reactivate_cardholder(cardholder_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE cardholders SET is_active = 1 WHERE cardholder_id = ?", (cardholder_id,))
        conn.commit()


def add_credit_card(
    creditor_id: int, cardholder_id: int | None, card_name: str, last4: str, credit_limit: float,
    annual_fee_due_month: int | None, statement_day: int | None,
) -> tuple[bool, str]:
    """Validates credit_limit itself rather than trusting the caller — credit_cards.credit_limit
    has a CHECK(credit_limit > 0) constraint, and a 0-or-blank widget left untouched would
    otherwise hit that raw sqlite3.IntegrityError uncaught, same class of bug as
    create_loan_note()'s principal_amount."""
    if (err := money_amount_error(credit_limit, "Credit limit")):
        return False, err
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO credit_cards (creditor_id, cardholder_id, card_name, card_number_last4, credit_limit, annual_fee_due_month, statement_day)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (creditor_id, cardholder_id, card_name, last4, credit_limit, annual_fee_due_month, statement_day),
        )
        conn.commit()
    return True, "Card added."


def edit_credit_card(
    card_id: int, card_name: str, creditor_id: int, cardholder_id: int | None,
    credit_limit: float, annual_fee_due_month: int | None, statement_day: int | None,
) -> tuple[bool, str]:
    if not card_name.strip():
        return False, "Card name can't be blank."
    if (err := money_amount_error(credit_limit, "Credit limit")):
        return False, err
    with get_conn() as conn:
        conn.execute(
            "UPDATE credit_cards SET card_name = ?, creditor_id = ?, cardholder_id = ?, credit_limit = ?, annual_fee_due_month = ?, statement_day = ? WHERE card_id = ?",
            (card_name.strip(), creditor_id, cardholder_id, credit_limit, annual_fee_due_month, statement_day, card_id),
        )
        conn.commit()
    return True, "Card updated."


def deactivate_credit_card(card_id: int) -> None:
    """Reversible is_active toggle -- a closed/replaced card stops appearing as pickable for a new
    statement but keeps its existing statement history intact (mirrors deactivate_creditor())."""
    with get_conn() as conn:
        conn.execute("UPDATE credit_cards SET is_active = 0 WHERE card_id = ?", (card_id,))
        conn.commit()


def reactivate_credit_card(card_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE credit_cards SET is_active = 1 WHERE card_id = ?", (card_id,))
        conn.commit()


def get_last_cc_statement_unpaid_balance(card_id: int) -> float:
    """This card's most recent statement's unpaid_balance_next — auto-fills the next
    statement's previous_balance, so the rolling balance chains correctly month to month.
    A card with no statements yet, or whose statements predate rolling-balance tracking
    (unpaid_balance_next defaults to 0), simply starts from 0."""
    row = q_live(
        "SELECT unpaid_balance_next FROM cc_statements WHERE card_id = ? ORDER BY due_date DESC, cc_statement_id DESC LIMIT 1",
        (card_id,),
    )
    return float(row["unpaid_balance_next"][0]) if not row.empty else 0.0


def get_unconfirmed_cc_statement(card_id: int) -> dict | None:
    """The card's latest statement whose payment isn't known yet (paid_confirmed = 0), if any."""
    row = q_live(
        "SELECT cc_statement_id, due_date, total_amount_due FROM cc_statements WHERE card_id = ? AND paid_confirmed = 0 AND status != 'Cancelled' "
        "ORDER BY due_date DESC, cc_statement_id DESC LIMIT 1",
        (card_id,),
    )
    return None if row.empty else {"id": int(row["cc_statement_id"][0]), "due_date": row["due_date"][0], "total": float(row["total_amount_due"][0])}


def confirm_cc_payment_from_next_statement(cc_statement_id: int, carried_over: float) -> tuple[float, str]:
    """The next statement shows what was carried over from this one, so what was paid is its total
    minus that balance (never below 0 or above the total). Marks the statement confirmed and sets its
    status. Returns (amount_paid, new_status)."""
    with get_conn() as conn:
        (total,) = conn.execute("SELECT total_amount_due FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,)).fetchone()
        paid = round(min(max(total - carried_over, 0.0), total), 2)
        unpaid_next = round(carried_over, 2)
        status = "Paid" if unpaid_next <= 0.005 else ("Partially Paid" if paid > 0.005 else "Unpaid")
        conn.execute(
            "UPDATE cc_statements SET amount_paid = ?, unpaid_balance_next = ?, status = ?, paid_confirmed = 1 WHERE cc_statement_id = ?",
            (paid, unpaid_next, status, cc_statement_id),
        )
        conn.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CC_PAYMENT_CONFIRMED', 'cc_statements', ?, ?)",
            (cc_statement_id, f"paid {php(paid)}, carried over {php(unpaid_next)} (from the next statement)"),
        )
        conn.commit()
    return paid, status


def get_default_statement_month(card_id: int) -> tuple[int, int]:
    """(year, month) to pre-select for this card's NEXT statement: the month after the latest
    statement month already recorded (a statement with no explicit month counts as its due date's
    month), rolling December over to January of the following year automatically. A card with no
    statements yet starts at the current month."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT MAX(COALESCE(statement_month, SUBSTR(due_date, 1, 7))) FROM cc_statements WHERE card_id = ?", (card_id,)
        ).fetchone()
    if not row or not row[0]:
        today = date.today()
        return today.year, today.month
    year, month = int(row[0][:4]), int(row[0][5:7])
    return (year + 1, 1) if month == 12 else (year, month + 1)


def add_cc_statement(
    card_id: int, due_date: date, previous_balance: float, new_charges: float,
    interest_charged: float, amount_paid: float, statement_month: str | None = None, paid_confirmed: int = 1,
) -> int:
    """Records a CC statement with a real rolling balance (migration 027, 2026-09-15):
    total_amount_due is computed, not typed in, as previous_balance + new_charges +
    interest_charged; whatever amount_paid doesn't cover becomes unpaid_balance_next, which
    the next statement's previous_balance auto-fills from (get_last_cc_statement_unpaid_balance).
    Status reflects the same computation up front, then stays subject to whatever settles it
    later (an edit to the statement, etc.) — same as every other status field in this app."""
    total_amount_due = round(previous_balance + new_charges + interest_charged, 2)
    unpaid_balance_next = round(total_amount_due - amount_paid, 2)
    if unpaid_balance_next <= 0.005:
        status = "Paid"
    elif amount_paid > 0.005:
        status = "Partially Paid"
    else:
        status = "Unpaid"
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO cc_statements
               (card_id, statement_month, due_date, total_amount_due, previous_balance, new_charges, interest_charged,
                amount_paid, unpaid_balance_next, status, paid_confirmed)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (card_id, statement_month, str(due_date), total_amount_due, previous_balance, new_charges, interest_charged,
             amount_paid, unpaid_balance_next, status, int(paid_confirmed)),
        )
        conn.commit()
        return cur.lastrowid


def _is_latest_cc_statement(cur: sqlite3.Cursor, cc_statement_id: int, card_id: int) -> bool:
    """A statement's unpaid_balance_next auto-fills the NEXT statement's previous_balance
    (get_last_cc_statement_unpaid_balance) -- editing or deleting anything but the latest one for
    its card would leave that later statement's previous_balance chained to a now-stale number.
    Restricting edit/delete to the latest keeps the rolling balance always consistent."""
    latest_id = cur.execute(
        "SELECT cc_statement_id FROM cc_statements WHERE card_id = ? ORDER BY due_date DESC, cc_statement_id DESC LIMIT 1",
        (card_id,),
    ).fetchone()[0]
    return latest_id == cc_statement_id


def edit_cc_statement(
    cc_statement_id: int, due_date: date, previous_balance: float, new_charges: float,
    interest_charged: float, amount_paid: float, statement_month: str | None = None,
) -> tuple[bool, str]:
    """In-place edit of a CC statement's rolling-balance figures -- only allowed on the latest
    statement for its card (see _is_latest_cc_statement), same recompute as add_cc_statement()."""
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute("SELECT card_id FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,)).fetchone()
        if not row:
            return False, "Statement not found."
        card_id = row[0]
        if not _is_latest_cc_statement(cur, cc_statement_id, card_id):
            return False, "Only the most recent statement for a card can be edited — a later statement already carried its balance forward."
        total_amount_due = round(previous_balance + new_charges + interest_charged, 2)
        unpaid_balance_next = round(total_amount_due - amount_paid, 2)
        if unpaid_balance_next <= 0.005:
            status = "Paid"
        elif amount_paid > 0.005:
            status = "Partially Paid"
        else:
            status = "Unpaid"
        cur.execute(
            """UPDATE cc_statements SET due_date = ?, total_amount_due = ?, previous_balance = ?, new_charges = ?,
                      interest_charged = ?, amount_paid = ?, unpaid_balance_next = ?, status = ?, paid_confirmed = 1,
                      statement_month = COALESCE(?, statement_month)
               WHERE cc_statement_id = ?""",
            (str(due_date), total_amount_due, previous_balance, new_charges, interest_charged,
             amount_paid, unpaid_balance_next, status, statement_month, cc_statement_id),
        )
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CC_STATEMENT_EDITED', 'cc_statements', ?, ?)",
            (cc_statement_id, f"due {due_date}, total {php(total_amount_due)}"),
        )
        conn.commit()
    return True, "Statement updated."


def delete_cc_statement(cc_statement_id: int) -> tuple[bool, str]:
    """Removes a CC statement entirely -- only allowed on the latest statement for its card, same
    guard as edit_cc_statement(), so a mistakenly-added statement can be undone cleanly."""
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute(
            "SELECT card_id, due_date, total_amount_due FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,)
        ).fetchone()
        if not row:
            return False, "Statement not found."
        card_id, due_date, total_amount_due = row
        if not _is_latest_cc_statement(cur, cc_statement_id, card_id):
            return False, "Only the most recent statement for a card can be deleted — a later statement already carried its balance forward."
        cur.execute("DELETE FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CC_STATEMENT_DELETED', 'cc_statements', ?, ?)",
            (cc_statement_id, f"Deleted statement due {due_date} ({php(total_amount_due)})"),
        )
        conn.commit()
    return True, "Statement deleted."
