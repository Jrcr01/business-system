"""Guarantee checks (owner, 2026-10-01: "check Luz 2M ... usually no date, guarantee checks, later cancelled or withdrawn once we settled the loan").

A check given to a lender as security. It is NOT a payment: it lives in its own table, never in checks_issued, so it stays out of payables, the payment
schedule and the cash forecast. What it IS: an accounted-for leaf in its booklet, a contingent exposure (what the lender could present), and something to
close when the loan is settled. If the bank ever pays one, bank_statement_service flags the line and the guarantee turns 'Presented'."""
from __future__ import annotations

import os
import sqlite3
from datetime import date

import pandas as pd

from engines.check_engine import DEFAULT_BRANCH_CODE
from engines.check_rules import _is_check_number_available
from engines.common import PRIMARY_BANK_ACCOUNT_ID, php

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

COLUMNS = ["check_number", "holder", "loan_note", "face_amount", "given_on", "status", "returned_on", "note", "return_note", "presented_on", "guarantee_id"]


def canonical_number(text) -> str:
    """The full 10-digit form every check number is stored in (a 7-digit typing is the same leaf)."""
    t = str(text or "").strip()
    return DEFAULT_BRANCH_CODE + t.zfill(7) if t.isdigit() and len(t) <= 7 else t


def record_guarantee_check(creditor_id: int, check_number: str, face_amount: float | None = None, given_on: date | None = None,
                           loan_note_id: int | None = None, note: str = "") -> tuple[bool, str, int | None]:
    number = canonical_number(check_number)
    if not number:
        return False, "Enter the check number.", None
    if not number.isdigit():
        return False, "A check number is digits only.", None
    if face_amount is not None and float(face_amount) < 0:
        return False, "The face amount can't be negative.", None
    if given_on is not None and given_on > date.today():
        return False, "The date given can't be in the future.", None
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        holder = cur.execute("SELECT creditor_name FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone()
        if not holder:
            return False, "Holder (lender) not found.", None
        if loan_note_id is not None:
            ln = cur.execute("SELECT creditor_id, note_ref FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,)).fetchone()
            if not ln or ln[0] != creditor_id:
                return False, "That loan note does not belong to this lender.", None
        if not _is_check_number_available(cur, PRIMARY_BANK_ACCOUNT_ID, number):
            return False, f"Check number {number} is already on file (as a check, a spoiled leaf or another guarantee check).", None
        cur.execute("""INSERT INTO guarantee_checks (bank_account_id, check_number, creditor_id, loan_note_id, face_amount, given_on, note)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (PRIMARY_BANK_ACCOUNT_ID, number, creditor_id, loan_note_id, None if face_amount in (None, "") else float(face_amount),
                     given_on.isoformat() if given_on else None, (note or "").strip() or None))
        gid = cur.lastrowid
        cur.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('GUARANTEE_CHECK_RECORDED', 'guarantee_checks', ?, ?)",
                    (gid, f"Guarantee check {number} held by {holder[0]}" + (f" for {php(float(face_amount))}" if face_amount not in (None, "") else " (amount not stated)")
                     + (f" — {note.strip()}" if (note or "").strip() else "")))
    return True, f"Guarantee check {number} recorded: held by {holder[0]}.", gid


def return_guarantee_check(guarantee_id: int, returned_on: date, note: str = "") -> tuple[bool, str]:
    """The loan is settled and the check was taken back / cancelled."""
    if returned_on > date.today():
        return False, "The date returned can't be in the future."
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT check_number, status FROM guarantee_checks WHERE guarantee_id = ?", (guarantee_id,)).fetchone()
        if not row:
            return False, "Guarantee check not found."
        number, status = row
        if status == "Returned":
            return False, f"Check {number} is already marked returned."
        if status == "Presented":
            return False, f"The bank already paid / received check {number}, so it can't be marked returned. Look into the bank line."
        conn.execute("UPDATE guarantee_checks SET status = 'Returned', returned_on = ?, return_note = ? WHERE guarantee_id = ?",
                     (returned_on.isoformat(), (note or "").strip() or None, guarantee_id))
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('GUARANTEE_CHECK_RETURNED', 'guarantee_checks', ?, ?)",
                     (guarantee_id, f"Guarantee check {number} returned / cancelled on {returned_on}" + (f": {note.strip()}" if (note or "").strip() else "")))
    return True, f"Check {number} is marked returned."


def undo_return_guarantee(guarantee_id: int) -> tuple[bool, str]:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT check_number, status FROM guarantee_checks WHERE guarantee_id = ?", (guarantee_id,)).fetchone()
        if not row:
            return False, "Guarantee check not found."
        if row[1] != "Returned":
            return False, f"Check {row[0]} is not marked returned."
        conn.execute("UPDATE guarantee_checks SET status = 'Held', returned_on = NULL, return_note = NULL WHERE guarantee_id = ?", (guarantee_id,))
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, old_values) VALUES ('GUARANTEE_CHECK_RETURN_UNDONE', 'guarantee_checks', ?, ?)",
                     (guarantee_id, f"Guarantee check {row[0]}: returned mark removed — held again"))
    return True, f"Check {row[0]} is held again."


def guarantee_checks(status: str | None = None) -> pd.DataFrame:
    """Every guarantee check, Held first then newest. Columns: see COLUMNS."""
    with sqlite3.connect(DB_PATH) as conn:
        df = pd.read_sql_query(
            """SELECT g.check_number, cr.creditor_name AS holder, COALESCE(ln.note_ref, '') AS loan_note, g.face_amount, g.given_on, g.status, g.returned_on,
                      COALESCE(g.note, '') AS note, COALESCE(g.return_note, '') AS return_note, g.presented_on, g.guarantee_id
               FROM guarantee_checks g JOIN creditors cr ON cr.creditor_id = g.creditor_id LEFT JOIN loan_notes ln ON ln.loan_note_id = g.loan_note_id
               WHERE (? IS NULL OR g.status = ?)
               ORDER BY CASE g.status WHEN 'Presented' THEN 0 WHEN 'Held' THEN 1 ELSE 2 END, g.guarantee_id DESC""", conn, params=(status, status))
    return df[COLUMNS] if not df.empty else pd.DataFrame(columns=COLUMNS)


def exposure() -> dict:
    """What lenders hold right now: {'held': count, 'total': face amounts stated, 'no_amount': how many have no amount stated}."""
    with sqlite3.connect(DB_PATH) as conn:
        n, total, blank = conn.execute("SELECT COUNT(*), COALESCE(SUM(face_amount), 0), COALESCE(SUM(face_amount IS NULL), 0) FROM guarantee_checks WHERE status = 'Held'").fetchone()
    return {"held": int(n), "total": float(total), "no_amount": int(blank)}


def presented_count() -> int:
    """Guarantee checks the bank has paid / received — each one needs a look."""
    with sqlite3.connect(DB_PATH) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM guarantee_checks WHERE status = 'Presented'").fetchone()[0])
