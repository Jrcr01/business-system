"""Private lender loan note repayment schedules.

Schedule rows are entered progressively — due date + principal decided as real payment
commitments are made, not fully planned at loan signing (per how these loans are actually
negotiated: dates get fixed first, interest is computed to compensate for the actual gap).
Interest is a *suggested* value (rate x outstanding balance x days-since-previous-row/30),
never a locked formula — it stays freely editable per row for lender-specific conventions
(some lenders accept flat monthly division instead of day-proration for a clean ~30-day span).

Outstanding balance and loan-note totals (total_interest/total_payable) are always fully
recomputed from the schedule after any change, the same "never let a denormalized total drift"
principle order_statements.total_amount already follows via recompute_statement_total().
"""
from __future__ import annotations

import os
import sqlite3
from engines.validators import money_amount_error
from datetime import date
from engines.common import get_conn

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def _get_conn():
    return get_conn(DB_PATH)


def suggest_installment_interest(loan_note_id: int, due_date: date) -> tuple[float, float]:
    """Returns (suggested_interest, balance_before) for a new row landing on due_date, based
    on the current last row (by due date) or the loan's principal/issue date if the schedule
    is still empty. Purely a suggestion for the UI to pre-fill — never enforced."""
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT principal_amount, monthly_interest_rate, issue_date FROM loan_notes WHERE loan_note_id = ?",
            (loan_note_id,),
        )
        row = cur.fetchone()
        if not row:
            return 0.0, 0.0
        principal_amount, rate, issue_date_s = row
        cur.execute(
            """SELECT due_date, outstanding_balance FROM loan_installments
               WHERE loan_note_id = ? ORDER BY due_date DESC, installment_id DESC LIMIT 1""",
            (loan_note_id,),
        )
        last = cur.fetchone()

    if last:
        prev_date_s, balance_before = last
    else:
        prev_date_s, balance_before = issue_date_s, principal_amount
    # A "Pending Setup" lender (Phase 8a placeholder) has no principal/rate on file yet --
    # treat as 0 rather than crash, since this expander renders before the user has had a
    # chance to fill in real terms via "Edit loan note terms".
    balance_before = balance_before or 0.0
    rate = rate or 0.0
    prev_date = date.fromisoformat(prev_date_s) if isinstance(prev_date_s, str) else prev_date_s
    days_elapsed = max((due_date - prev_date).days, 0)
    suggested_interest = round(balance_before * (rate / 100.0) * (days_elapsed / 30.0), 2)
    return suggested_interest, balance_before




def _recompute_schedule(cur: sqlite3.Cursor, loan_note_id: int) -> None:
    """Recomputes every row's installment_no + outstanding_balance in due-date order, then the
    loan note's total_interest/total_payable, and auto-settles the note once Outstanding
    (uncleared) actually hits zero — not merely once every installment has a check issued
    against it. A postdated check hasn't actually left the bank yet, so "every installment has
    an Active check" alone doesn't mean the loan is truly settled; only once every issued check
    has also passed its date (same 'cleared' convention as v_checks_ledger.effective_status,
    schema.sql: check_date before today -- due today = still owed) does the real cash position reach zero. Called from within
    an already-open connection/cursor so it composes with the caller's own transaction
    (installment add/edit/delete, and check issue/cancel/replace touching a linked installment)."""
    cur.execute("SELECT principal_amount FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
    (principal_amount,) = cur.fetchone()
    if principal_amount is None:
        # A "Pending Setup" placeholder note migrated with no real principal yet (Phase 8a) --
        # used to crash here with a raw TypeError (None - float) the moment an installment was
        # added before its terms were filled in. edit_loan_note_terms() is what's meant to
        # graduate a note out of this state; installments come after that, not before.
        raise _ScheduleError("This loan note has no principal amount on file yet. Set its terms first, under \"Edit loan note terms,\" before adding installments.")
    cur.execute(
        "SELECT installment_id, principal, interest, status FROM loan_installments WHERE loan_note_id = ? ORDER BY due_date, installment_id",
        (loan_note_id,),
    )
    rows = cur.fetchall()

    balance = principal_amount
    total_interest = 0.0
    for i, (installment_id, principal, interest, status) in enumerate(rows, start=1):
        balance = round(balance - principal, 2)
        total_interest += interest
        cur.execute(
            "UPDATE loan_installments SET installment_no = ?, outstanding_balance = ? WHERE installment_id = ?",
            (i, balance, installment_id),
        )

    total_payable = round(principal_amount + total_interest, 2)
    cur.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM checks_issued WHERE loan_note_id = ? AND status = 'Active' AND check_date < manila_today()",
        (loan_note_id,),
    )
    (cleared_sum,) = cur.fetchone()
    outstanding_uncleared = round(total_payable - cleared_sum, 2)

    cur.execute("SELECT status FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
    (current_status,) = cur.fetchone()
    if current_status == "Active" and bool(rows) and outstanding_uncleared <= 0.005:
        cur.execute(
            "UPDATE loan_notes SET total_interest = ?, total_payable = ?, status = 'Settled' WHERE loan_note_id = ?",
            (round(total_interest, 2), total_payable, loan_note_id),
        )
    else:
        cur.execute(
            "UPDATE loan_notes SET total_interest = ?, total_payable = ? WHERE loan_note_id = ?",
            (round(total_interest, 2), total_payable, loan_note_id),
        )


class _ScheduleError(Exception):
    """Raised inside a with-connection block to force a rollback (a plain `return` from inside
    a `with conn:` block still commits whatever ran before it — only a raised exception rolls
    back), then caught just outside to turn into the normal (False, message) return."""


def _assert_no_negative_balance(cur: sqlite3.Cursor, loan_note_id: int) -> None:
    """Hard rule: total scheduled principal can never exceed the loan's principal_amount —
    that would drive the outstanding balance negative, which is never valid."""
    cur.execute("SELECT MIN(outstanding_balance) FROM loan_installments WHERE loan_note_id = ?", (loan_note_id,))
    (min_balance,) = cur.fetchone()
    if min_balance is not None and min_balance < -0.005:
        raise _ScheduleError(
            f"This over-commits principal beyond the loan amount (outstanding balance would go to {min_balance:,.2f}). Reduce this row's principal."
        )


def preview_schedule(loan_note_id: int, first_due: date, installments: int) -> list[dict]:
    """A monthly schedule to look at before it is saved: the principal in equal parts (the last one takes the odd centavos), the interest each month = the monthly rate x the balance
    still owed, one month apart from `first_due`. Rows: due_date, principal, interest, total, balance after."""
    from engines.common import add_months
    with _get_conn() as conn:
        row = conn.execute("SELECT principal_amount, monthly_interest_rate FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,)).fetchone()
    if not row or not row[0] or installments < 1:
        return []
    principal, rate = float(row[0]), float(row[1] or 0)
    part = round(principal / installments, 2)
    out, balance = [], principal
    for i in range(installments):
        pay = round(balance, 2) if i == installments - 1 else part
        interest = round(balance * rate / 100, 2)
        balance = round(balance - pay, 2)
        out.append({"due_date": add_months(first_due, i), "principal": pay, "interest": interest, "total": round(pay + interest, 2), "balance": balance})
    return out


def generate_schedule(loan_note_id: int, first_due: date, installments: int) -> tuple[bool, str]:
    """Saves preview_schedule() as the loan note's repayment schedule. Only for a note with no installment yet (a schedule that exists is edited, never replaced)."""
    with _get_conn() as conn:
        n = conn.execute("SELECT COUNT(*) FROM loan_installments WHERE loan_note_id = ?", (loan_note_id,)).fetchone()[0]
    if n:
        return False, "This loan note already has a schedule. Edit its rows, or delete them first."
    rows = preview_schedule(loan_note_id, first_due, int(installments))
    if not rows:
        return False, "Set the loan note's principal first (Edit loan note terms), and use at least one installment."
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            for r in rows:
                cur.execute("INSERT INTO loan_installments (loan_note_id, installment_no, due_date, principal, interest, outstanding_balance) VALUES (?, 0, ?, ?, ?, 0)",
                            (loan_note_id, str(r["due_date"]), r["principal"], r["interest"]))
            _recompute_schedule(cur, loan_note_id)
            _assert_no_negative_balance(cur, loan_note_id)
            cur.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'LOAN_SCHEDULE_GENERATED', 'loan_notes', ?, ?)",
                        (loan_note_id, f"{len(rows)} monthly installment(s) from {rows[0]['due_date']}"))
    except _ScheduleError as e:
        return False, str(e)
    return True, f"{len(rows)} installment(s) added."


def issue_all_pdcs(loan_note_id: int) -> tuple[bool, str, int]:
    """Parks a post-dated check for every installment that has none yet, each for its scheduled amount on its due date (the same thing 'Issue PDC' does one by one).
    Stops at the first refusal and says which; the checks already parked stay. Returns (ok, message, how many were parked)."""
    from engines.check_rules import issue_check
    with _get_conn() as conn:
        note = conn.execute("SELECT creditor_id, status FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,)).fetchone()
        pending = conn.execute("SELECT installment_id, installment_no, due_date, principal + interest FROM loan_installments WHERE loan_note_id = ? AND status = 'Pending' ORDER BY due_date",
                               (loan_note_id,)).fetchall()
    if not note:
        return False, "Loan note not found.", 0
    if note[1] in ("Settled", "Defaulted", "Cancelled"):
        return False, f"This loan note is {note[1]}: no further checks can be issued.", 0
    if not pending:
        return False, "Every installment already has its check.", 0
    done = 0
    for iid, no, due, amount in pending:
        ok, msg, _cid = issue_check(note[0], round(float(amount), 2), date.fromisoformat(str(due)[:10]), loan_note_id=loan_note_id, installment_id=int(iid))
        if not ok:
            return False, f"Stopped at installment #{no} ({due}): {msg} {done} check(s) were parked before it.", done
        done += 1
    return True, f"{done} post-dated check(s) parked, one per installment.", done


def add_loan_installment(loan_note_id: int, due_date: date, principal: float, interest: float) -> tuple[bool, str]:
    if principal < 0 or interest < 0:
        return False, "Principal and Interest cannot be negative."
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO loan_installments (loan_note_id, installment_no, due_date, principal, interest, outstanding_balance)
                   VALUES (?, 0, ?, ?, ?, 0)""",
                (loan_note_id, str(due_date), principal, interest),
            )
            _recompute_schedule(cur, loan_note_id)
            _assert_no_negative_balance(cur, loan_note_id)
    except _ScheduleError as e:
        return False, str(e)
    return True, "Installment added."


def edit_loan_installment(installment_id: int, due_date: date, principal: float, interest: float) -> tuple[bool, str]:
    if principal < 0 or interest < 0:
        return False, "Principal and Interest cannot be negative."
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT loan_note_id, status FROM loan_installments WHERE installment_id = ?", (installment_id,))
            row = cur.fetchone()
            if not row:
                return False, "Installment not found."
            loan_note_id, status = row
            if status == "Active":
                # Same rule as delete_loan_installment(): a check has already been cut against
                # this row's current amount -- silently rewriting it here would leave the stored
                # schedule diverging from what was actually issued, with no audit trail.
                return False, "Cannot edit an installment with an active check — cancel its check first."
            cur.execute(
                "UPDATE loan_installments SET due_date = ?, principal = ?, interest = ? WHERE installment_id = ?",
                (str(due_date), principal, interest, installment_id),
            )
            _recompute_schedule(cur, loan_note_id)
            _assert_no_negative_balance(cur, loan_note_id)
    except _ScheduleError as e:
        return False, str(e)
    return True, "Installment updated."


def edit_loan_note_terms(loan_note_id: int, principal_amount: float, monthly_interest_rate: float) -> tuple[bool, str]:
    """Edits a loan note's core terms (Principal/Monthly Interest Rate) — while the note is
    Active, or Pending Setup (a Phase 8a placeholder migrated with no real terms on file yet;
    saving real numbers here is exactly how it's meant to graduate out of that state, so this
    also flips it to Active). Editing a Settled/Defaulted/Cancelled note's terms after the fact
    doesn't make sense, so those stay blocked. Existing installment rows' own interest values
    are untouched (interest is always a stored, per-row value, never live-derived — only future
    suggest_installment_interest() calls pick up the new rate); the schedule's
    outstanding_balance chain and the note's total_interest/total_payable are recomputed from
    the new principal via _recompute_schedule(), same negative-balance guard as a normal
    installment edit (can't drop principal below what's already scheduled)."""
    if (err := money_amount_error(principal_amount, "Principal")):
        return False, err
    if monthly_interest_rate < 0:
        return False, "Monthly interest rate cannot be negative."
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT status, is_beginning_balance, note_ref FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
            row = cur.fetchone()
            if not row:
                return False, "Loan note not found."
            if row[0] not in ("Active", "Pending Setup"):
                return False, f"Only Active (or Pending Setup) loan notes can be edited (current status: {row[0]})."
            new_status = "Active" if row[0] == "Pending Setup" else row[0]
            cur.execute(
                "UPDATE loan_notes SET principal_amount = ?, monthly_interest_rate = ?, status = ? WHERE loan_note_id = ?",
                (principal_amount, monthly_interest_rate, new_status, loan_note_id),
            )
            if row[1]:                       # real terms entered: the "Beginning balance (from MS Access)" label has done its job (and is never put back)
                cur.execute("UPDATE loan_notes SET is_beginning_balance = 0, beginning_label_removed = 1 WHERE loan_note_id = ?", (loan_note_id,))
                cur.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BEGINNING_BALANCE_LABEL_REMOVED', 'loan_notes', ?, ?)",
                            (loan_note_id, f"{row[2]}: beginning-balance label removed because its terms were entered"))
            _recompute_schedule(cur, loan_note_id)
            _assert_no_negative_balance(cur, loan_note_id)
            cur.execute(
                "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'LOAN_NOTE_EDITED', 'loan_notes', ?, ?)",
                (loan_note_id, f"Principal/rate updated to {principal_amount}/{monthly_interest_rate}%"),
            )
    except _ScheduleError as e:
        return False, str(e)
    return True, "Loan note updated."


def delete_loan_installment(installment_id: int) -> tuple[bool, str]:
    with _get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT loan_note_id, status, due_date, principal, interest FROM loan_installments WHERE installment_id = ?", (installment_id,))
        row = cur.fetchone()
        if not row:
            return False, "Installment not found."
        loan_note_id, status, due_date, principal, interest = row
        if status == "Active":
            return False, "Cannot delete an installment with an active check — cancel its check first."
        cur.execute("DELETE FROM loan_installments WHERE installment_id = ?", (installment_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values) VALUES (acting_user(), 'LOAN_INSTALLMENT_DELETED', 'loan_installments', ?, ?)",
            (installment_id, f"Loan note {loan_note_id}: installment due {str(due_date)[:10]} deleted (principal {principal:,.2f} + interest {interest:,.2f})"),
        )
        _recompute_schedule(cur, loan_note_id)
        conn.commit()
    return True, "Installment deleted."


def mark_installment_active(cur: sqlite3.Cursor, installment_id: int, check_id: int) -> None:
    """Called from issue_check()/replace_check() (app.py) within their own transaction —
    takes an existing cursor rather than opening a connection, and does NOT commit."""
    cur.execute("SELECT loan_note_id FROM loan_installments WHERE installment_id = ?", (installment_id,))
    (loan_note_id,) = cur.fetchone()
    cur.execute("UPDATE loan_installments SET status = 'Active', check_id = ? WHERE installment_id = ?", (check_id, installment_id))
    _recompute_schedule(cur, loan_note_id)


def unlink_installment_for_check(cur: sqlite3.Cursor, check_id: int) -> None:
    """Called from cancel_check() (app.py) within its own transaction — if this check was
    issued against an installment, that installment reverts to Pending rather than staying
    stuck as Active against a now-cancelled check."""
    cur.execute("SELECT installment_id, loan_note_id FROM loan_installments WHERE check_id = ?", (check_id,))
    row = cur.fetchone()
    if not row:
        return
    installment_id, loan_note_id = row
    cur.execute("UPDATE loan_installments SET status = 'Pending', check_id = NULL WHERE installment_id = ?", (installment_id,))
    _recompute_schedule(cur, loan_note_id)


def retarget_installment_check(cur: sqlite3.Cursor, old_check_id: int, new_check_id: int) -> None:
    """Called from replace_check() (app.py) within its own transaction — a replaced check
    (postdated/void+reissue, not a cancellation) keeps its installment link, just pointing at
    the new check id. Status stays Active since the check itself wasn't cancelled, just reissued."""
    cur.execute("UPDATE loan_installments SET check_id = ? WHERE check_id = ?", (new_check_id, old_check_id))


def unlinked_loan_checks(loan_note_id: int) -> list[tuple]:
    """Active checks on this loan note that no schedule row points at (typically checks brought over from the
    legacy system): [(check_id, check_number, check_date, amount)], oldest date first."""
    with _get_conn() as conn:
        return conn.execute(
            """SELECT c.check_id, c.check_number, c.check_date, c.amount FROM checks_issued c
               WHERE c.loan_note_id = ? AND c.status = 'Active'
                 AND NOT EXISTS (SELECT 1 FROM loan_installments li WHERE li.check_id = c.check_id)
               ORDER BY c.check_date, c.check_id""",
            (loan_note_id,),
        ).fetchall()


def create_schedule_from_checks(loan_note_id: int) -> tuple[bool, str]:
    """Gives every unlinked Active check its own repayment-schedule row: due date = check date, principal = check
    amount, interest = 0 (a check alone can't say how much of it is interest -- edit the row afterwards if known).
    A Pending row that already has the same date and total is linked instead of duplicated. All-or-nothing: if the
    checks add up to more than the note's principal the balance would go negative, so nothing is saved."""
    checks = unlinked_loan_checks(loan_note_id)
    if not checks:
        return False, "Every active check on this loan note is already tied to a schedule row."
    try:
        with _get_conn() as conn:
            cur = conn.cursor()
            cur.execute("SELECT status FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
            row = cur.fetchone()
            if not row:
                return False, "Loan note not found."
            if row[0] not in ("Active", "Pending Setup"):
                return False, f"The schedule of a {row[0]} loan note is read-only."
            created = linked = 0
            for check_id, _number, check_date, amount in checks:
                cur.execute(
                    """SELECT installment_id FROM loan_installments
                       WHERE loan_note_id = ? AND status = 'Pending' AND check_id IS NULL AND due_date = ?
                         AND ABS(principal + interest - ?) < 0.005 LIMIT 1""",
                    (loan_note_id, str(check_date)[:10], amount),
                )
                match = cur.fetchone()
                if match:
                    cur.execute("UPDATE loan_installments SET status = 'Active', check_id = ? WHERE installment_id = ?", (check_id, match[0]))
                    linked += 1
                else:
                    cur.execute(
                        """INSERT INTO loan_installments (loan_note_id, installment_no, due_date, principal, interest, outstanding_balance, status, check_id)
                           VALUES (?, 0, ?, ?, 0, 0, 'Active', ?)""",
                        (loan_note_id, str(check_date)[:10], amount, check_id),
                    )
                    created += 1
            _recompute_schedule(cur, loan_note_id)
            _assert_no_negative_balance(cur, loan_note_id)
            cur.execute(
                "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'SCHEDULE_FROM_CHECKS', 'loan_notes', ?, ?)",
                (loan_note_id, f"{created} schedule row(s) created and {linked} existing row(s) linked to existing checks (interest 0)"),
            )
    except _ScheduleError:
        total = sum(float(c[3]) for c in checks)
        return False, (f"These {len(checks)} checks total {total:,.2f}, which is more than the loan note's principal leaves room for. "
                       "Nothing was changed. Check the principal under \"Edit loan note terms\", or cancel any check that doesn't belong.")
    return True, f"Done: {created} schedule row(s) created, {linked} existing row(s) linked. Interest is 0 on the new rows -- edit a row if you know its interest."
