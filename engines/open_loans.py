"""Open loans (owner, 2026-10-14: "guaranteed checks these are open loans. it has no maturity as long as we pay interest then it stops when we pay the
principal"; mock-up docs/mockups/x_open_loans.html). A loan note marked open earns interest every month on the principal still owed, on its interest
day, from the month set in interest_from (older months are not tracked: no backlog). The system writes one INTEREST row a month, two months ahead, so
it shows on the Calendar, Daily payables and the 8 AM email on its day. A row is paid by its check (written from the loan note, as any lender
payment) or ticked paid in cash or by transfer, before or after its day. Paying principal is a PRINCIPAL row; it lowers the interest of the months
not yet paid, and the last peso closes the loan and lists its guarantee checks to take back. What we owe on an open loan is the principal still owed."""
from __future__ import annotations

import calendar
from datetime import date

from engines.clock import manila_today
from engines.common import get_conn, write_tx

MONTHS_AHEAD = 2
HOW = {"CASH": "cash", "TRANSFER": "transfer", "CHECK": "check"}


def _month_add(d: date, months: int) -> date:
    m = d.month - 1 + months
    return date(d.year + m // 12, m % 12 + 1, 1)


def _due(month_first: date, day: int) -> date:
    return month_first.replace(day=min(int(day), calendar.monthrange(month_first.year, month_first.month)[1]))


def _paid_principal(conn, loan_note_id: int) -> float:
    """Principal paid (or committed by a check written): PRINCIPAL rows ticked paid or carrying an active check."""
    return float(conn.execute("""SELECT COALESCE(SUM(i.principal), 0) FROM loan_installments i LEFT JOIN checks_issued c ON c.check_id = i.check_id
                                 WHERE i.loan_note_id = ? AND i.kind = 'PRINCIPAL' AND (i.paid_at IS NOT NULL OR c.status = 'Active')""",
                              (int(loan_note_id),)).fetchone()[0] or 0)


def principal_owed(loan_note_id: int, conn=None) -> float:
    if conn is None:
        with get_conn() as c:
            return principal_owed(loan_note_id, c)
    row = conn.execute("SELECT principal_amount FROM loan_notes WHERE loan_note_id = ?", (int(loan_note_id),)).fetchone()
    return round(max(float(row[0] or 0) - _paid_principal(conn, loan_note_id), 0.0), 2) if row else 0.0


def _audit(conn, action: str, loan_note_id: int, text: str) -> None:
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'loan_notes', ?, ?)",
                 (action, int(loan_note_id), text))


def _check_terms(principal, rate, day, start) -> str:
    if principal is None or float(principal) <= 0:
        return "Type the principal."
    if rate is None or float(rate) <= 0 or float(rate) > 20:
        return "Type the interest a month, in percent (e.g. 2)."
    if not day or not 1 <= int(day) <= 31:
        return "Type the day of the month the interest is paid (1 to 31)."
    if not start:
        return "Pick the first month to track."
    return ""


def make_open(loan_note_id: int, principal: float, rate: float, interest_day: int, interest_from: date) -> tuple[bool, str]:
    """An existing loan note becomes an open loan (Luz's PENDING-21). Its old rows stay as history."""
    from engines.permissions import require
    ok, why = require("loans.use")
    if not ok:
        return False, why
    problem = _check_terms(principal, rate, interest_day, interest_from)
    if problem:
        return False, problem
    start = interest_from.replace(day=1)
    with write_tx() as conn:
        n = conn.execute("SELECT note_ref, status FROM loan_notes WHERE loan_note_id = ?", (int(loan_note_id),)).fetchone()
        if n is None:
            return False, "Loan note not found."
        if n[1] not in ("Active", "Pending Setup"):
            return False, f"This loan note is {n[1]}."
        conn.execute("""UPDATE loan_notes SET is_open = 1, principal_amount = ?, monthly_interest_rate = ?, interest_day = ?, interest_from = ?, status = 'Active'
                        WHERE loan_note_id = ?""", (round(float(principal), 2), float(rate), int(interest_day), start.isoformat(), int(loan_note_id)))
        _audit(conn, "LOAN_MADE_OPEN", loan_note_id, f"{n[0]}: open loan, principal {float(principal):,.2f}, {float(rate):g}% a month, day {int(interest_day)}, from {start:%B %Y}")
    ensure_interest(manila_today(), only=int(loan_note_id))
    return True, f"{n[0]} is an open loan: {float(rate):g}% a month on the principal still owed, on day {int(interest_day)}, from {start:%B %Y}."


def new_open_loan(creditor_id: int, given_on: date, principal: float, rate: float, interest_day: int, interest_from: date) -> tuple[bool, str, int | None]:
    from engines.loan_note_rules import create_loan_note, promote_draft_loan_note
    problem = _check_terms(principal, rate, interest_day, interest_from) or ("" if given_on else "Give the date the loan was given.")
    if problem:
        return False, problem, None
    ok, msg, lnid = create_loan_note(int(creditor_id), given_on, float(principal), float(rate))
    if not ok:
        return False, msg, None
    ref = promote_draft_loan_note(lnid)
    ok, msg = make_open(lnid, principal, rate, interest_day, interest_from)
    return ok, (f"Open loan {ref} saved. " + msg) if ok else msg, lnid


def _recompute(conn, loan_note_id: int) -> None:
    from engines.loan_schedule_service import _recompute_schedule
    _recompute_schedule(conn.cursor(), int(loan_note_id))


def ensure_interest(today: date | None = None, only: int | None = None) -> int:
    """One INTEREST row a month for every active open loan, from its first tracked month to MONTHS_AHEAD ahead; a month already written is left alone.
    Returns how many rows were added."""
    today = today or manila_today()
    last = _month_add(today.replace(day=1), MONTHS_AHEAD)
    added = 0
    with write_tx() as conn:
        notes = conn.execute("""SELECT loan_note_id, monthly_interest_rate, interest_day, interest_from FROM loan_notes
                                WHERE is_open = 1 AND status = 'Active' AND interest_from IS NOT NULL""" + (" AND loan_note_id = ?" if only else ""),
                             ((only,) if only else ())).fetchall()
        for lnid, rate, day, start in notes:
            have = {r[0] for r in conn.execute("SELECT substr(due_date, 1, 7) FROM loan_installments WHERE loan_note_id = ? AND kind = 'INTEREST'", (lnid,))}
            month, wrote = date.fromisoformat(str(start)[:10]).replace(day=1), False
            while month <= last:
                if month.strftime("%Y-%m") not in have:
                    owed = principal_owed(lnid, conn)
                    if owed <= 0.005:
                        break
                    conn.execute("""INSERT INTO loan_installments (loan_note_id, installment_no, due_date, principal, interest, outstanding_balance, kind)
                                    VALUES (?, 0, ?, 0, ?, 0, 'INTEREST')""", (lnid, _due(month, day).isoformat(), round(owed * float(rate) / 100, 2)))
                    added, wrote = added + 1, True
                month = _month_add(month, 1)
            if wrote:
                _recompute(conn, lnid)
    return added


def mark_paid(installment_id: int, how: str, on: date | None = None, note: str = "") -> tuple[bool, str]:
    """An interest (or principal) row paid in cash or by transfer, on `on` -- before its day too (owner, 2026-10-14: "allow loans to be paid early")."""
    from engines.common import us_date
    from engines.permissions import require
    ok, why = require("payables.mark_paid")
    if not ok:
        return False, why
    how = (how or "CASH").upper()
    if how not in ("CASH", "TRANSFER"):
        return False, "Say how it was paid: cash or transfer."
    on = on or manila_today()
    if on > manila_today():
        return False, "A payment can't be marked paid on a future date."
    with write_tx() as conn:
        row = conn.execute("""SELECT i.loan_note_id, i.due_date, i.principal + i.interest, i.kind, i.paid_at, i.check_id, n.note_ref, n.is_open
                              FROM loan_installments i JOIN loan_notes n ON n.loan_note_id = i.loan_note_id WHERE i.installment_id = ?""", (int(installment_id),)).fetchone()
        if row is None or not row[7] or row[3] not in ("INTEREST", "PRINCIPAL"):
            return False, "Not an open loan's payment."
        if row[4]:
            return False, f"Already marked paid on {us_date(row[4])}."
        if row[5]:
            return False, "This payment has a check: it is paid when the bank pays the check."
        conn.execute("UPDATE loan_installments SET paid_at = ?, paid_how = ?, paid_by = acting_user(), status = 'Active' WHERE installment_id = ?",
                     (on.isoformat(), how, int(installment_id)))
        _audit(conn, "LOAN_ROW_MARKED_PAID", row[0], f"{row[6]} {row[3].lower()} due {us_date(row[1])} ({float(row[2]):,.2f}) paid by {HOW[how]} on {on:%m/%d/%Y}"
               + (f" ({note.strip()})" if (note or "").strip() else ""))
    return True, f"{row[6]} {row[3].lower()} of {float(row[2]):,.2f} marked paid by {HOW[how]} on {on:%m/%d/%Y}."


def unmark_paid(installment_id: int) -> tuple[bool, str]:
    from engines.permissions import require
    ok, why = require("payables.mark_paid")
    if not ok:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT loan_note_id, paid_at, kind FROM loan_installments WHERE installment_id = ?", (int(installment_id),)).fetchone()
        if not row or not row[1]:
            return False, "Not marked paid."
        if row[2] == "PRINCIPAL":
            return False, "A principal payment is undone by deleting it on the loan note."
        conn.execute("UPDATE loan_installments SET paid_at = NULL, paid_how = NULL, paid_by = NULL, status = 'Pending' WHERE installment_id = ?", (int(installment_id),))
        _audit(conn, "LOAN_ROW_UNMARKED_PAID", row[0], f"installment {installment_id}: back to not paid")
    return True, "Back to not paid."


def pay_principal(loan_note_id: int, amount: float, how: str, on: date | None = None) -> tuple[bool, str, list[dict]]:
    """Principal paid by cash, transfer or a check (parked here, written like any lender check). The months not yet paid are worked out again on what is
    still owed; the last peso closes the loan and returns its held guarantee checks, for the person to mark returned. Returns (ok, message, guarantees)."""
    from engines.permissions import require
    ok, why = require("loans.use")
    if not ok:
        return False, why, []
    how, on = (how or "CASH").upper(), on or manila_today()
    if how not in ("CASH", "TRANSFER", "CHECK"):
        return False, "Say how it is paid: check, cash or transfer.", []
    if how != "CHECK" and on > manila_today():
        return False, "A cash or transfer payment can't be dated in the future.", []
    try:
        amount = round(float(amount), 2)
    except (TypeError, ValueError):
        return False, "Type the amount.", []
    with get_conn() as conn:
        n = conn.execute("SELECT creditor_id, note_ref, status, is_open, monthly_interest_rate FROM loan_notes WHERE loan_note_id = ?", (int(loan_note_id),)).fetchone()
        owed = principal_owed(loan_note_id, conn) if n else 0
    if not n or not n[3]:
        return False, "Not an open loan.", []
    if n[2] != "Active":
        return False, f"This loan is {n[2]}.", []
    if amount <= 0 or amount > owed + 0.005:
        return False, f"Type an amount above zero and at most what is still owed ({owed:,.2f}).", []
    with write_tx() as conn:
        iid = conn.execute("""INSERT INTO loan_installments (loan_note_id, installment_no, due_date, principal, interest, outstanding_balance, kind, paid_at, paid_how, paid_by, status)
                              VALUES (?, 0, ?, ?, 0, 0, 'PRINCIPAL', ?, ?, acting_user(), ?) RETURNING installment_id""",
                           (int(loan_note_id), on.isoformat(), amount, None if how == "CHECK" else on.isoformat(), None if how == "CHECK" else how,
                            "Pending" if how == "CHECK" else "Active")).fetchone()[0]
    if how == "CHECK":
        from engines.check_rules import issue_check
        ok, msg, _cid = issue_check(int(n[0]), amount, on, loan_note_id=int(loan_note_id), installment_id=int(iid))
        if not ok:
            with write_tx() as conn:
                conn.execute("DELETE FROM loan_installments WHERE installment_id = ?", (int(iid),))
            return False, msg, []
    left = round(owed - amount, 2)
    with write_tx() as conn:
        for rid, due in conn.execute("""SELECT installment_id, due_date FROM loan_installments WHERE loan_note_id = ? AND kind = 'INTEREST'
                                        AND paid_at IS NULL AND check_id IS NULL AND due_date > ?""", (int(loan_note_id), on.isoformat())).fetchall():
            if left <= 0.005:
                conn.execute("DELETE FROM loan_installments WHERE installment_id = ?", (rid,))
            else:
                conn.execute("UPDATE loan_installments SET interest = ? WHERE installment_id = ?", (round(left * float(n[4]) / 100, 2), rid))
        _recompute(conn, loan_note_id)
        if left <= 0.005:
            conn.execute("UPDATE loan_notes SET status = 'Settled' WHERE loan_note_id = ?", (int(loan_note_id),))
        _audit(conn, "LOAN_PRINCIPAL_PAID", loan_note_id, f"{n[1]}: principal {amount:,.2f} by {HOW[how]} on {on:%m/%d/%Y}; still owed {max(left, 0):,.2f}"
               + ("; closed" if left <= 0.005 else ""))
        held = [{"guarantee_id": a, "check_number": b, "amount": c} for a, b, c in conn.execute(
            "SELECT guarantee_id, check_number, face_amount FROM guarantee_checks WHERE loan_note_id = ? AND status = 'Held' ORDER BY guarantee_id", (int(loan_note_id),))] \
            if left <= 0.005 else []
    msg = f"Principal {amount:,.2f} paid by {HOW[how]}." + (f" Still owed {left:,.2f}; the months not yet paid are worked out again." if left > 0.005
                                                             else f" {n[1]} is fully paid and closed.")
    return True, msg, held


def owed_total() -> tuple[float, float, int]:
    """(principal still owed on active open loans, their interest a month, how many)."""
    with get_conn() as conn:
        rows = conn.execute("SELECT loan_note_id, monthly_interest_rate FROM loan_notes WHERE is_open = 1 AND status = 'Active'").fetchall()
        owed = [(principal_owed(i, conn), float(r or 0)) for i, r in rows]
    return round(sum(o for o, _ in owed), 2), round(sum(o * r / 100 for o, r in owed), 2), len(rows)


def schedule_rows():
    """Interest months not paid and with no check yet, for the schedule of payments (Calendar, Daily payables' sources, the 8 AM email)."""
    import pandas as pd
    with get_conn() as conn:
        return pd.read_sql_query("""SELECT i.due_date AS due_date, cr.creditor_name AS payee_name, 'Lender' AS track,
                                           'Interest · ' || n.note_ref AS reference, NULL AS batch, i.interest AS amount, 'interest' AS stage
                                    FROM loan_installments i JOIN loan_notes n ON n.loan_note_id = i.loan_note_id JOIN creditors cr ON cr.creditor_id = n.creditor_id
                                    WHERE n.is_open = 1 AND n.status = 'Active' AND i.kind = 'INTEREST' AND i.check_id IS NULL AND i.paid_at IS NULL""", conn)
