"""Credit cards: cardholders, cards and card statements.

Moved out of app.py unchanged (2026-09-27) so the rules can be read and tested without the screens; app.py imports
every name back, so app.<name> keeps working."""
from __future__ import annotations

import sqlite3

from datetime import date
from engines.validators import money_amount_error
from engines.common import get_conn, php, q_live
from engines.clock import manila_today
from engines.common import us_date


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
            "UPDATE cc_statements SET amount_paid = ?, unpaid_balance_next = ?, status = ?, paid_confirmed = 1, soa_confirmed_at = CURRENT_TIMESTAMP WHERE cc_statement_id = ?",
            (paid, unpaid_next, status, cc_statement_id),
        )
        conn.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CC_PAYMENT_CONFIRMED', 'cc_statements', ?, ?)",
            (cc_statement_id, f"paid {php(paid)}, carried over {php(unpaid_next)} (from the next statement)"),
        )
        # Two ways a card payment is known (owner, 2026-10-09): the owner's -- from the next SOA (this function) -- and the co-owners' daily one -- each payment
        # recorded on Daily payables (cc_payments). The SOA is the final word, like the bank statement for checks; a difference goes on the Review list.
        recorded = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM cc_payments WHERE cc_statement_id = ?", (cc_statement_id,)).fetchone()
        if recorded[0] and abs(float(recorded[1]) - paid) > 0.005:
            from engines.review_flags import flag
            card = conn.execute("SELECT cc.card_name || ' - ' || cc.card_number_last4, cs.due_date FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id "
                                "WHERE cs.cc_statement_id = ?", (cc_statement_id,)).fetchone()
            flag(conn, "CC_PAYMENT_MISMATCH", "cc_statements", cc_statement_id,
                 f"{card[0]} statement due {us_date(card[1])}: the next SOA shows {php(paid)} paid, but {php(float(recorded[1]))} was recorded "
                 f"on Daily payables ({recorded[0]} payment(s)). The SOA figure is kept -- find out which is right.")
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
        today = manila_today()
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
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CC_STATEMENT_EDITED', 'cc_statements', ?, ?)",
            (cc_statement_id, f"due {due_date}, total {php(total_amount_due)}"),
        )
        conn.commit()
    return True, "Statement updated."


def correct_cc_statement(cc_statement_id: int, statement_month: str | None, due_date, total_due=None, interest=None) -> tuple[bool, str]:
    """A typing error on a recorded statement put right (owner, 2026-10-13: "why i cant edit this ... for example error").
    Any statement: its month and its due date, as long as the due date stays between the statements before and after it. The LATEST statement
    of a card only: also its total and its finance charge -- what it carried over from the earlier statement stays, the new charges are worked
    out again, and payments already recorded against it stay. Older amounts stay locked: a later statement was built on them."""
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute("""SELECT card_id, statement_month, due_date, total_amount_due, previous_balance, interest_charged, amount_paid
                             FROM cc_statements WHERE cc_statement_id = ? AND status != 'Cancelled'""", (int(cc_statement_id),)).fetchone()
        if not row:
            return False, "Statement not found."
        card_id, old_month, old_due, old_total, previous, old_interest, paid = row
        if not due_date:
            return False, "Give the due date."
        due = str(due_date)[:10]
        before = cur.execute("SELECT MAX(due_date) FROM cc_statements WHERE card_id = ? AND status != 'Cancelled' AND cc_statement_id != ? AND due_date < ?",
                             (card_id, int(cc_statement_id), str(old_due)[:10])).fetchone()[0]
        after = cur.execute("SELECT MIN(due_date) FROM cc_statements WHERE card_id = ? AND status != 'Cancelled' AND cc_statement_id != ? AND due_date > ?",
                            (card_id, int(cc_statement_id), str(old_due)[:10])).fetchone()[0]
        if (before and due <= str(before)[:10]) or (after and due >= str(after)[:10]):
            return False, "That due date would put this statement before or after its neighbours. Keep it between the statement before and the one after."
        month = (statement_month or "").strip()[:7] or old_month
        latest = _is_latest_cc_statement(cur, int(cc_statement_id), card_id)
        total, fee = float(old_total), float(old_interest or 0)
        if total_due is not None or interest is not None:
            new_total = float(old_total if total_due is None else total_due)
            new_fee = float(old_interest or 0) if interest is None else float(interest or 0)
            if abs(new_total - total) > 0.004 or abs(new_fee - fee) > 0.004:
                if not latest:
                    return False, "Only the newest statement of a card can have its amounts changed: a later statement was built on this one. Its month and due date can be corrected."
                if new_total <= 0:
                    return False, "Type the total amount due from the statement."
                if float(paid or 0) - new_total > 0.005:
                    return False, f"{php(paid)} is already recorded as paid on this statement, more than the new total. Take the payment back first."
                if new_total - float(previous or 0) - new_fee < -1.0:
                    return False, (f"These don't add up: {php(previous)} carried over plus the finance charge {php(new_fee)} is more than the total {php(new_total)}.")
                total, fee = round(new_total, 2), round(new_fee, 2)
        new_charges = max(round(total - float(previous or 0) - fee, 2), 0.0)
        left = round(total - float(paid or 0), 2)
        status = "Paid" if left <= 0.005 else ("Partially Paid" if float(paid or 0) > 0.005 else "Unpaid")
        cur.execute("""UPDATE cc_statements SET statement_month = ?, due_date = ?, total_amount_due = ?, new_charges = ?, interest_charged = ?,
                              unpaid_balance_next = ?, status = ? WHERE cc_statement_id = ?""", (month, due, total, new_charges, fee, max(left, 0.0), status, int(cc_statement_id)))
        was = f"month {old_month or 'none'}, due {str(old_due)[:10]}, total {php(old_total)}, finance charge {php(old_interest or 0)}"
        now = f"month {month or 'none'}, due {due}, total {php(total)}, finance charge {php(fee)}"
        if was == now:
            return False, "Nothing was changed."
        cur.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values, new_values) VALUES (acting_user(), 'CC_STATEMENT_CORRECTED', 'cc_statements', ?, ?, ?)",
                    (int(cc_statement_id), was, now))
        conn.commit()
    return True, "Statement corrected."


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
        paid = cur.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM cc_payments WHERE cc_statement_id = ?", (cc_statement_id,)).fetchone()
        if paid[0]:                                             # owner, 2026-10-10: deleting such a statement crashed (the payment still points at it)
            return False, (f"This statement has {paid[0]} payment(s) recorded on Daily payables ({php(paid[1])}). Take the payment back first "
                           "(Daily payables → Ticked → Undo), then delete the statement.")
        cur.execute("DELETE FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CC_STATEMENT_DELETED', 'cc_statements', ?, ?)",
            (cc_statement_id, f"Deleted statement due {due_date} ({php(total_amount_due)})"),
        )
        conn.commit()
    return True, "Statement deleted."


def add_statement_from_soa(card_id: int, statement_month: str, due_date: date, total_due: float, payments_since: float | None,
                           interest: float = 0.0) -> tuple[bool, str, int | None]:
    """The owner's way of recording a card (2026-10-09, the Credit cards screen's arithmetic moved here for the web page): a new SOA's total, the payments it
    shows since the earlier statement, and its finance charge. What the earlier statement carried over = earlier total - payments; the new charges are what
    is left of the total. The earlier statement, if not confirmed yet, is confirmed from this SOA (confirm_cc_payment_from_next_statement -- which flags a
    difference with the payments recorded day by day). Returns (ok, message, new statement id)."""
    if total_due is None or total_due <= 0:
        return False, "Type the total amount due from the statement.", None
    with get_conn() as conn:
        earlier = conn.execute("""SELECT cc_statement_id, total_amount_due, paid_confirmed, due_date FROM cc_statements
                                  WHERE card_id = ? AND status != 'Cancelled' ORDER BY due_date DESC, cc_statement_id DESC LIMIT 1""", (card_id,)).fetchone()
    if earlier and str(earlier[3])[:10] >= str(due_date):
        earlier = None                                   # a statement dated before the latest one on file: no chaining
    interest = float(interest or 0)
    if earlier and payments_since is not None:
        carried = max(float(earlier[1]) - float(payments_since), 0.0)
        new_charges = round(total_due - carried - interest, 2)
        if new_charges < -1.0:
            return False, (f"These don't add up: earlier statement {php(earlier[1])} - payments {php(payments_since)} + interest {php(interest)} is already "
                           f"{php(carried + interest)}, more than the total due {php(total_due)}. Check the payments, or the earlier statement's amount."), None
        if new_charges < 0:                              # a few centavos of rounding on the bank's side
            carried, new_charges = max(total_due - interest, 0.0), 0.0
        previous = round(carried, 2)
    else:
        previous, new_charges = 0.0, max(round(total_due - interest, 2), 0.0)
    new_id = add_cc_statement(card_id, due_date, previous, new_charges, interest, 0.0, statement_month=statement_month, paid_confirmed=0)
    msg = f"Statement recorded: {php(total_due)} due {due_date:%m/%d/%Y}."
    if earlier and payments_since is not None and int(earlier[2]) == 0:
        paid, status = confirm_cc_payment_from_next_statement(int(earlier[0]), previous)
        msg += f" The earlier statement is confirmed: {php(paid)} paid ({status})."
    return True, msg, new_id
