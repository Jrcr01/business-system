"""Recording checks that were hand-written outside the system (owner, 2026-09-30/10-01).

The owner's mother hand-writes urgent checks from her own booklet and sends photos; the owner records them afterwards. Usually the photo is a new
invoice that needs a post-dated check at the same time, or a check that corrects an earlier one. This records, in one save:
  * the invoice (optional), in the supplier's batch (or a new batch);
  * the handwritten check(s), each with its REAL number, marked printed and tagged handwritten -- so they never go to "Checks to print" / Chrysanth;
  * each check tied to the invoice(s) it pays;
  * optionally "this replaces check no. ___" (voiding that one first if it is still active) -- linked both ways.
Everything is checked BEFORE anything is written, so a refusal never leaves a half-recorded invoice behind."""
from __future__ import annotations

import os
import sqlite3
from datetime import date

from engines.check_rules import (
    _is_check_number_available, allocate_check_to_invoices, assign_check_number, cancel_check, issue_check, issue_checks_grid, link_replacement, replace_check,
)
from engines.common import PRIMARY_BANK_ACCOUNT_ID, php
from engines.db_engine import promote_draft_statement
from engines.park_draft_service import clear_park_draft
from engines.statement_rules import add_invoice, create_order_statement, get_batch_balance, resolve_due_date, sync_invoice_payment_status
from engines.validators import calculate_trade_discount

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def _booklet_for(cur: sqlite3.Cursor, number: str) -> int | None:
    """The registered booklet whose range holds this check number (Active or Exhausted), or None."""
    if not number.isdigit():
        return None
    row = cur.execute("SELECT booklet_id FROM check_booklets WHERE bank_account_id = ? AND status IN ('Active', 'Exhausted') AND start_number <= ? AND end_number >= ? "
                      "ORDER BY booklet_id LIMIT 1", (PRIMARY_BANK_ACCOUNT_ID, int(number), int(number))).fetchone()
    return int(row[0]) if row else None


def _problems(creditor_id, checks, statement_id, loan_note_id, invoice, replaces_check_id) -> tuple[str | None, dict]:
    """Everything that could refuse the save, found before a single row is written. Returns (message or None, facts needed to write)."""
    if not checks:
        return "Enter at least one check.", {}
    seen = set()
    for c in checks:
        number = str(c.get("number") or "").strip()
        if not number:
            return "Every check needs its check number.", {}
        if number in seen:
            return f"Check number {number} is typed twice.", {}
        seen.add(number)
        if float(c.get("amount") or 0) <= 0:
            return f"Check {number}: the amount must be more than zero.", {}
        if not c.get("date"):
            return f"Check {number}: enter the check date.", {}
    if statement_id is not None and loan_note_id is not None:
        return "A check pays a batch or a loan note, not both.", {}
    if statement_id is None and loan_note_id is None and invoice is None:
        return "Pick a batch or loan note, or enter the invoice these checks pay.", {}
    if loan_note_id is not None and invoice is not None:
        return "A loan note has no invoices — leave the invoice empty.", {}
    facts: dict = {"net_invoice": 0.0}
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        row = cur.execute("SELECT creditor_name, terms FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone()
        if not row:
            return "Supplier / lender not found.", {}
        facts["terms"] = row[1]
        for c in checks:
            number = str(c["number"]).strip()
            if not _is_check_number_available(cur, PRIMARY_BANK_ACCOUNT_ID, number):
                return f"Check number {number} is already on file or logged as spoiled.", {}
        if statement_id is not None:
            st_row = cur.execute("SELECT creditor_id, status, is_locked FROM order_statements WHERE statement_id = ?", (statement_id,)).fetchone()
            if not st_row or st_row[0] != creditor_id:
                return "That batch does not belong to this supplier.", {}
            if st_row[1] == "Cancelled":
                return "That batch is cancelled.", {}
            if st_row[2]:
                return "That batch is locked. Unlock it first (Order statements), then record the check.", {}
        if invoice is not None:
            doc = str(invoice.get("doc_ref") or "").strip()
            if not doc:
                return "Enter the invoice number.", {}
            if float(invoice.get("gross") or 0) <= 0:
                return "The invoice amount must be more than zero.", {}
            if cur.execute("SELECT 1 FROM invoices WHERE creditor_id = ? AND doc_ref = ?", (creditor_id, doc)).fetchone():
                return f"Invoice {doc} already exists for this supplier.", {}
            _disc, net = calculate_trade_discount(float(invoice["gross"]), [float(invoice["less_pct"])] if invoice.get("less_pct") else [])
            facts["net_invoice"] = float(net)
        freed = 0.0
        if replaces_check_id is not None:
            old = cur.execute("SELECT creditor_id, status, amount, statement_id, loan_note_id, replacement_check_id FROM checks_issued WHERE check_id = ?", (replaces_check_id,)).fetchone()
            if not old:
                return "The check to replace was not found.", {}
            if old[0] != creditor_id:
                return "The check to replace must be to the same supplier / payee.", {}
            if old[5] is not None or old[1] not in ("Active", "Cancelled"):
                return "The check to replace is not available to replace (already replaced, or spoiled).", {}
            if old[1] == "Active":
                freed = float(old[2])
        total = sum(float(c["amount"]) for c in checks)
        if statement_id is not None:
            remaining = get_batch_balance(statement_id)[2] + facts["net_invoice"] + freed
            what = "the batch's remaining balance"
        elif loan_note_id is not None:
            ln = cur.execute("SELECT total_payable FROM loan_notes WHERE loan_note_id = ?", (loan_note_id,)).fetchone()
            if not ln:
                return "Loan note not found.", {}
            active = cur.execute("SELECT COALESCE(SUM(amount), 0) FROM checks_issued WHERE loan_note_id = ? AND status = 'Active'", (loan_note_id,)).fetchone()[0]
            remaining = (float(ln[0]) - float(active) + freed) if ln[0] is not None else None
            what = "what is left on the loan note"
        else:                                   # a brand-new batch: only the invoice is in it
            remaining = facts["net_invoice"] + freed
            what = "the invoice"
        if remaining is not None and total > remaining + 0.005:
            return f"These checks total {php(total)}, which exceeds {what} ({php(max(remaining, 0.0))}).", {}
    return None, facts


def problems_with(creditor_id, checks, statement_id=None, loan_note_id=None, invoice=None, replaces_check_id=None) -> str | None:
    """The reason this save would be refused (nothing is written), or None when it can go ahead. The screen asks this before it shows its confirmation."""
    return _problems(creditor_id, checks, statement_id, loan_note_id, invoice, replaces_check_id)[0]


def _allocate_fifo(check_id: int, amount: float, statement_id: int) -> None:
    """Tie a check to the batch's open invoices, oldest first, as far as they have room (the same record the batch screen makes when parking)."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """SELECT i.invoice_id, i.amount - COALESCE((SELECT SUM(a.amount) FROM invoice_check_allocations a WHERE a.invoice_id = i.invoice_id), 0) AS remaining
               FROM invoices i WHERE i.statement_id = ? AND i.status != 'Cancelled' ORDER BY i.invoice_date, i.invoice_id""", (statement_id,)).fetchall()
    parts, left = [], round(float(amount), 2)
    for invoice_id, remaining in rows:
        if left <= 0.005:
            break
        take = round(min(left, float(remaining)), 2)
        if take > 0.005:
            parts.append((int(invoice_id), take))
            left = round(left - take, 2)
    if parts:
        allocate_check_to_invoices(check_id, parts)


def record_handwritten_checks(creditor_id: int, checks: list[dict], statement_id: int | None = None, loan_note_id: int | None = None,
                              invoice: dict | None = None, replaces_check_id: int | None = None) -> tuple[bool, str, list[int]]:
    """checks: [{'number': '3992554297', 'date': date, 'amount': 30400.0}, ...]; invoice: {'doc_ref', 'invoice_date', 'gross', 'less_pct'} or None."""
    problem, facts = _problems(creditor_id, checks, statement_id, loan_note_id, invoice, replaces_check_id)
    if problem:
        return False, problem, []

    new_batch = False
    try:
        if invoice is not None:
            if statement_id is None:
                statement_id = create_order_statement(creditor_id)
                new_batch = True
            inv_date = invoice["invoice_date"]
            add_invoice(creditor_id, str(invoice["doc_ref"]).strip(), inv_date, resolve_due_date(inv_date, facts.get("terms")), float(invoice["gross"]),
                        [float(invoice["less_pct"])] if invoice.get("less_pct") else [], statement_id)
        if replaces_check_id is not None:
            with sqlite3.connect(DB_PATH) as conn:
                old_status = conn.execute("SELECT status FROM checks_issued WHERE check_id = ?", (replaces_check_id,)).fetchone()[0]
            if old_status == "Active":
                ok, msg = cancel_check(replaces_check_id, "replaced by a handwritten check")
                if not ok:
                    return False, msg, []

        check_ids: list[int] = []
        if loan_note_id is not None:
            for c in checks:
                ok, msg, cid = issue_check(creditor_id, float(c["amount"]), c["date"], loan_note_id=loan_note_id)
                if not ok:
                    return False, f"Saved {len(check_ids)} of {len(checks)} check(s) before stopping: {msg}", check_ids
                check_ids.append(cid)
        else:
            ok, msg, check_ids = issue_checks_grid(creditor_id, statement_id, [{"check_date": c["date"], "amount": float(c["amount"])} for c in checks])
            if not ok:
                return False, msg, []
        for cid, c in zip(check_ids, checks):
            number = str(c["number"]).strip()
            with sqlite3.connect(DB_PATH) as conn:
                booklet_id = _booklet_for(conn.cursor(), number)
            ok, msg = assign_check_number(cid, booklet_id, int(number) if booklet_id else None, None if booklet_id else number)
            if not ok:
                return False, f"Check {number} was saved without its number: {msg}", check_ids
            if statement_id is not None:
                _allocate_fifo(cid, float(c["amount"]), statement_id)
        with sqlite3.connect(DB_PATH) as conn:
            marks = ",".join("?" * len(check_ids))
            conn.execute(f"UPDATE checks_issued SET printed_at = COALESCE(printed_at, CURRENT_TIMESTAMP), is_handwritten = 1 WHERE check_id IN ({marks})", check_ids)
            conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('HANDWRITTEN_CHECKS_RECORDED', 'checks_issued', ?, ?)",
                         (check_ids[0], f"Handwritten check(s) recorded afterwards: " + ", ".join(f"{str(c['number']).strip()} ({php(float(c['amount']))})" for c in checks)
                          + (f" with invoice {str(invoice['doc_ref']).strip()}" if invoice else "") + (" in a new batch" if new_batch else "")))
        if replaces_check_id is not None:
            ok, msg = link_replacement(replaces_check_id, check_ids[0], "replaced by handwritten check " + str(checks[0]["number"]).strip())
            if not ok:
                return False, f"The checks are saved, but they could not be linked to the check they replace: {msg}", check_ids
        if statement_id is not None:
            with sqlite3.connect(DB_PATH) as conn:
                batch_status = conn.execute("SELECT status FROM order_statements WHERE statement_id = ?", (statement_id,)).fetchone()[0]
            if batch_status == "Draft":                          # same as the batch screen's "Park checks": a Draft batch with checks becomes Issued, with its permanent OS number
                promote_draft_statement(statement_id, "Issued")
            sync_invoice_payment_status(statement_id)
            clear_park_draft(statement_id)               # the same checks typed earlier in the batch's grid are now real; the "typed but not parked" reminder would be stale
    except sqlite3.IntegrityError as e:
        return False, f"Not everything could be saved ({e}). Check the batch and the audit log before trying again.", []
    total = sum(float(c["amount"]) for c in checks)
    return True, f"Recorded {len(check_ids)} handwritten check(s) totaling {php(total)}" + (f" and invoice {str(invoice['doc_ref']).strip()}" if invoice else "") + ".", check_ids


def replace_check_with_number(check_id: int, new_check_date, new_check_number: str = "", reason: str = "") -> tuple[bool, str, int | None]:
    """Void & reissue in ONE step (owner, 2026-10-01: "how can I replace a check without parking it"). With no number it is the old behaviour: the
    replacement is parked. With a number (the replacement was handwritten) the number is checked FIRST -- on file, spoiled or a guarantee leaf is refused
    before anything is voided -- and the replacement is saved with that number, marked printed and handwritten (never goes to Chrysanth), same amount."""
    number = (new_check_number or "").strip()
    if not number:
        return replace_check(check_id, new_check_date, reason)
    with sqlite3.connect(DB_PATH) as conn:
        cur = conn.cursor()
        if not cur.execute("SELECT 1 FROM checks_issued WHERE check_id = ?", (check_id,)).fetchone():
            return False, "Check not found.", None
        if not _is_check_number_available(cur, PRIMARY_BANK_ACCOUNT_ID, number):
            return False, f"Check number {number} is already on file or logged as spoiled.", None
        booklet_id = _booklet_for(cur, number)
    ok, msg, new_id = replace_check(check_id, new_check_date, reason)
    if not ok:
        return False, msg, None
    ok, msg2 = assign_check_number(new_id, booklet_id, int(number) if booklet_id else None, None if booklet_id else number)
    if not ok:
        return False, f"The check was replaced, but its number could not be saved ({msg2}). The replacement is parked; give it its number on the Checks page.", new_id
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE checks_issued SET printed_at = COALESCE(printed_at, CURRENT_TIMESTAMP), is_handwritten = 1 WHERE check_id = ?", (new_id,))
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('HANDWRITTEN_CHECKS_RECORDED', 'checks_issued', ?, ?)",
                     (new_id, f"Replacement recorded with its number: {number} replaces check_id {check_id}"))
    return True, f"Check replaced by handwritten check {number}.", new_id
