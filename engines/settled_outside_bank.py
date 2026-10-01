"""Checks settled outside the bank (owner, 2026-09-30: "checks not yet cleared -- have an option to solve, like when the check is encashed to us
instead of the bank").

A check the payee gave back, and was paid in cash (or exchanged) instead, will never clear the bank. It stays on file and keeps counting toward its
batch / loan note (the supplier IS paid), but it stops being money still leaving the bank: the not-yet-cleared list, cash forecast, float,
stale-check count and payment schedule all skip a check with settled_outside_at filled in. It is never marked cleared by the bank -- the bank
statement stays the truth, and if the bank pays it anyway the list shows it so the owner can see it was paid twice."""
from __future__ import annotations

import os
import sqlite3
from datetime import date

import pandas as pd

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

REASONS = ["Paid in cash", "Check returned and exchanged", "Other"]
COLUMNS = ["check_number", "check_date", "amount", "payee", "reference", "settled_on", "reason", "note", "bank_also_cleared", "check_id"]


def settle_outside_bank(check_id: int, reason: str, settled_on: date, note: str = "") -> tuple[bool, str]:
    """Mark an Active, numbered-or-not check that the bank has NOT cleared as settled outside the bank on `settled_on`."""
    if reason not in REASONS:
        return False, "Pick a reason: " + ", ".join(REASONS) + "."
    if settled_on > date.today():
        return False, "The settlement date can't be in the future."
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT check_number, amount, status, bank_cleared_date, settled_outside_at FROM checks_issued WHERE check_id = ?", (int(check_id),)).fetchone()
        if not row:
            return False, "Check not found."
        number, amount, status, bank_cleared, already = row
        if status != "Active":
            return False, f"Only an Active check can be settled outside the bank (this one is {status})."
        if already:
            return False, f"Check {number or check_id} is already marked settled outside the bank ({already})."
        if bank_cleared:
            return False, f"The bank already cleared check {number or check_id} on {bank_cleared} — nothing to settle outside the bank."
        conn.execute("UPDATE checks_issued SET settled_outside_at = ?, settled_outside_reason = ?, settled_outside_note = ? WHERE check_id = ?",
                     (settled_on.isoformat(), reason, (note or "").strip() or None, int(check_id)))
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CHECK_SETTLED_OUTSIDE', 'checks_issued', ?, ?)",
                     (int(check_id), f"Check {number or check_id} (PHP {float(amount):,.2f}) settled outside the bank on {settled_on}: {reason}" + (f" — {note.strip()}" if (note or "").strip() else "")))
    return True, f"Check {number or check_id} is settled outside the bank. It no longer counts as money still to leave the bank."


def undo_settlement(check_id: int) -> tuple[bool, str]:
    """Take the mark off again (settled by mistake): the check goes back to waiting for the bank."""
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT check_number, settled_outside_at FROM checks_issued WHERE check_id = ?", (int(check_id),)).fetchone()
        if not row:
            return False, "Check not found."
        if not row[1]:
            return False, "This check is not marked as settled outside the bank."
        conn.execute("UPDATE checks_issued SET settled_outside_at = NULL, settled_outside_reason = NULL, settled_outside_note = NULL WHERE check_id = ?", (int(check_id),))
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, old_values) VALUES ('CHECK_SETTLEMENT_UNDONE', 'checks_issued', ?, ?)",
                     (int(check_id), f"Check {row[0] or check_id}: settled-outside mark ({row[1]}) removed — waiting for the bank again"))
    return True, f"Check {row[0] or check_id} is waiting for the bank again."


def settled_checks() -> pd.DataFrame:
    """Every check settled outside the bank, newest first. 'bank_also_cleared' is filled when the bank paid it as well (paid twice -- look into it)."""
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT c.check_number, c.check_date, c.amount, cr.creditor_name AS payee, COALESCE(os.statement_ref, ln.note_ref) AS reference,
                      c.settled_outside_at AS settled_on, c.settled_outside_reason AS reason, COALESCE(c.settled_outside_note, '') AS note,
                      CASE WHEN c.bank_cleared_date IS NULL THEN '' ELSE 'Yes - bank cleared it ' || c.bank_cleared_date || ' too' END AS bank_also_cleared,
                      c.check_id
               FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
               LEFT JOIN order_statements os ON os.statement_id = c.statement_id LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
               WHERE c.settled_outside_at IS NOT NULL ORDER BY c.settled_outside_at DESC, c.check_id DESC""", conn)
    return df[COLUMNS] if not df.empty else pd.DataFrame(columns=COLUMNS)
