"""Credit card payments (2026-09-21): record when and how much you actually paid on a card statement.

A recorded payment reduces what that statement still owes right away (amount_paid, unpaid_balance_next
and status are updated the same way the statement form does). The NEXT statement's carried-over
balance is still the final word -- see app.confirm_cc_payment_from_next_statement -- and any
difference from what was recorded is reported to the user.
"""
import os
import sqlite3

import pandas as pd

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")
METHODS = ["Cash / Online", "Direct Debit", "Check", "Other"]


def _conn():
    return sqlite3.connect(DB_PATH)


def _status(total: float, paid: float) -> str:
    unpaid = round(total - paid, 2)
    return "Paid" if unpaid <= 0.005 else ("Partially Paid" if paid > 0.005 else "Unpaid")


def statements_owing(card_id: int) -> pd.DataFrame:
    """The card's statements that still owe something, oldest first."""
    with _conn() as conn:
        return pd.read_sql_query(
            """SELECT cc_statement_id, due_date, total_amount_due, amount_paid, paid_confirmed,
                      ROUND(total_amount_due - amount_paid, 2) AS owed
               FROM cc_statements WHERE card_id = ? AND status NOT IN ('Paid', 'Cancelled') AND total_amount_due - amount_paid > 0.005
               ORDER BY due_date, cc_statement_id""", conn, params=(card_id,))


def list_payments(card_id: int) -> pd.DataFrame:
    with _conn() as conn:
        return pd.read_sql_query(
            """SELECT p.payment_id, p.payment_date, p.amount, p.method, p.note, s.due_date AS statement_due
               FROM cc_payments p JOIN cc_statements s ON s.cc_statement_id = p.cc_statement_id
               WHERE p.card_id = ? ORDER BY p.payment_date DESC, p.payment_id DESC""", conn, params=(card_id,))


def recorded_total(statement_id: int) -> float:
    with _conn() as conn:
        return float(conn.execute("SELECT COALESCE(SUM(amount), 0) FROM cc_payments WHERE cc_statement_id = ?", (statement_id,)).fetchone()[0])


def record_payment(statement_id: int, payment_date, amount: float, method: str = "Cash / Online", note: str = "") -> tuple[bool, str]:
    if amount is None or amount <= 0:
        return False, "The payment amount must be more than zero."
    with _conn() as conn:
        row = conn.execute("SELECT card_id, total_amount_due, amount_paid, status FROM cc_statements WHERE cc_statement_id = ?", (statement_id,)).fetchone()
        if not row:
            return False, "Statement not found."
        card_id, total, paid, status = row
        if status == "Cancelled":
            return False, "That statement is cancelled."
        owed = round(total - paid, 2)
        if amount > owed + 0.005:
            return False, f"That is more than the statement still owes (PHP {owed:,.2f}). Record the extra against a later statement."
        new_paid = round(paid + amount, 2)
        conn.execute(
            "UPDATE cc_statements SET amount_paid = ?, unpaid_balance_next = ?, status = ?, payment_date = ? WHERE cc_statement_id = ?",
            (new_paid, round(total - new_paid, 2), _status(total, new_paid), str(payment_date), statement_id),
        )
        conn.execute(
            "INSERT INTO cc_payments (cc_statement_id, card_id, payment_date, amount, method, note) VALUES (?, ?, ?, ?, ?, ?)",
            (statement_id, card_id, str(payment_date), float(amount), method, (note or "").strip() or None),
        )
        conn.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CC_PAYMENT_RECORDED', 'cc_statements', ?, ?)",
            (statement_id, f"PHP {amount:,.2f} on {payment_date} ({method})"),
        )
        conn.commit()
    return True, f"Payment of PHP {amount:,.2f} recorded."


def delete_payment(payment_id: int) -> tuple[bool, str]:
    """Undo a recorded payment -- only while its statement is still the card's latest (a later
    statement has otherwise already taken over its balance)."""
    with _conn() as conn:
        p = conn.execute("SELECT cc_statement_id, card_id, amount FROM cc_payments WHERE payment_id = ?", (payment_id,)).fetchone()
        if not p:
            return False, "Payment not found."
        statement_id, card_id, amount = p
        newer = conn.execute(
            """SELECT 1 FROM cc_statements n JOIN cc_statements s ON s.cc_statement_id = ? WHERE n.card_id = s.card_id AND n.status != 'Cancelled'
               AND (n.due_date > s.due_date OR (n.due_date = s.due_date AND n.cc_statement_id > s.cc_statement_id)) LIMIT 1""", (statement_id,)).fetchone()
        if newer:
            return False, "A later statement already exists for this card, so this payment can no longer be undone here."
        total, paid = conn.execute("SELECT total_amount_due, amount_paid FROM cc_statements WHERE cc_statement_id = ?", (statement_id,)).fetchone()
        new_paid = max(round(paid - amount, 2), 0.0)
        conn.execute("UPDATE cc_statements SET amount_paid = ?, unpaid_balance_next = ?, status = ? WHERE cc_statement_id = ?",
                     (new_paid, round(total - new_paid, 2), _status(total, new_paid), statement_id))
        conn.execute("DELETE FROM cc_payments WHERE payment_id = ?", (payment_id,))
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CC_PAYMENT_UNDONE', 'cc_statements', ?, ?)",
                     (statement_id, f"PHP {amount:,.2f} removed"))
        conn.commit()
    return True, "Payment undone."
