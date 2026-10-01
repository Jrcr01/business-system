"""Private-lender loan notes: create, promote, status, delete/cancel.

Moved out of app.py unchanged (2026-09-27) so the rules can be read and tested without the screens; app.py imports
every name back, so app.<name> keeps working."""
from __future__ import annotations


from datetime import date
from datetime import datetime
from engines.validators import money_amount_error
from engines.common import get_conn
from engines.statement_rules import new_draft_ref
from engines.check_rules import cancel_check


_LOAN_NOTE_STATUSES = ("Active", "Settled", "Defaulted", "Cancelled", "Pending Setup")


def create_loan_note(creditor_id: int, issue_date: date, principal_amount: float, monthly_interest_rate: float) -> tuple[bool, str, int | None]:
    """note_ref starts as a DRAFT-<timestamp> placeholder (same pattern as
    create_order_statement()) — promote_draft_loan_note() assigns the permanent "YYYY - LN
    XXXX" number once it's ready, mirroring promote_draft_statement()'s exact scheme.

    total_interest/total_payable start at 0/principal — the schedule is empty at creation
    and these are recomputed from loan_installments as rows get added (recompute_schedule()).

    No placeholder row is seeded — issue_date/principal_amount on loan_notes itself already
    serve as the day-zero anchor (suggest_installment_interest() falls back to them when the
    schedule is empty), so a visible "day-zero" row in loan_installments was never functionally
    necessary, just confusing (it displayed as a Paid installment for money going out, not a
    payment collected).

    Validates principal_amount/monthly_interest_rate itself (same convention as
    edit_loan_note_terms()) rather than trusting the caller — loan_notes.principal_amount has a
    CHECK(principal_amount > 0) constraint, and a 0-or-blank "Principal amount" widget left
    untouched used to hit that raw sqlite3.IntegrityError uncaught, all the way up to the user."""
    if (err := money_amount_error(principal_amount, "Principal amount")):
        return False, err, None
    if monthly_interest_rate < 0:
        return False, "Monthly interest rate cannot be negative.", None
    note_ref = new_draft_ref("loan_notes", "note_ref")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, monthly_interest_rate, total_interest, total_payable, status)
               VALUES (?, ?, ?, ?, ?, 0, ?, 'Active')""",
            (creditor_id, note_ref, str(issue_date), principal_amount, monthly_interest_rate, principal_amount),
        )
        loan_note_id = cur.lastrowid
        cur.execute("UPDATE loan_notes SET is_lease = 1 WHERE loan_note_id = ? AND (SELECT is_lessor FROM creditors WHERE creditor_id = ?) = 1", (loan_note_id, creditor_id))   # rent, not a loan
        conn.commit()
        return True, "Draft loan note created.", loan_note_id


def promote_draft_loan_note(loan_note_id: int) -> str:
    """Assigns a permanent "YYYY - LN XXXX" sequence number if the note is still on its
    DRAFT- placeholder ref — same numbering scheme as promote_draft_statement()."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT note_ref FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
        row = cur.fetchone()
        if not row:
            raise ValueError(f"Loan note {loan_note_id} not found.")
        current_ref = row[0]
        if not current_ref.startswith("DRAFT-"):
            return current_ref
        prefix = f"{datetime.now().year} - LN "
        # MAX sequence number in use, not "whichever row has the highest loan_note_id" -- same
        # fix as promote_draft_statement() (engines/db_engine.py): promotion order and
        # loan_note_id order aren't the same thing, so picking the highest-id row's ref could
        # yield a lower sequence number than one already assigned elsewhere, colliding on UNIQUE.
        cur.execute("SELECT note_ref FROM loan_notes WHERE note_ref LIKE ?", (f"{prefix}%",))
        existing_seqs = [int(suffix) for (ref,) in cur.fetchall() if (suffix := ref.replace(prefix, "").strip()).isdigit()]
        next_seq = max(existing_seqs) + 1 if existing_seqs else 1
        permanent_ref = f"{prefix}{next_seq:04d}"
        cur.execute("UPDATE loan_notes SET note_ref = ? WHERE loan_note_id = ?", (permanent_ref, loan_note_id))
        conn.commit()
        return permanent_ref


def update_loan_note_status(loan_note_id: int, status: str) -> tuple[bool, str]:
    """Only the UI's own constrained selectbox stopped an invalid status from ever being passed
    here -- any other caller would have hit the raw sqlite3.IntegrityError from loan_notes' own
    CHECK(status IN (...)) uncaught. Whitelisted server-side too now."""
    if status not in _LOAN_NOTE_STATUSES:
        return False, f"'{status}' isn't a valid loan note status."
    with get_conn() as conn:
        conn.execute("UPDATE loan_notes SET status = ? WHERE loan_note_id = ?", (status, loan_note_id))
        conn.commit()
    return True, "Status updated."


def delete_loan_note(loan_note_id: int) -> tuple[bool, str]:
    """Hard delete — only when nothing real has happened yet (no checks issued against it),
    same "nothing real yet = deletable" principle delete_loan_installment() already applies to
    a single Pending row. Once a check exists, use cancel_loan_note() instead (soft-cancel,
    keeps the audit trail — mirrors cancel_order_statement())."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT 1 FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
        if not cur.fetchone():
            return False, "Loan note not found."
        cur.execute("SELECT COUNT(*) FROM checks_issued WHERE loan_note_id = ?", (loan_note_id,))
        (check_count,) = cur.fetchone()
        if check_count > 0:
            return False, "This loan note has checks on record and can't be deleted — use Cancel instead."
        cur.execute("DELETE FROM loan_installments WHERE loan_note_id = ?", (loan_note_id,))
        cur.execute("DELETE FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('LOAN_NOTE_DELETED', 'loan_notes', ?, ?)",
            (loan_note_id, "Deleted loan note with no checks on record"),
        )
        conn.commit()
    return True, "Loan note deleted."


def cancel_loan_note(loan_note_id: int) -> tuple[bool, str]:
    """Cancels a loan note: every Active check issued against it gets cancelled (restores the
    bank balance, stays on record — never deleted, per the audit-trail rule), and the note
    itself is marked Cancelled permanently. Mirrors cancel_order_statement()'s exact pattern."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
        row = cur.fetchone()
        if not row:
            return False, "Loan note not found."
        if row[0] == "Cancelled":
            return False, "This loan note is already cancelled."
        cur.execute("SELECT check_id FROM checks_issued WHERE loan_note_id = ? AND status = 'Active'", (loan_note_id,))
        active_check_ids = [r[0] for r in cur.fetchall()]

    cancelled_checks = 0
    for check_id in active_check_ids:
        ok, _ = cancel_check(int(check_id))
        if ok:
            cancelled_checks += 1

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE loan_notes SET status = 'Cancelled' WHERE loan_note_id = ?", (loan_note_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('LOAN_NOTE_CANCELLED', 'loan_notes', ?, ?)",
            (loan_note_id, f"Cancelled loan note: {cancelled_checks} check(s) cancelled"),
        )
        conn.commit()
    return True, f"Loan note cancelled. {cancelled_checks} check(s) cancelled."
