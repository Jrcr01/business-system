"""Edit the details that used to be fixed once a loan existed (owner, 2026-09-30/10-01, plan D2: "I cannot edit loan details once a loan is created").

Loan note: the loan note number, the issue date and the lender (the money terms -- principal and rate -- already have edit_loan_note_terms()).
Bank loan: bank, loan reference, account number, start / maturity / repricing dates, principal, mode of payment and the amortization flag.
Every change is checked first, written in one transaction and audited with old -> new. Nothing here touches an amount that was already paid or a check
that was already printed, with one deliberate exception: giving a loan note to a different lender moves its checks to that lender, and is refused once
the bank has cleared any of them (the bank paid the old lender)."""
from __future__ import annotations

from engines.common import get_conn
import os
import sqlite3
from datetime import date

from engines.validators import money_amount_error
from engines.common import us_date

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

_ISO = lambda v: str(v)[:10] if v not in (None, "") else None   # noqa: E731


def _audit(conn, action: str, table: str, rec_id: int, old: str, new: str) -> None:
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values, new_values) VALUES (acting_user(), ?, ?, ?, ?, ?)", (action, table, rec_id, old, new))


def _diff(pairs: list[tuple[str, object, object]]) -> list[str]:
    return [f"{label}: {'—' if old in (None, '') else old} → {'—' if new in (None, '') else new}" for label, old, new in pairs if str(old or "") != str(new or "")]


def edit_loan_note_details(loan_note_id: int, note_ref: str, issue_date: date, creditor_id: int) -> tuple[bool, str]:
    note_ref = (note_ref or "").strip()
    if not note_ref:
        return False, "The loan note number cannot be empty."
    if not isinstance(issue_date, date):
        return False, "Enter the issue date."
    with get_conn(DB_PATH) as conn:
        row = conn.execute("""SELECT ln.note_ref, ln.issue_date, ln.creditor_id, ln.status, cr.creditor_name
                              FROM loan_notes ln JOIN creditors cr ON cr.creditor_id = ln.creditor_id WHERE ln.loan_note_id = ?""", (loan_note_id,)).fetchone()
        if not row:
            return False, "Loan note not found."
        old_ref, old_issue, old_creditor, status, old_name = row
        if status == "Cancelled":
            return False, "A cancelled loan note is permanent and cannot be edited."
        if note_ref != old_ref and conn.execute("SELECT 1 FROM loan_notes WHERE note_ref = ? AND loan_note_id != ?", (note_ref, loan_note_id)).fetchone():
            return False, f"Loan note number {note_ref} is already used by another loan note."
        first_due = conn.execute("SELECT MIN(due_date) FROM loan_installments WHERE loan_note_id = ?", (loan_note_id,)).fetchone()[0]
        if first_due and str(issue_date) > str(first_due)[:10]:
            return False, f"The issue date cannot be after the first installment ({us_date(first_due)}). Change that installment first."
        new_name = old_name
        if creditor_id != old_creditor:
            c = conn.execute("SELECT creditor_type, creditor_name FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone()
            if not c:
                return False, "Lender not found."
            if c[0] != "LENDER":
                return False, f"{c[1]} is not a lender."
            cleared = conn.execute("SELECT COUNT(*) FROM checks_issued WHERE loan_note_id = ? AND bank_cleared_date IS NOT NULL", (loan_note_id,)).fetchone()[0]
            if cleared:
                return False, f"{cleared} check(s) of this loan note were already cleared by the bank to {old_name}, so the lender cannot be changed."
            new_name = c[1]
        changes = _diff([("Loan note no.", old_ref, note_ref), ("Issue date", _ISO(old_issue), str(issue_date)), ("Lender", old_name, new_name)])
        if not changes:
            return True, "No changes."
        moved = 0
        conn.execute("UPDATE loan_notes SET note_ref = ?, issue_date = ?, creditor_id = ? WHERE loan_note_id = ?", (note_ref, str(issue_date), creditor_id, loan_note_id))
        if creditor_id != old_creditor:
            moved = conn.execute("UPDATE checks_issued SET creditor_id = ? WHERE loan_note_id = ?", (creditor_id, loan_note_id)).rowcount
            conn.execute("UPDATE guarantee_checks SET creditor_id = ? WHERE loan_note_id = ?", (creditor_id, loan_note_id))
        text = "; ".join(changes) + (f" ({moved} check(s) now payable to {new_name})" if moved else "")
        _audit(conn, "LOAN_NOTE_DETAILS_EDITED", "loan_notes", loan_note_id, f"{old_ref} / {_ISO(old_issue)} / {old_name}", text)
    return True, f"Loan note {note_ref} updated: " + "; ".join(changes) + "."


def edit_bank_loan_details(bank_loan_id: int, creditor_id: int, loan_ref: str, account_number: str | None, start_date: date | None, maturity_date: date | None,
                           repricing_date: date | None, principal_amount: float | None, mode_of_payment: str, has_amortization: bool = False) -> tuple[bool, str]:
    loan_ref = (loan_ref or "").strip()
    if not loan_ref:
        return False, "The loan reference cannot be empty."
    if principal_amount is not None and principal_amount != 0:
        if (err := money_amount_error(principal_amount, "Principal amount")):
            return False, err
    else:
        principal_amount = None
    if start_date and maturity_date and maturity_date <= start_date:
        return False, "Maturity date must be after the start date."
    if start_date and repricing_date and repricing_date < start_date:
        return False, "Repricing date cannot be before the start date."
    with get_conn(DB_PATH) as conn:
        row = conn.execute("""SELECT bl.creditor_id, bl.loan_ref, bl.account_number, bl.start_date, bl.maturity_date, bl.repricing_date, bl.principal_amount,
                                     bl.mode_of_payment, bl.has_amortization, bl.is_cancelled, bl.is_beginning_balance, cr.creditor_name
                              FROM bank_loans bl JOIN creditors cr ON cr.creditor_id = bl.creditor_id WHERE bl.bank_loan_id = ?""", (bank_loan_id,)).fetchone()
        if not row:
            return False, "Bank loan not found."
        o_cred, o_ref, o_acc, o_start, o_mat, o_rep, o_prin, o_mode, o_amort, cancelled, beginning, o_bank = row
        if cancelled:
            return False, "A cancelled bank loan is permanent and cannot be edited."
        if loan_ref != o_ref and conn.execute("SELECT 1 FROM bank_loans WHERE loan_ref = ? AND bank_loan_id != ?", (loan_ref, bank_loan_id)).fetchone():
            return False, f"Loan reference {loan_ref} is already used by another bank loan."
        new_bank = o_bank
        if creditor_id != o_cred:
            c = conn.execute("SELECT creditor_type, creditor_name FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone()
            if not c:
                return False, "Bank not found."
            if c[0] != "BANK_LOAN":
                return False, f"{c[1]} is not a bank set up for loans."
            new_bank = c[1]
        account_number = (account_number or "").strip() or None
        mode_of_payment = (mode_of_payment or "").strip() or "ADA"
        s, m, r = (str(d) if d else None for d in (start_date, maturity_date, repricing_date))
        changes = _diff([("Bank", o_bank, new_bank), ("Loan ref", o_ref, loan_ref), ("Account no.", o_acc, account_number), ("Start date", _ISO(o_start), s),
                         ("Maturity date", _ISO(o_mat), m), ("Repricing date", _ISO(o_rep), r),
                         ("Principal", None if o_prin is None else f"{o_prin:,.2f}", None if principal_amount is None else f"{principal_amount:,.2f}"),
                         ("Mode of payment", o_mode, mode_of_payment), ("Amortization", "yes" if o_amort else "no", "yes" if has_amortization else "no")])
        if not changes:
            return True, "No changes."
        conn.execute("""UPDATE bank_loans SET creditor_id = ?, loan_ref = ?, account_number = ?, start_date = ?, maturity_date = ?, repricing_date = ?,
                        principal_amount = ?, mode_of_payment = ?, has_amortization = ? WHERE bank_loan_id = ?""",
                     (creditor_id, loan_ref, account_number, s, m, r, principal_amount, mode_of_payment, 1 if has_amortization else 0, bank_loan_id))
        _audit(conn, "BANK_LOAN_DETAILS_EDITED", "bank_loans", bank_loan_id, f"{o_ref} / {o_bank}", "; ".join(changes))
        if beginning and s and principal_amount is not None:               # real details entered: the "Beginning balance (from MS Access)" label has done its job
            conn.execute("UPDATE bank_loans SET is_beginning_balance = 0, beginning_label_removed = 1 WHERE bank_loan_id = ?", (bank_loan_id,))
            _audit(conn, "BEGINNING_BALANCE_LABEL_REMOVED", "bank_loans", bank_loan_id, "", f"{loan_ref}: beginning-balance label removed because its real details were entered")
    return True, f"Bank loan {loan_ref} updated: " + "; ".join(changes) + "."
