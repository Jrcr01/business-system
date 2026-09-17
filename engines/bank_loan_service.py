"""Bank loan monthly dues (Phase 3).

Just Due Date + Amount per installment, per explicit user simplification -- no principal/
interest/insurance breakdown, no computation of any kind. installment_no is the one exception:
it's a display sequence number, not a financial figure, so it's kept in due-date order
automatically rather than typed in.

interest_portion (migration 028, 2026-09-15, Unified Debt Interest Tracking) is the one addition
to that simplification -- still no computation, still not required: an optional extra number,
typed straight off the bank's document exactly like Amount already is, defaulting to 0 when not
given. It exists purely so bank loan interest can feed the interest-tracking dashboard/KPI --
"for bank loan still simplified but i need to get the portion of interest so i can track
properly," explicitly not the full amortization-table-upload workflow that was first proposed.

"Paid" is not a stored field -- same standing convention as a check's "cleared" status elsewhere
in this app (CLAUDE.md): implied automatically once due_date is in the past relative to today,
computed at query time (see is_installment_cleared()/CLEARED_SQL below), never a manual toggle.

Settlement is untracked cash by default -- confirmed explicitly, not assumed. There is no
check_id/checks_issued link at all here, unlike loan_installments (Loan Notes), which links to
an issued check.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

# Same "cleared" convention as v_checks_ledger.effective_status (schema.sql) and the Loan Notes/
# Order Statement cleared-vs-issued work: a due date counts as cleared once it's in the past.
CLEARED_SQL = "due_date <= DATE('now', 'localtime')"


def _get_conn():
    return sqlite3.connect(DB_PATH)


def is_installment_cleared(due_date) -> bool:
    d = due_date if isinstance(due_date, date) else date.fromisoformat(str(due_date))
    return d <= date.today()


def _renumber_installments(cur: sqlite3.Cursor, bank_loan_id: int) -> None:
    cur.execute(
        "SELECT installment_id FROM bank_loan_installments WHERE bank_loan_id = ? ORDER BY due_date, installment_id",
        (bank_loan_id,),
    )
    for i, (installment_id,) in enumerate(cur.fetchall(), start=1):
        cur.execute("UPDATE bank_loan_installments SET installment_no = ? WHERE installment_id = ?", (i, installment_id))


def _sync_loan_status(cur: sqlite3.Cursor, bank_loan_id: int) -> None:
    """A loan auto-Closes once every installment's due date has passed (cleared), and auto-
    reopens to Active if a new future-dated row gets added after closing (e.g. a correction)."""
    cur.execute("SELECT status FROM bank_loans WHERE bank_loan_id = ?", (bank_loan_id,))
    (current_status,) = cur.fetchone()
    cur.execute(
        f"SELECT COUNT(*), SUM(CASE WHEN {CLEARED_SQL} THEN 1 ELSE 0 END) FROM bank_loan_installments WHERE bank_loan_id = ?",
        (bank_loan_id,),
    )
    total, cleared = cur.fetchone()
    cleared = cleared or 0
    if total and cleared == total and current_status == "Active":
        cur.execute("UPDATE bank_loans SET status = 'Closed' WHERE bank_loan_id = ?", (bank_loan_id,))
    elif (not total or cleared < total) and current_status == "Closed":
        cur.execute("UPDATE bank_loans SET status = 'Active' WHERE bank_loan_id = ?", (bank_loan_id,))


def create_bank_loan(
    creditor_id: int,
    loan_ref: str,
    account_number: str,
    start_date: date,
    maturity_date: date,
    repricing_date: date | None,
    principal_amount: float,
    mode_of_payment: str,
    beneficiary_id: int | None = None,
) -> int:
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO bank_loans (creditor_id, beneficiary_id, loan_ref, account_number, start_date, maturity_date, repricing_date, principal_amount, mode_of_payment, status)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active')""",
            (creditor_id, beneficiary_id, loan_ref, account_number or None, str(start_date), str(maturity_date), str(repricing_date) if repricing_date else None, principal_amount, mode_of_payment),
        )
        return cur.lastrowid


def add_loan_beneficiary(name: str, beneficiary_type: str, contact_number: str) -> int:
    if beneficiary_type not in ("Person", "Affiliate"):
        raise ValueError("beneficiary_type must be 'Person' or 'Affiliate'.")
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO loan_beneficiaries (name, beneficiary_type, contact_number) VALUES (?, ?, ?)",
            (name, beneficiary_type, contact_number or None),
        )
        return cur.lastrowid


def edit_bank_loan_beneficiary(bank_loan_id: int, beneficiary_id: int | None) -> None:
    with _get_conn() as conn:
        conn.execute("UPDATE bank_loans SET beneficiary_id = ? WHERE bank_loan_id = ?", (beneficiary_id, bank_loan_id))
        conn.commit()


def delete_bank_loan(bank_loan_id: int) -> tuple[bool, str]:
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM bank_loan_installments WHERE bank_loan_id = ?", (bank_loan_id,))
        (count,) = cur.fetchone()
        if count > 0:
            return False, "Cannot delete a bank loan that already has schedule rows entered."
        cur.execute("DELETE FROM bank_loans WHERE bank_loan_id = ?", (bank_loan_id,))
        conn.commit()
    return True, "Bank loan deleted."


def add_bank_loan_installment(bank_loan_id: int, due_date: date, amount: float, interest_portion: float = 0.0) -> tuple[bool, str]:
    if amount < 0:
        return False, "Amount cannot be negative."
    if interest_portion < 0:
        return False, "Interest portion cannot be negative."
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount, interest_portion) VALUES (?, 0, ?, ?, ?)",
            (bank_loan_id, str(due_date), amount, interest_portion),
        )
        _renumber_installments(cur, bank_loan_id)
        _sync_loan_status(cur, bank_loan_id)
        conn.commit()
    return True, "Installment added."


def edit_bank_loan_installment(installment_id: int, due_date: date, amount: float, interest_portion: float = 0.0) -> tuple[bool, str]:
    if amount < 0:
        return False, "Amount cannot be negative."
    if interest_portion < 0:
        return False, "Interest portion cannot be negative."
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT bank_loan_id FROM bank_loan_installments WHERE installment_id = ?", (installment_id,))
        row = cur.fetchone()
        if not row:
            return False, "Installment not found."
        (bank_loan_id,) = row
        cur.execute(
            "UPDATE bank_loan_installments SET due_date = ?, amount = ?, interest_portion = ? WHERE installment_id = ?",
            (str(due_date), amount, interest_portion, installment_id),
        )
        _renumber_installments(cur, bank_loan_id)
        _sync_loan_status(cur, bank_loan_id)
        conn.commit()
    return True, "Installment updated."


def delete_bank_loan_installment(installment_id: int) -> tuple[bool, str]:
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT bank_loan_id, due_date FROM bank_loan_installments WHERE installment_id = ?", (installment_id,))
        row = cur.fetchone()
        if not row:
            return False, "Installment not found."
        bank_loan_id, due_date = row
        if is_installment_cleared(due_date):
            return False, "Cannot delete an installment whose due date has already passed — it's treated as paid."
        cur.execute("DELETE FROM bank_loan_installments WHERE installment_id = ?", (installment_id,))
        _renumber_installments(cur, bank_loan_id)
        _sync_loan_status(cur, bank_loan_id)
        conn.commit()
    return True, "Installment deleted."
