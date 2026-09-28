"""Checks: booklets and check numbers, issuing/parking, un-parking, corrections, cancel/replace, spoiled checks, invoice allocation.

Moved out of app.py unchanged (2026-09-27) so the rules can be read and tested without the screens; app.py imports
every name back, so app.<name> keeps working."""
from __future__ import annotations

import io
import pandas as pd
import sqlite3

from datetime import date
from datetime import datetime
from engines.check_engine import DEFAULT_BRANCH_CODE
from engines.check_engine import DEFAULT_CHECK_HEIGHT_MM
from engines.check_engine import DEFAULT_CHECK_WIDTH_MM
from engines.check_engine import generate_metrobank_check_pdf
from engines.loan_schedule_service import mark_installment_active
from engines.loan_schedule_service import retarget_installment_check
from engines.loan_schedule_service import unlink_installment_for_check
try:
    from pypdf import PdfReader, PdfWriter
except ImportError:  # same guard as app.py had: the dual-copy check PDF is simply unavailable without pypdf
    PdfReader = PdfWriter = None
from engines.common import PRIMARY_BANK_ACCOUNT_ID, get_conn, php, q_live, write_tx
from engines.statement_rules import _batch_lock_status, get_batch_balance, sync_invoice_payment_status


class _BulkAssignError(Exception):
    pass


def _booklet_row_dict(cur: sqlite3.Cursor, booklet_id: int) -> dict | None:
    cur.execute("SELECT * FROM check_booklets WHERE booklet_id = ?", (booklet_id,))
    row = cur.fetchone()
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


def _check_number_key(num) -> str:
    """Canonical form of a check number for comparing: digits only, the bank's 399 prefix dropped
    from a full 10-digit number, leading zeros dropped -- so "2554301", "02554301" and "3992554301"
    are all the same physical leaf. Non-numeric text is compared as typed."""
    text = str(num).strip()
    if not text.isdigit():
        return text
    if len(text) == 10 and text.startswith(DEFAULT_BRANCH_CODE):
        text = text[len(DEFAULT_BRANCH_CODE):]
    return text.lstrip("0") or "0"


def _check_number_variants(num) -> list[str]:
    """Every stored form the same leaf could be recorded under (as typed, bare, and 399-prefixed)."""
    text = str(num).strip()
    key = _check_number_key(text)
    variants = {text, key}
    if key.isdigit():
        variants.add(DEFAULT_BRANCH_CODE + key.zfill(7))
    return list(variants)


def _is_check_number_available(cur: sqlite3.Cursor, bank_account_id: int, number_str: str, exclude_check_id: int | None = None) -> bool:
    variants = _check_number_variants(number_str)
    marks = ",".join("?" * len(variants))
    if exclude_check_id is None:
        cur.execute(f"SELECT 1 FROM checks_issued WHERE bank_account_id = ? AND check_number IN ({marks})", (bank_account_id, *variants))
    else:
        cur.execute(
            f"SELECT 1 FROM checks_issued WHERE bank_account_id = ? AND check_number IN ({marks}) AND check_id != ?",
            (bank_account_id, *variants, exclude_check_id),
        )
    if cur.fetchone():
        return False
    cur.execute(f"SELECT 1 FROM spoiled_checks WHERE bank_account_id = ? AND check_number IN ({marks})", (bank_account_id, *variants))
    if cur.fetchone():
        return False
    return True


def allocate_check_to_invoices(check_id: int, allocations: list[tuple[int, float]]) -> tuple[bool, str]:
    """Records which invoice(s) one newly-issued check pays and how much of each — mandatory for
    every check issued from here on (2026-09-14, migration 025). Re-validates against each
    invoice's live remaining balance even though the UI already enforces it, since this is the
    actual write path."""
    with write_tx() as conn:          # Phase F1.4: check and save in ONE locked step
        for invoice_id, amount in allocations:
            if amount <= 0.005:
                continue
            row = conn.execute(
                """SELECT i.amount - COALESCE((SELECT SUM(amount) FROM invoice_check_allocations WHERE invoice_id = i.invoice_id), 0)
                   FROM invoices i WHERE i.invoice_id = ?""",
                (invoice_id,),
            ).fetchone()
            remaining = row[0] if row else 0
            if amount > remaining + 0.01:
                conn.rollback()
                return False, f"Invoice #{invoice_id} only has {php(remaining)} remaining — can't allocate {php(amount)}."
            conn.execute(
                "INSERT INTO invoice_check_allocations (invoice_id, check_id, amount) VALUES (?, ?, ?)",
                (invoice_id, check_id, round(amount, 2)),
            )
    return True, "Allocated."


def apply_check_correction(check_id: int, correction_type: str, new_value: str, reason: str) -> tuple[bool, str]:
    with write_tx() as conn:          # Phase F1.4: check and save in ONE locked step
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, loan_note_id, status, amount, check_date, bank_account_id, check_number, printed_at FROM checks_issued WHERE check_id = ?",
            (check_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Check not found."
        statement_id, loan_note_id, status, old_amount, old_check_date, bank_account_id, old_check_number, printed_at = row
        if status != "Active":
            return False, f"Only Active checks can be corrected (current status: {status})."
        # Per-field gating: correcting a *value that already exists* is the trigger for this
        # locked/audited workflow -- printing and number-assignment are independent now (Phase 6
        # Redux), so each field checks its own "is there something here to correct" condition
        # rather than one blanket printed_at gate. Assigning a still-missing number goes through
        # assign_check_number() instead (no lock needed -- nothing to overwrite yet).
        if correction_type == "Check Number" and old_check_number is None:
            return False, "This check has no number yet — use 'Assign a check number' instead of correcting one."
        if correction_type in ("Amount", "Check Date") and printed_at is None:
            return False, "This check hasn't been printed yet — edit it via 'Edit parked checks' instead."
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to apply a correction."

        if correction_type == "Check Number":
            new_number = (new_value or "").strip()
            if not new_number:
                return False, "New check number cannot be blank."
            if not _is_check_number_available(cur, bank_account_id, new_number, exclude_check_id=check_id):
                return False, f"Check number {new_number} is already on file or logged as spoiled for this bank account."
            old_value = str(old_check_number)
            cur.execute("UPDATE checks_issued SET check_number = ? WHERE check_id = ?", (new_number, check_id))
        elif correction_type == "Amount":
            try:
                new_amount = float(new_value)
            except ValueError:
                return False, "New amount must be a number."
            if new_amount <= 0:
                return False, "New amount must be greater than zero."

            # Hard rule: a correction can never push checks issued past what's payable either.
            cur.execute("SELECT installment_id, principal, interest FROM loan_installments WHERE check_id = ?", (check_id,))
            inst_row = cur.fetchone()
            if inst_row is not None:
                _, inst_principal, inst_interest = inst_row
                payable = inst_principal + inst_interest
                if new_amount > payable + 0.005:
                    return False, f"New amount {php(new_amount)} exceeds this installment's payable of {php(payable)}."
            elif loan_note_id is not None:
                cur.execute("SELECT total_payable FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
                (total_payable,) = cur.fetchone()
                cur.execute("SELECT COALESCE(SUM(amount),0) FROM checks_issued WHERE loan_note_id = ? AND status='Active' AND check_id != ?", (loan_note_id, check_id))
                (other_active_sum,) = cur.fetchone()
                if other_active_sum + new_amount > total_payable + 0.005:
                    return False, f"New amount would push total checks issued to {php(other_active_sum + new_amount)}, exceeding this loan's total payable of {php(total_payable)}."
            elif statement_id is not None:
                net_total, active_sum, _ = get_batch_balance(statement_id)
                other_active_sum = active_sum - old_amount
                if other_active_sum + new_amount > net_total + 0.005:
                    return False, f"New amount would push total checks issued to {php(other_active_sum + new_amount)}, exceeding the batch's net total of {php(net_total)}."

            old_value = str(old_amount)
            cur.execute("UPDATE checks_issued SET amount = ? WHERE check_id = ?", (new_amount, check_id))
        elif correction_type == "Check Date":
            try:
                datetime.strptime(new_value.strip(), "%Y-%m-%d")
            except ValueError:
                return False, "New check date must be in YYYY-MM-DD format."
            old_value = str(old_check_date)
            cur.execute("UPDATE checks_issued SET check_date = ? WHERE check_id = ?", (new_value.strip(), check_id))
        else:
            old_value = ""

        cur.execute(
            """INSERT INTO check_corrections (check_id, statement_id, correction_type, old_value, new_value, reason)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (check_id, statement_id, correction_type, old_value, new_value, reason),
        )
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECK_CORRECTION', 'checks_issued', ?, ?)",
            (check_id, f"{correction_type}: {old_value} -> {new_value} ({reason or 'no reason given'})"),
        )
    if correction_type == "Amount" and statement_id is not None:
        sync_invoice_payment_status(statement_id)
    return True, "Correction applied and logged."


def archive_booklet(booklet_id: int) -> tuple[bool, str]:
    with get_conn() as conn:
        conn.execute("UPDATE check_booklets SET status = 'Archived' WHERE booklet_id = ?", (booklet_id,))
        conn.commit()
    return True, "Booklet archived."


def assign_check_number(check_id: int, booklet_id: int | None, chosen_number: int | None, manual_number: str | None) -> tuple[bool, str]:
    """Assigns a check_number to a check that doesn't have one yet — a specific leaf manually
    picked from booklet_id's dropdown (Phase 6 Redux v2 — no auto-sequencing), or exactly
    manual_number for a check with no booklet on file. Independent of printed_at (works whether
    the check has already been printed, is about to be, or won't be printed by this software at
    all) and needs no unlock: filling in a still-missing number isn't a correction to an existing
    value, so it doesn't need that workflow's audit friction. Once a number exists, changing it
    goes through apply_check_correction() instead (locked)."""
    with write_tx() as conn:          # Phase F1.4: check and save in ONE locked step
        cur = conn.cursor()
        cur.execute(
            "SELECT bank_account_id, check_number, status FROM checks_issued WHERE check_id = ?",
            (check_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Check not found."
        bank_account_id, existing_number, status = row
        if status != "Active":
            return False, f"Only Active checks can be assigned a number (current status: {status})."
        if existing_number is not None:
            return False, f"This check already has number {existing_number} — use 'Correct an active check' to change it."

        if booklet_id is not None and chosen_number is not None:
            brow = _booklet_row_dict(cur, booklet_id)
            if not brow:
                return False, "Booklet not found."
            check_number, err = assign_check_number_from_booklet(cur, bank_account_id, brow, chosen_number)
        elif manual_number is not None:
            check_number, err = assign_check_number_manual(cur, bank_account_id, manual_number)
        else:
            return False, "Pick a booklet and a number, or type a check number directly."
        if err:
            return False, err

        cur.execute("UPDATE checks_issued SET check_number = ?, booklet_id = ? WHERE check_id = ?", (check_number, booklet_id, check_id))
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECK_NUMBER_ASSIGNED', 'checks_issued', ?, ?)",
            (check_id, f"Assigned check number {check_number}" + (" (from booklet)" if booklet_id else " (manual)")),
        )
    return True, f"Check number {check_number} assigned."


def assign_check_number_from_booklet(cur: sqlite3.Cursor, bank_account_id: int, booklet_row: dict, chosen_number: int) -> tuple[str | None, str | None]:
    """Phase 6 Redux v2: the user picks any still-available leaf in the booklet's range from a
    dropdown — not forced to take "the next one" the way auto-sequencing worked. next_available_
    number is kept only as a soft "suggested default" for that dropdown: it advances past the
    chosen leaf only when the choice was at or beyond the current cursor, so picking an earlier,
    backfilled leaf doesn't move it backwards or interfere with the normal forward case."""
    booklet_id = int(booklet_row["booklet_id"])
    start, end = int(booklet_row["start_number"]), int(booklet_row["end_number"])
    if not (start <= chosen_number <= end):
        return None, f"{chosen_number} is outside this booklet's range ({start}-{end})."
    check_number = str(chosen_number)
    if not _is_check_number_available(cur, bank_account_id, check_number):
        return None, f"Check number {check_number} is already on file or logged as spoiled."

    if chosen_number >= int(booklet_row["next_available_number"]):
        seq = chosen_number + 1
        while seq <= end and not _is_check_number_available(cur, bank_account_id, str(seq)):
            seq += 1
        new_status = "Exhausted" if seq > end else "Active"
        cur.execute("UPDATE check_booklets SET next_available_number = ?, status = ? WHERE booklet_id = ?", (min(seq, end + 1), new_status, booklet_id))
        booklet_row["next_available_number"] = min(seq, end + 1)
        booklet_row["status"] = new_status
    return check_number, None


def assign_check_number_manual(cur: sqlite3.Cursor, bank_account_id: int, raw_number: str) -> tuple[str | None, str | None]:
    """Backfill/manual path: assigns exactly what's typed, no booklet cursor touched — for
    retroactively recording a check already found/written from an exhausted or older booklet."""
    num = (raw_number or "").strip()
    if not num:
        return None, "Check number is required."
    if not _is_check_number_available(cur, bank_account_id, num):
        return None, f"Check number {num} is already on file or logged as spoiled."
    return num, None


def assign_check_numbers_from_booklet(assignments: list[tuple[int, int]], booklet_id: int) -> tuple[bool, str, list[int]]:
    """Assigns the planned (check_id, leaf) pairs in ONE transaction -- all or nothing. Every leaf is re-validated at write time (inside the
    booklet, still free, not spoiled) exactly as a single assignment is, and the booklet's next-free pointer moves on. Each is audit-logged."""
    if not assignments:
        return False, "Nothing to assign.", []
    try:
        with write_tx() as conn:          # Phase F1.4: check and save in ONE locked step
            cur = conn.cursor()
            brow = _booklet_row_dict(cur, booklet_id)
            if not brow:
                raise _BulkAssignError("Booklet not found.")
            done = []
            for check_id, leaf in sorted(assignments, key=lambda x: x[1]):
                cur.execute("SELECT bank_account_id FROM checks_issued WHERE check_id = ? AND status = 'Active' AND check_number IS NULL", (check_id,))
                row = cur.fetchone()
                if not row:
                    raise _BulkAssignError(f"Check {check_id} is not an Active check without a number any more.")
                number, err = assign_check_number_from_booklet(cur, row[0], brow, int(leaf))
                if err:
                    raise _BulkAssignError(err)
                cur.execute("UPDATE checks_issued SET check_number = ?, booklet_id = ? WHERE check_id = ?", (number, booklet_id, check_id))
                cur.execute(
                    "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECK_NUMBER_ASSIGNED', 'checks_issued', ?, ?)",
                    (check_id, f"Assigned check number {number} (bulk, booklet {booklet_id})"),
                )
                done.append(check_id)
    except _BulkAssignError as e:
        return False, f"{e} Nothing was assigned.", []
    except sqlite3.IntegrityError as e:          # the database's own duplicate-number rule
        return False, f"{e}. Nothing was assigned.", []
    return True, f"Assigned {len(done)} check numbers.", done


def assign_check_numbers_manual(assignments: dict[int, str], booklet_id: int | None) -> tuple[bool, str, list[int]]:
    """assignments: {check_id: raw_check_number}. Assigns exactly what's typed per check — no
    booklet cursor is touched. Works regardless of printed_at (a backfill/handwritten check may
    never go through this software's printer at all). booklet_id is optional, purely for
    record-keeping, and may be any status (Active/Exhausted/Archived) since a backfill is often
    against an already-exhausted one."""
    if not assignments:
        return False, "No checks selected.", []
    seen = set()
    for check_id, raw in assignments.items():
        num = (raw or "").strip()
        if not num:
            return False, f"Check {check_id}: check number is required.", []
        if num in seen:
            return False, f"Check number {num} is entered more than once in this batch.", []
        seen.add(num)

    # Phase F1.5: all or nothing -- one bad number (taken, spoiled, the check changed meanwhile) saves NONE of the batch. Before, the
    # early `return False` inside the locked step kept the numbers saved before the bad one while the screen said it failed.
    assigned_ids = []
    try:
        with write_tx() as conn:          # Phase F1.4: check and save in ONE locked step
            cur = conn.cursor()
            for check_id, raw in assignments.items():
                cur.execute("SELECT bank_account_id FROM checks_issued WHERE check_id = ? AND status = 'Active' AND check_number IS NULL", (check_id,))
                row = cur.fetchone()
                if not row:
                    raise _BulkAssignError(f"Check {check_id} not found, not Active, or already has a number.")
                check_number, err = assign_check_number_manual(cur, row[0], raw)
                if err:
                    raise _BulkAssignError(err)
                cur.execute("UPDATE checks_issued SET check_number = ?, booklet_id = ? WHERE check_id = ?", (check_number, booklet_id, check_id))
                cur.execute(
                    "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECK_NUMBER_ASSIGNED', 'checks_issued', ?, ?)",
                    (check_id, f"Assigned check number {check_number} (manual/backfill)"),
                )
                assigned_ids.append(check_id)
    except _BulkAssignError as e:
        return False, f"{e} Nothing was assigned.", []
    except sqlite3.IntegrityError as e:          # the database's own duplicate-number rule
        return False, f"{e}. Nothing was assigned.", []
    return True, f"Assigned check numbers to {len(assigned_ids)} check(s).", assigned_ids


def build_dual_copy_check_pdf(payee_name: str, amount: float, check_date: date, bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID) -> bytes | None:
    if PdfReader is None:
        return None
    calib = get_check_print_calibration(bank_account_id)
    writer = PdfWriter()
    for _ in range(2):
        page_bytes = generate_metrobank_check_pdf(
            payee_name, amount, datetime.combine(check_date, datetime.min.time()),
            check_width_mm=calib["check_width_mm"], check_height_mm=calib["check_height_mm"],
            offset_x_mm=calib["offset_x_mm"], offset_y_mm=calib["offset_y_mm"],
        )
        reader = PdfReader(io.BytesIO(page_bytes))
        writer.add_page(reader.pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def cancel_check(check_id: int) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT bank_account_id, amount, status, statement_id FROM checks_issued WHERE check_id = ?", (check_id,))
        row = cur.fetchone()
        if not row:
            return False, "Check not found."
        bank_account_id, amount, status, statement_id = row
        if status != "Active":
            return False, f"Only Active checks can be cancelled (current status: {status})."
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to cancel a check."
        cur.execute("UPDATE checks_issued SET status = 'Cancelled' WHERE check_id = ?", (check_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECK_CANCELLED', 'checks_issued', ?, ?)",
            (check_id, f"Cancelled check for {php(amount)}"),
        )
        unlink_installment_for_check(cur, check_id)
        unlink_invoice_allocations_for_check(cur, check_id)
        conn.commit()
    if statement_id is not None:
        sync_invoice_payment_status(statement_id)
    return True, f"Check cancelled ({php(amount)})."


def cancel_order_statement(statement_id: int) -> tuple[bool, str]:
    """Cancels a batch: every Active check issued against it gets cancelled (restores the bank
    balance, stays on record with status='Cancelled' — never deleted, per the audit-trail rule),
    and every non-Cancelled invoice in the batch is marked Cancelled (soft, not deleted —
    invoices.status='Cancelled' is already excluded from AP totals/aging everywhere in this app,
    same as sync_invoice_payment_status already protects it from being overwritten). The batch
    itself is locked permanently; unlock_statement() refuses to reopen a Cancelled batch."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM order_statements WHERE statement_id = ?", (statement_id,))
        row = cur.fetchone()
        if not row:
            return False, "Order statement not found."
        if row[0] == "Cancelled":
            return False, "This batch is already cancelled."
        cur.execute("SELECT check_id FROM checks_issued WHERE statement_id = ? AND status = 'Active'", (statement_id,))
        active_check_ids = [r[0] for r in cur.fetchall()]

    cancelled_checks = 0
    for check_id in active_check_ids:
        ok, _ = cancel_check(int(check_id))
        if ok:
            cancelled_checks += 1

    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "UPDATE invoices SET status = 'Cancelled' WHERE statement_id = ? AND status != 'Cancelled'",
            (statement_id,),
        )
        cancelled_invoices = cur.rowcount
        cur.execute(
            "UPDATE order_statements SET status = 'Cancelled', is_locked = 1 WHERE statement_id = ?",
            (statement_id,),
        )
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BATCH_CANCELLED', 'order_statements', ?, ?)",
            (statement_id, f"Cancelled batch: {cancelled_checks} check(s) cancelled, {cancelled_invoices} invoice(s) marked Cancelled"),
        )
        conn.commit()
    return True, f"Batch cancelled — {cancelled_checks} check(s) cancelled, {cancelled_invoices} invoice(s) marked Cancelled."


def create_booklet(bank_account_id: int, booklet_code: str, start_num: int, end_num: int, date_received: date) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        # Two booklets covering the same leaf numbers on the same bank account would make every
        # number in the overlap ambiguous about which physical checkbook it actually belongs to.
        # Archived booklets are excluded -- their range is retired stock, safe to reuse.
        cur.execute(
            """SELECT booklet_code, start_number, end_number FROM check_booklets
               WHERE bank_account_id = ? AND status != 'Archived' AND start_number <= ? AND end_number >= ?""",
            (bank_account_id, end_num, start_num),
        )
        overlap = cur.fetchone()
        if overlap:
            other_code, other_start, other_end = overlap
            return False, f"Overlaps {other_code or 'an existing booklet'}'s range ({other_start}-{other_end}) on this bank account."
        cur.execute(
            """INSERT INTO check_booklets (bank_account_id, booklet_code, start_number, end_number, next_available_number, date_received, status)
               VALUES (?, ?, ?, ?, ?, ?, 'Active')""",
            (bank_account_id, booklet_code, start_num, end_num, start_num, str(date_received)),
        )
        conn.commit()
    return True, "Booklet registered."


def edit_parked_check(check_id: int, new_amount: float, new_check_date: date) -> tuple[bool, str]:
    """Checks with no check number yet stay freely editable regardless of the batch's lock state
    (2026-09-27, per user: no number assigned = not final -- so a check already exported to Chrysanth, which only carries a
    placeholder number, is still editable) — nothing final exists yet, so the usual unlock gate (built around a real, numbered check)
    doesn't apply. Auto-logs old -> new + timestamp, same no-friction pattern as editing an
    invoice while unlocked. Once a check is printed, further changes go through
    apply_check_correction() instead (requires unlocking the batch first)."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, loan_note_id, status, amount, check_date, check_number FROM checks_issued WHERE check_id = ?",
            (check_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Check not found."
        statement_id, loan_note_id, status, old_amount, old_check_date, check_number = row
        if status != "Active":
            return False, f"Only Active checks can be edited (current status: {status})."
        if check_number is not None:
            return False, "This check already has a check number — use 'Correct an active check' instead (requires unlocking the batch)."
        if new_amount <= 0:
            return False, "Amount must be greater than zero."

        # Same hard payable-ceiling rule as issue_check()/apply_check_correction().
        cur.execute("SELECT installment_id, principal, interest FROM loan_installments WHERE check_id = ?", (check_id,))
        inst_row = cur.fetchone()
        if inst_row is not None:
            _, inst_principal, inst_interest = inst_row
            payable = inst_principal + inst_interest
            if new_amount > payable + 0.005:
                return False, f"New amount {php(new_amount)} exceeds this installment's payable of {php(payable)}."
        elif loan_note_id is not None:
            cur.execute("SELECT total_payable FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,))
            (total_payable,) = cur.fetchone()
            cur.execute("SELECT COALESCE(SUM(amount),0) FROM checks_issued WHERE loan_note_id = ? AND status='Active' AND check_id != ?", (loan_note_id, check_id))
            (other_active_sum,) = cur.fetchone()
            if other_active_sum + new_amount > total_payable + 0.005:
                return False, f"New amount would push total checks issued to {php(other_active_sum + new_amount)}, exceeding this loan's total payable of {php(total_payable)}."
        elif statement_id is not None:
            net_total, active_sum, _ = get_batch_balance(statement_id)
            other_active_sum = active_sum - old_amount
            if other_active_sum + new_amount > net_total + 0.005:
                return False, f"New amount would push total checks issued to {php(other_active_sum + new_amount)}, exceeding the batch's net total of {php(net_total)}."

        new_date_s = str(new_check_date)
        changes = []
        if abs(new_amount - old_amount) > 0.001:
            changes.append(("Amount", str(old_amount), str(new_amount)))
        if new_date_s != str(old_check_date):
            changes.append(("Check Date", str(old_check_date), new_date_s))
        if not changes:
            return True, "No changes to save."

        cur.execute("UPDATE checks_issued SET amount = ?, check_date = ? WHERE check_id = ?", (new_amount, new_date_s, check_id))
        for field, old_v, new_v in changes:
            cur.execute(
                """INSERT INTO check_corrections (check_id, statement_id, correction_type, old_value, new_value, reason)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (check_id, statement_id, field, old_v, new_v, "Parked-check edit (auto-logged)"),
            )
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'PARKED_CHECK_EDITED', 'checks_issued', ?, ?)",
            (check_id, f"{len(changes)} field(s) changed: {', '.join(c[0] for c in changes)}"),
        )
        conn.commit()
    if statement_id is not None:
        sync_invoice_payment_status(statement_id)
    return True, f"{len(changes)} field(s) updated."


def get_available_booklet_numbers(bank_account_id: int, booklet_row) -> list[int]:
    """Phase 6 Redux v2: manual per-booklet assignment needs every still-open leaf in a booklet's
    range, not just a single 'next' cursor — the user picks whichever one they like from a
    dropdown (it shrinks as numbers get used), auto-sequencing having been dropped as unneeded
    complexity for this single-operator, low-volume workflow."""
    start, end = int(booklet_row["start_number"]), int(booklet_row["end_number"])
    with get_conn() as conn:
        cur = conn.cursor()
        used = set()
        cur.execute("SELECT check_number FROM checks_issued WHERE bank_account_id = ? AND check_number IS NOT NULL", (bank_account_id,))
        used.update(r[0] for r in cur.fetchall())
        cur.execute("SELECT check_number FROM spoiled_checks WHERE bank_account_id = ?", (bank_account_id,))
        used.update(r[0] for r in cur.fetchall())
    used_keys = {_check_number_key(u) for u in used}
    return [n for n in range(start, end + 1) if _check_number_key(n) not in used_keys]


def get_booklets(bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID, statuses: tuple[str, ...] = ("Active",)) -> pd.DataFrame:
    placeholders = ",".join("?" * len(statuses))
    return q_live(
        f"SELECT * FROM check_booklets WHERE bank_account_id = ? AND status IN ({placeholders}) ORDER BY booklet_id",
        (bank_account_id, *statuses),
    )


def get_check_print_calibration(bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID) -> dict:
    """This bank account's check-print calibration (bank_accounts, migration 019) -- falls back
    to check_engine.py's own defaults if the account row is somehow missing."""
    row = q_live(
        "SELECT check_width_mm, check_height_mm, check_offset_x_mm, check_offset_y_mm FROM bank_accounts WHERE bank_account_id = ?",
        (bank_account_id,),
    )
    if row.empty:
        return {"check_width_mm": DEFAULT_CHECK_WIDTH_MM, "check_height_mm": DEFAULT_CHECK_HEIGHT_MM, "offset_x_mm": 0.0, "offset_y_mm": 0.0}
    r = row.iloc[0]
    return {
        "check_width_mm": float(r["check_width_mm"]),
        "check_height_mm": float(r["check_height_mm"]),
        "offset_x_mm": float(r["check_offset_x_mm"]),
        "offset_y_mm": float(r["check_offset_y_mm"]),
    }


def get_existing_check_dates_for_creditor(creditor_id: int) -> set[date]:
    """Every date this creditor already has an Active check on file for -- backs the
    same-day-duplicate warning when issuing a new check. This business deliberately avoids ever
    writing a second check to the same supplier on the same day (postdating past the due date
    instead, per the owner's own standing practice), so a new check landing on a date that
    already has one is worth flagging before it's parked, not after."""
    df = q_live("SELECT check_date FROM checks_issued WHERE creditor_id = ? AND status = 'Active'", (creditor_id,))
    if df.empty:
        return set()
    return {date.fromisoformat(d) for d in df["check_date"]}


def get_latest_check_for_creditor(creditor_id: int) -> tuple[date, float] | None:
    """The most recent (by check_date) Active check ever issued to this creditor -- a quick
    reference point when picking a new one's date ("last time we paid this supplier was..."),
    surfaced right where a new check's date gets entered. Excludes Cancelled/Replaced/Spoiled
    checks since those aren't real payment history anymore. None when nothing's been issued yet."""
    row = q_live(
        "SELECT check_date, amount FROM checks_issued WHERE creditor_id = ? AND status = 'Active' ORDER BY check_date DESC LIMIT 1",
        (creditor_id,),
    )
    if row.empty:
        return None
    return date.fromisoformat(row["check_date"][0]), float(row["amount"][0])


def issue_check(
    creditor_id: int,
    amount: float,
    check_date: date,
    statement_id: int | None = None,
    loan_note_id: int | None = None,
    bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID,
    installment_id: int | None = None,
) -> tuple[bool, str, int | None]:
    """Records (parks) a check with no check number yet — Phase 6 Redux: a number is assigned
    separately, whenever it's actually known (see assign_check_number()), independent of
    printing. Every check still belongs to a batch and is fully recorded the moment it's issued,
    same as before; only the number is deferred."""
    # Phase F1.4: the payable-ceiling check and the save are ONE locked step -- two people issuing on the same batch at the same moment
    # can never both pass the check and overpay it (the second waits, then sees the first check).
    with write_tx() as conn:
        cur = conn.cursor()
        # Hard rule: a check can never be issued for more than what's actually payable.
        if installment_id is not None:
            inst_row = cur.execute("SELECT principal, interest FROM loan_installments WHERE installment_id = ?", (installment_id,)).fetchone()
            if inst_row:
                payable = float(inst_row[0]) + float(inst_row[1])
                if amount > payable + 0.005:
                    return False, f"Amount {php(amount)} exceeds this installment's payable of {php(payable)}.", None
        elif loan_note_id is not None:
            note_row = cur.execute("SELECT total_payable FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,)).fetchone()
            if note_row:
                active_sum = float(cur.execute("SELECT COALESCE(SUM(amount), 0) FROM checks_issued WHERE loan_note_id = ? AND status = 'Active'",
                                               (loan_note_id,)).fetchone()[0])
                total_payable = float(note_row[0])
                if active_sum + amount > total_payable + 0.005:
                    return False, f"Amount would push total checks issued to {php(active_sum + amount)}, exceeding this loan's total payable of {php(total_payable)}.", None
        elif statement_id is not None:
            net_total, active_sum, remaining = get_batch_balance(statement_id)
            if amount > remaining + 0.005:
                return False, f"Amount {php(amount)} exceeds the batch's remaining balance of {php(remaining)}.", None

        check_id = cur.execute(
            """INSERT INTO checks_issued
               (creditor_id, bank_account_id, statement_id, loan_note_id, check_number, check_date, amount, status)
               VALUES (?, ?, ?, ?, NULL, ?, ?, 'Active') RETURNING check_id""",
            (creditor_id, bank_account_id, statement_id, loan_note_id, str(check_date), amount),
        ).fetchone()[0]
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECK_ISSUED', 'checks_issued', ?, ?)",
            (check_id, f"Parked check for {php(amount)} to creditor {creditor_id} — number to be assigned at print time"),
        )
        if installment_id is not None:
            mark_installment_active(cur, installment_id, check_id)
    if statement_id is not None:
        sync_invoice_payment_status(statement_id)
    return True, f"Check parked for {php(amount)} — pending (number assigned when printed).", check_id


def issue_checks_grid(
    creditor_id: int,
    statement_id: int,
    rows: list[dict],
    bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID,
) -> tuple[bool, str, list[int] | None]:
    """Bulk-issues (parks) multiple checks against a batch in one transaction — the discretionary
    split-into-N-checks workflow. No check number is collected here (Phase 6 Redux: assigned at
    print time), just date + amount per row."""
    cleaned = []
    for r in rows:
        amount = float(r["amount"])
        if amount <= 0:
            return False, "Every row's amount must be greater than zero.", None
        cleaned.append({"check_date": r["check_date"], "amount": amount})

    if not cleaned:
        return False, "No check rows to issue.", None

    # Phase F1.4: the ceiling check and the saves are ONE locked step (see issue_check).
    with write_tx() as conn:
        # Hard rule: this batch of checks can never push total active checks past the net total.
        net_total, active_sum, remaining = get_batch_balance(statement_id)
        grid_total = sum(r["amount"] for r in cleaned)
        if grid_total > remaining + 0.005:
            return False, f"These checks total {php(grid_total)}, exceeding the batch's remaining balance of {php(remaining)}.", None
        cur = conn.cursor()
        check_ids = []
        for r in cleaned:
            check_ids.append(cur.execute(
                """INSERT INTO checks_issued
                   (creditor_id, bank_account_id, statement_id, loan_note_id, check_number, check_date, amount, status)
                   VALUES (?, ?, ?, NULL, NULL, ?, ?, 'Active') RETURNING check_id""",
                (creditor_id, bank_account_id, statement_id, str(r["check_date"]), r["amount"]),
            ).fetchone()[0])
            cur.execute(
                "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECK_ISSUED', 'checks_issued', ?, ?)",
                (check_ids[-1], f"Parked check for {php(r['amount'])} to creditor {creditor_id} — number to be assigned at print time"),
            )

    sync_invoice_payment_status(statement_id)
    total = sum(r["amount"] for r in cleaned)
    return True, f"Parked {len(cleaned)} check(s) totaling {php(total)} — pending (numbers assigned when printed).", check_ids


def log_spoiled_check(check_number: str, reason: str, bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID) -> tuple[bool, str]:
    canonical, blocking, _warnings = spoiled_check_problems(check_number, bank_account_id)
    if blocking:
        return False, blocking[0]
    try:
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO spoiled_checks (bank_account_id, check_number, reason) VALUES (?, ?, ?)",
                (bank_account_id, canonical, reason),
            )
            spoiled_id = cur.lastrowid
            cur.execute(
                "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECK_SPOILED', 'spoiled_checks', ?, ?)",
                (spoiled_id, f"Check {canonical} logged as spoiled: {reason or 'no reason given'}"),
            )
            conn.commit()
    except sqlite3.IntegrityError as e:      # the database's own hard rule (a duplicate check number) caught what the screen check could not
        return False, str(e)
    return True, f"Check {canonical} logged as spoiled under 'Error Checks Only (for control)'."


def pick_number_source(booklet_id, chosen_number, manual_number) -> tuple[str, object]:
    """Which number an 'Assign' click should use. A number typed in by hand ALWAYS wins over the booklet dropdown --
    typing one is an explicit choice, and ignoring it (as this used to) silently assigned a booklet leaf instead."""
    if manual_number and str(manual_number).strip():
        return "manual", str(manual_number).strip()
    if booklet_id is not None and chosen_number is not None:
        return "booklet", (int(booklet_id), int(chosen_number))
    return "none", None


def plan_sequential_numbers(booklet_id: int, start_number: int | None, check_ids: list[int]) -> tuple[list[tuple[int, int]], list[int], str | None]:
    """Fast bulk assignment (2026-09-27): the next consecutive FREE leaves of a booklet, from start_number (default: its first free leaf), for
    check_ids in the given order. Leaves already used or logged as spoiled are stepped over. Returns (pairs [(check_id, leaf)], the leaves
    stepped over, error). Nothing is written -- this is the preview the user reviews before one confirmation."""
    if not check_ids:
        return [], [], "Pick at least one check."
    with get_conn() as conn:
        cur = conn.cursor()
        brow = _booklet_row_dict(cur, booklet_id)
        if not brow:
            return [], [], "Booklet not found."
        bank_account_id = int(brow["bank_account_id"])
    free = get_available_booklet_numbers(bank_account_id, brow)
    if start_number is not None:
        free = [n for n in free if n >= int(start_number)]
    if len(free) < len(check_ids):
        return [], [], f"Only {len(free)} free leaf/leaves are left from there in this booklet, but {len(check_ids)} checks need a number."
    leaves = free[:len(check_ids)]
    free_set = set(leaves)
    stepped_over = [n for n in range(leaves[0], leaves[-1] + 1) if n not in free_set]
    return list(zip(check_ids, leaves)), stepped_over, None


def print_check_pdf(check_id: int) -> tuple[bool, str, bytes | None]:
    """Generates a check's PDF and stamps printed_at (first time only — a reprint doesn't move
    it). Deliberately independent of check_number: printing and number-assignment are two
    separate actions that can happen in either order, since the number isn't even drawn on the
    PDF (it's pre-printed on the physical blank stock) — "print" just means the date/payee/
    amount overlay is ready to go on whichever leaf gets fed into the printer."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT creditor_id, bank_account_id, amount, check_date, status FROM checks_issued WHERE check_id = ?",
            (check_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Check not found.", None
        creditor_id, bank_account_id, amount, check_date, status = row
        if status != "Active":
            return False, f"Only Active checks can be printed (current status: {status}).", None
        cur.execute("UPDATE checks_issued SET printed_at = CURRENT_TIMESTAMP WHERE check_id = ? AND printed_at IS NULL", (check_id,))
        conn.commit()

    payee_row = q_live("SELECT creditor_name FROM creditors WHERE creditor_id = ?", (creditor_id,))
    payee_name = payee_row["creditor_name"][0] if not payee_row.empty else ""
    calib = get_check_print_calibration(bank_account_id)
    pdf_bytes = generate_metrobank_check_pdf(
        payee_name, float(amount), datetime.combine(pd.to_datetime(check_date).date(), datetime.min.time()),
        check_width_mm=calib["check_width_mm"], check_height_mm=calib["check_height_mm"],
        offset_x_mm=calib["offset_x_mm"], offset_y_mm=calib["offset_y_mm"],
    )
    return True, f"Check printed for {php(amount)}.", pdf_bytes


def replace_check(check_id: int, new_check_date: date, reason: str) -> tuple[bool, str, int | None]:
    """Void + reissue. The replacement check is parked, same as any newly issued check (Phase 6
    Redux: no number until print time) — if the original check had already been printed, that
    physical leaf's number is simply retired (still on file, permanently tied to the voided
    check) rather than reused."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT creditor_id, bank_account_id, statement_id, loan_note_id, amount, status FROM checks_issued WHERE check_id = ?",
            (check_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Check not found.", None
        creditor_id, bank_account_id, statement_id, loan_note_id, amount, status = row
        if status != "Active":
            return False, f"Only Active checks can be replaced (current status: {status}).", None
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to replace a check.", None

        cur.execute(
            """INSERT INTO checks_issued
               (creditor_id, bank_account_id, statement_id, loan_note_id, check_number, check_date, amount, status)
               VALUES (?, ?, ?, ?, NULL, ?, ?, 'Active')""",
            (creditor_id, bank_account_id, statement_id, loan_note_id, str(new_check_date), amount),
        )
        new_check_id = cur.lastrowid

        cur.execute(
            """UPDATE checks_issued
               SET status = 'Replaced', replacement_check_id = ?, replacement_reason = ?, replaced_at = CURRENT_TIMESTAMP
               WHERE check_id = ?""",
            (new_check_id, reason, check_id),
        )
        retarget_installment_check(cur, check_id, new_check_id)
        retarget_invoice_allocations_for_check(cur, check_id, new_check_id)
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECK_REPLACED', 'checks_issued', ?, ?)",
            (check_id, f"Replaced by a new parked check (check_id {new_check_id}): {reason or 'no reason given'}"),
        )
        conn.commit()
    if statement_id is not None:
        sync_invoice_payment_status(statement_id)
    return True, f"Check replaced. New check parked for {php(amount)} — pending (number assigned when printed).", new_check_id


def retarget_invoice_allocations_for_check(cur: sqlite3.Cursor, old_check_id: int, new_check_id: int) -> None:
    """Called from replace_check() within its own transaction -- a replaced check (void+reissue,
    not a cancellation) still pays the same invoice(s), just under a new check id, so the
    allocation rows move with it rather than being dropped (mirrors
    retarget_installment_check())."""
    cur.execute("UPDATE invoice_check_allocations SET check_id = ? WHERE check_id = ?", (new_check_id, old_check_id))


def retract_check_number(check_id: int) -> tuple[bool, str]:
    """Takes a wrongly assigned number back off a check: the check returns to 'no number yet' and, if the number
    came from a booklet, that leaf becomes available again (no false gap) and the booklet's next-number pointer
    steps back over the freed leaves. Logged in the audit trail. Refused for a locked batch (unlock it first),
    the same rule as any other correction to an existing check value."""
    with write_tx() as conn:          # Phase F1.4: check and save in ONE locked step
        cur = conn.cursor()
        cur.execute("SELECT check_number, booklet_id, status, statement_id, bank_account_id FROM checks_issued WHERE check_id = ?", (check_id,))
        row = cur.fetchone()
        if not row:
            return False, "Check not found."
        number, booklet_id, status, statement_id, bank_account_id = row
        if status != "Active":
            return False, f"Only Active checks can have a number retracted (current status: {status})."
        if number is None:
            return False, "This check has no number to retract."
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to retract a number."
        cur.execute("UPDATE checks_issued SET check_number = NULL, booklet_id = NULL WHERE check_id = ?", (check_id,))
        pointer_note = ""
        if booklet_id is not None:
            brow = _booklet_row_dict(cur, booklet_id)
            if brow:
                start, end, old_next = int(brow["start_number"]), int(brow["end_number"]), int(brow["next_available_number"])
                cur.execute("SELECT check_number FROM checks_issued WHERE bank_account_id = ? AND check_number IS NOT NULL", (bank_account_id,))
                used = {_check_number_key(r[0]) for r in cur.fetchall()}
                cur.execute("SELECT check_number FROM spoiled_checks WHERE bank_account_id = ?", (bank_account_id,))
                used |= {_check_number_key(r[0]) for r in cur.fetchall()}
                new_next = old_next
                while new_next - 1 >= start and _check_number_key(new_next - 1) not in used:
                    new_next -= 1
                if new_next != old_next:
                    cur.execute("UPDATE check_booklets SET next_available_number = ?, status = CASE WHEN status = 'Exhausted' THEN 'Active' ELSE status END WHERE booklet_id = ?",
                                (new_next, booklet_id))
                    pointer_note = f"; booklet's next number is back at {new_next}"
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values, new_values) VALUES (acting_user(), 'CHECK_NUMBER_RETRACTED', 'checks_issued', ?, ?, ?)",
            (check_id, f"check_number {number}", "number removed" + (" (booklet leaf returned)" if booklet_id else "") + pointer_note),
        )
    return True, f"Number {number} retracted" + (" — the booklet leaf is available again." if booklet_id else ".")


def spoiled_check_problems(check_number: str, bank_account_id: int = PRIMARY_BANK_ACCOUNT_ID) -> tuple[str, list[str], list[str]]:
    """Checks a number about to be logged as spoiled (2026-09-27, a gap the user found: the same number could be logged twice, and
    nothing looked at the booklets). Returns (canonical number, blocking problems, warnings):
    - blocking: not a number; already logged as spoiled; already the number of a check on file.
    - warning:  not inside any registered booklet's range (could be a leaf from a booklet that was never registered, so it can go ahead
                once confirmed).
    The number is stored in the full 10-digit form the checks use, so 7-digit and 10-digit typings of one leaf are the same leaf."""
    text = str(check_number or "").strip()
    if not text:
        return text, ["Check number is required."], []
    if not text.isdigit():
        return text, ["A check number is digits only."], []
    canonical = DEFAULT_BRANCH_CODE + text.zfill(7) if len(text) <= 7 else text
    variants = _check_number_variants(canonical)
    marks = ",".join("?" * len(variants))
    blocking, warnings = [], []
    with get_conn() as conn:
        cur = conn.cursor()
        row = cur.execute(f"SELECT spoiled_id, logged_at FROM spoiled_checks WHERE bank_account_id = ? AND check_number IN ({marks}) LIMIT 1",
                          (bank_account_id, *variants)).fetchone()
        if row:
            blocking.append(f"Check {canonical} is already logged as spoiled (entry #{row[0]}, {str(row[1])[:16]}) — one leaf can only be logged once.")
        row = cur.execute(
            f"""SELECT c.check_id, c.status, c.amount, cr.creditor_name FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
                WHERE c.bank_account_id = ? AND c.check_number IN ({marks}) LIMIT 1""",
            (bank_account_id, *variants),
        ).fetchone()
        if row:
            blocking.append(f"Check {canonical} is already the number of a {row[1]} check to {row[3]} ({php(row[2])}) — it can't also be spoiled.")
        key = int(_check_number_key(canonical))
        in_range = False
        for start, end in cur.execute("SELECT start_number, end_number FROM check_booklets WHERE bank_account_id = ?", (bank_account_id,)).fetchall():
            if int(_check_number_key(start)) <= key <= int(_check_number_key(end)):
                in_range = True
                break
        if not in_range:
            warnings.append(f"Check {canonical} is not inside any registered booklet's range. Log it anyway only if the leaf really came from a booklet that isn't registered here.")
    return canonical, blocking, warnings


def unlink_invoice_allocations_for_check(cur: sqlite3.Cursor, check_id: int) -> None:
    """Called from cancel_check() within its own transaction -- a cancelled check no longer
    represents a real payment, so its invoice_check_allocations rows must go with it. Without
    this, get_invoices_with_remaining() kept counting the invoice as paid down by a check that no
    longer exists, permanently hiding that slice of the invoice from every future allocation
    (2026-09-17 fix -- mirrors unlink_installment_for_check()'s handling of the loan-note side)."""
    cur.execute("DELETE FROM invoice_check_allocations WHERE check_id = ?", (check_id,))


def unpark_checks(statement_id: int, check_ids: list[int]) -> tuple[bool, str]:
    """Undoes "Park checks" for the chosen checks: deletes them outright, along with their invoice links, as if never made
    (2026-09-26, per user). Only a check that is Active and has no check number yet can go (2026-09-27: even if it was already
    exported to Chrysanth, which only holds a placeholder number for it) -- a numbered check is final, and is cancelled or replaced instead. All-or-nothing. If that leaves the batch with no
    active check and it was Issued, it goes back to Draft (keeping its OS number). Logged in the audit trail."""
    ids = [int(c) for c in check_ids]
    if not ids:
        return False, "Pick at least one parked check."
    with get_conn() as conn:
        cur = conn.cursor()
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked — unlock it first."
        details, exported, print_batches = [], 0, set()
        for cid in ids:
            cur.execute("SELECT print_batch_id FROM checks_issued WHERE check_id = ?", (cid,))
            pb = cur.fetchone()
            if pb and pb[0] is not None:
                print_batches.add(pb[0])
            cur.execute("SELECT statement_id, status, check_number, printed_at, amount, check_date FROM checks_issued WHERE check_id = ?", (cid,))
            row = cur.fetchone()
            if not row or row[0] != statement_id:
                return False, f"Check {cid} isn't part of this batch."
            _, status, number, printed_at, amount, check_date = row
            if status != "Active":
                return False, f"Check {cid} is {status}, so it can't be un-parked."
            if number is not None:
                return False, f"Check {cid} already has the number {number} — un-park only removes unnumbered checks; cancel or replace it instead."
            if printed_at is not None:
                exported += 1
            for table, column in (("loan_installments", "check_id"), ("cc_payments", "payment_check_id")):
                try:
                    used = cur.execute(f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1", (cid,)).fetchone()
                except sqlite3.OperationalError:
                    used = None
                if used:
                    return False, f"Check {cid} has other records attached ({table.replace('_', ' ')}) — cancel it instead."
            details.append((cid, amount, str(check_date)[:10]))
        for cid, amount, check_date in details:
            cur.execute("DELETE FROM invoice_check_allocations WHERE check_id = ?", (cid,))
            cur.execute("DELETE FROM check_corrections WHERE check_id = ?", (cid,))      # only the edit log of a check that never became final
            cur.execute("DELETE FROM checks_issued WHERE check_id = ?", (cid,))
            cur.execute(
                "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values) VALUES (acting_user(), 'CHECK_UNPARKED', 'checks_issued', ?, ?)",
                (cid, f"parked check {php(amount)} dated {check_date} removed from batch {statement_id}"),
            )
        cur.execute(
            "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values) VALUES (acting_user(), 'CHECKS_UNPARKED', 'order_statements', ?, ?)",
            (statement_id, f"{len(details)} parked check(s) totaling {php(sum(d[1] for d in details))} removed"),
        )
        for pbid in print_batches:      # keep the export history's counts honest after removing some of its checks
            cur.execute(
                "UPDATE check_print_batches SET check_count = (SELECT COUNT(*) FROM checks_issued WHERE print_batch_id = ?), "
                "total_amount = (SELECT COALESCE(SUM(amount), 0) FROM checks_issued WHERE print_batch_id = ?) WHERE print_batch_id = ?",
                (pbid, pbid, pbid),
            )
        cur.execute("SELECT status, statement_ref FROM order_statements WHERE statement_id = ?", (statement_id,))
        batch_status, batch_ref = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM checks_issued WHERE statement_id = ? AND status = 'Active'", (statement_id,))
        back_to_draft = ""
        if cur.fetchone()[0] == 0 and batch_status == "Issued":
            cur.execute("UPDATE order_statements SET status = 'Draft' WHERE statement_id = ?", (statement_id,))
            cur.execute(
                "INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'STATEMENT_BACK_TO_DRAFT', 'order_statements', ?, ?)",
                (statement_id, f"no active checks left after un-park; {batch_ref} is a Draft again (number kept)"),
            )
            back_to_draft = f" The batch has no active checks left, so it is a Draft again (it keeps {batch_ref})."
        conn.commit()
    sync_invoice_payment_status(statement_id)
    chrysanth_note = f" {exported} of them had already been exported to Chrysanth — remove them there too." if exported else ""
    return True, f"Un-parked {len(details)} check(s) totaling {php(sum(d[1] for d in details))}; the balance is available again." + chrysanth_note + back_to_draft
