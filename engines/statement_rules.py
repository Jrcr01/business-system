"""Order statements (batches), invoices, credit memos, due dates, batch balance, lock/unlock and invoice paid-status.

Moved out of app.py unchanged (2026-09-27) so the rules can be read and tested without the screens; app.py imports
every name back, so app.<name> keeps working."""
from __future__ import annotations

import ast
import calendar
import pandas as pd
import sqlite3

from datetime import date
from datetime import datetime
from datetime import timedelta
from engines.validators import calculate_trade_discount
from engines.validators import detect_invoice_anomalies
from engines.common import _as_date, cell_text, get_conn, php, q_live
from engines.clock import manila_now


DEFAULT_SUPPLIER_TERMS = "120"


def _batch_lock_status(cur: sqlite3.Cursor, statement_id: int | None) -> bool:
    if statement_id is None:
        return False
    cur.execute("SELECT is_locked FROM order_statements WHERE statement_id = ?", (statement_id,))
    row = cur.fetchone()
    return bool(row and row[0] == 1)


def add_credit_memo(statement_id: int, memo_ref: str, memo_date: date, amount: float, reason: str) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT status, is_locked FROM order_statements WHERE statement_id = ?", (statement_id,)
        )
        row = cur.fetchone()
        if not row:
            return False, "Order statement not found."
        status, is_locked = row
        if status == "Cancelled":
            return False, "Cannot apply a credit memo to a cancelled statement."
        if is_locked == 1:
            return False, "This batch is locked. Unlock it first to apply a credit memo."
        cur.execute(
            """INSERT INTO credit_memos (statement_id, memo_ref, memo_date, amount, reason)
               VALUES (?, ?, ?, ?, ?)""",
            (statement_id, memo_ref, str(memo_date), amount, reason),
        )
        # Credit Memo Deduction Guard (migration 026): a new memo needs re-confirming even if
        # earlier memos on this batch were already confirmed -- the net total just changed again.
        cur.execute("UPDATE order_statements SET credit_memos_confirmed = 0 WHERE statement_id = ?", (statement_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CREDIT_MEMO_APPLIED', 'order_statements', ?, ?)",
            (statement_id, f"Memo {memo_ref} for {php(amount)}: {reason or 'no reason given'}"),
        )
        conn.commit()
    sync_invoice_payment_status(statement_id)
    return True, f"Credit memo {memo_ref} for {php(amount)} applied to the batch."


def add_invoice(creditor_id: int, doc_ref: str, invoice_date: date, due_date: date, gross_amount: float, discount_rates: list, statement_id: int | None):
    total_discount, net_amount = calculate_trade_discount(gross_amount, discount_rates)
    with get_conn() as conn:
        warnings = detect_invoice_anomalies(creditor_id, doc_ref, net_amount, str(invoice_date), conn)
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO invoices
               (creditor_id, doc_ref, invoice_date, due_date, gross_amount, trade_discounts, amount, status, statement_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'Unpaid', ?)""",
            (creditor_id, doc_ref, str(invoice_date), str(due_date), gross_amount, str(discount_rates), net_amount, statement_id),
        )
        conn.commit()
    if statement_id is not None:
        recompute_statement_total(statement_id)
        sync_invoice_payment_status(statement_id)
    return net_amount, total_discount, warnings


def add_terms_bank_rule(invoice_date, terms) -> date:
    """Due date by the bank's rule (2026-09-26, per user): terms that are whole months (30/60/90/120/150 days = 1/2/3/4/5
    months) land on the SAME DAY of the month as the invoice, not on an exact day count -- an invoice dated Sep 8 on 120-day
    terms is due Jan 8, not Jan 6. A month with no such day (Jan 31 + 1 month) uses its last day. Terms that are not whole
    months (e.g. 140) and COD keep the plain day count."""
    d = _as_date(invoice_date)
    days = parse_terms_days(terms)
    if days > 0 and days % 30 == 0:
        year_offset, month_index = divmod(d.month - 1 + days // 30, 12)
        year, month = d.year + year_offset, month_index + 1
        return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))
    return d + timedelta(days=days)


def confirm_credit_memos(statement_id: int) -> bool:
    """Marks this batch's current credit memos as reviewed -- clears the Deduction Guard so
    checks can be issued. Re-triggered (confirmed reset to 0) the moment a memo is added or its
    amount changed, so this is a fresh acknowledgment each time the net total actually moves."""
    with get_conn() as conn:
        conn.execute("UPDATE order_statements SET credit_memos_confirmed = 1 WHERE statement_id = ?", (statement_id,))
        conn.commit()
    return True


def create_order_statement(creditor_id: int) -> int:
    """Creates a Draft batch from a supplier alone. total_amount always starts at 0 and is
    never manually entered — it is recalculated from linked invoices (see recompute_statement_total)."""
    ref = new_draft_ref("order_statements", "statement_ref")
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, ?, 0.0, 'Draft')",
            (creditor_id, ref),
        )
        conn.commit()
        return cur.lastrowid


def delete_credit_memo(credit_memo_id: int) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, memo_ref, amount FROM credit_memos WHERE credit_memo_id = ?", (credit_memo_id,)
        )
        row = cur.fetchone()
        if not row:
            return False, "Credit memo not found."
        statement_id, memo_ref, amount = row
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to delete a credit memo."
        cur.execute("DELETE FROM credit_memos WHERE credit_memo_id = ?", (credit_memo_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CREDIT_MEMO_DELETED', 'credit_memos', ?, ?)",
            (credit_memo_id, f"Deleted {memo_ref} ({php(amount)})"),
        )
        # Credit Memo Deduction Guard: re-evaluate after the delete -- if that was the batch's
        # last remaining memo, there's nothing left to confirm, so clear the guard automatically
        # rather than leaving it permanently blocked with no memo left to review or remove.
        remaining = cur.execute("SELECT COUNT(*) FROM credit_memos WHERE statement_id = ?", (statement_id,)).fetchone()[0]
        if remaining == 0:
            cur.execute("UPDATE order_statements SET credit_memos_confirmed = 1 WHERE statement_id = ?", (statement_id,))
        conn.commit()
    sync_invoice_payment_status(statement_id)
    return True, f"Deleted credit memo {memo_ref}."


def delete_invoice(invoice_id: int) -> tuple[bool, str]:
    """Removes an invoice row entirely, gated behind the batch's unlock state like edit_invoice().
    Unlike edit, there's no per-field log (nothing to diff) — one audit_logs entry captures what
    was removed."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, doc_ref, amount FROM invoices WHERE invoice_id = ?", (invoice_id,)
        )
        row = cur.fetchone()
        if not row:
            return False, "Invoice not found."
        statement_id, doc_ref, amount = row
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to delete an invoice."
        (alloc_count,) = cur.execute(
            "SELECT COUNT(*) FROM invoice_check_allocations WHERE invoice_id = ?", (invoice_id,)
        ).fetchone()
        if alloc_count:
            # Deleting the invoice row here would otherwise hit invoice_check_allocations' FK
            # constraint as a raw, uncaught sqlite3.IntegrityError -- a check is already tied to
            # this invoice, so the invoice needs to stay (or the check cancelled/reallocated first).
            return False, f"This invoice has {alloc_count} check(s) already allocated to it — cancel or reallocate those first, then delete."
        cur.execute("DELETE FROM invoices WHERE invoice_id = ?", (invoice_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('INVOICE_DELETED', 'invoices', ?, ?)",
            (invoice_id, f"Deleted {doc_ref} ({php(amount)})"),
        )
        conn.commit()
    if statement_id is not None:
        recompute_statement_total(statement_id)
        sync_invoice_payment_status(statement_id)
    return True, f"Deleted invoice {doc_ref}."


def edit_credit_memo(credit_memo_id: int, memo_ref: str, memo_date: date, amount: float, reason: str) -> tuple[bool, str]:
    """In-place edit of a saved credit memo, gated behind the batch's unlock state — same
    pattern as edit_invoice(). credit_memo_total is summed live in v_batch_net_totals, so
    no recompute step is needed beyond re-syncing invoice payment status."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, memo_ref, memo_date, amount, reason FROM credit_memos WHERE credit_memo_id = ?",
            (credit_memo_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Credit memo not found."
        statement_id, old_ref, old_date, old_amount, old_reason = row
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to edit a credit memo."
        if not memo_ref or not memo_ref.strip():
            return False, "Credit memo reference cannot be blank."
        if amount <= 0:
            return False, "Amount must be greater than zero."

        new_date_s = str(memo_date)
        changes = []
        if memo_ref != old_ref:
            changes.append(("memo_ref", old_ref, memo_ref))
        if new_date_s != str(old_date):
            changes.append(("memo_date", str(old_date), new_date_s))
        if abs(amount - old_amount) > 0.001:
            changes.append(("amount", str(old_amount), str(amount)))
        if (reason or "") != (old_reason or ""):
            changes.append(("reason", old_reason or "", reason or ""))

        if not changes:
            return True, "No changes to save."

        cur.execute(
            "UPDATE credit_memos SET memo_ref = ?, memo_date = ?, amount = ?, reason = ? WHERE credit_memo_id = ?",
            (memo_ref, new_date_s, amount, reason, credit_memo_id),
        )
        # Credit Memo Deduction Guard: an amount change alters the net total again, so it needs
        # re-confirming -- a ref/date/reason-only edit doesn't affect what's owed, so it's left
        # alone rather than forcing a re-confirm for a purely cosmetic change.
        if any(c[0] == "amount" for c in changes):
            cur.execute("UPDATE order_statements SET credit_memos_confirmed = 0 WHERE statement_id = ?", (statement_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CREDIT_MEMO_EDITED', 'credit_memos', ?, ?)",
            (credit_memo_id, f"{len(changes)} field(s) changed: {', '.join(c[0] for c in changes)}"),
        )
        conn.commit()
    sync_invoice_payment_status(statement_id)
    return True, f"Credit memo updated ({len(changes)} field(s) changed)."


def edit_invoice(invoice_id: int, doc_ref: str, invoice_date: date, due_date: date, gross_amount: float, less_pct: float) -> tuple[bool, str]:
    """True in-place edit of a saved invoice row, gated behind the batch's unlock state.
    Auto-logs old->new per changed field (no manual reason — consistent with the
    no-friction unlock philosophy), then keeps the batch total and invoice payment
    status in sync since gross_amount/less % changes affect both."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT statement_id, doc_ref, invoice_date, due_date, gross_amount, trade_discounts FROM invoices WHERE invoice_id = ?",
            (invoice_id,),
        )
        row = cur.fetchone()
        if not row:
            return False, "Invoice not found."
        statement_id, old_doc_ref, old_invoice_date, old_due_date, old_gross, old_discounts = row
        if _batch_lock_status(cur, statement_id):
            return False, "This batch is locked. Unlock it first to edit an invoice."
        if not doc_ref or not doc_ref.strip():
            return False, "Doc Ref cannot be blank."
        if gross_amount <= 0:
            return False, "Amount must be greater than zero."
        if not (0 <= less_pct <= 100):
            return False, "Less % must be between 0 and 100."

        total_discount, new_amount = calculate_trade_discount(gross_amount, [less_pct] if less_pct else [])
        new_invoice_date_s, new_due_date_s = str(invoice_date), str(due_date)
        new_discounts_repr = str([less_pct] if less_pct else [])

        changes = []
        if doc_ref != old_doc_ref:
            changes.append(("doc_ref", old_doc_ref, doc_ref))
        if new_invoice_date_s != str(old_invoice_date):
            changes.append(("invoice_date", str(old_invoice_date), new_invoice_date_s))
        if new_due_date_s != str(old_due_date):
            changes.append(("due_date", str(old_due_date), new_due_date_s))
        if abs(gross_amount - old_gross) > 0.001:
            changes.append(("gross_amount", str(old_gross), str(gross_amount)))
        if abs(parse_less_pct(old_discounts) - less_pct) > 0.001:
            changes.append(("less_pct", str(parse_less_pct(old_discounts)), str(less_pct)))

        if not changes:
            return True, "No changes to save."

        try:
            cur.execute(
                """UPDATE invoices SET doc_ref = ?, invoice_date = ?, due_date = ?, gross_amount = ?, trade_discounts = ?, amount = ?
                   WHERE invoice_id = ?""",
                (doc_ref, new_invoice_date_s, new_due_date_s, gross_amount, new_discounts_repr, new_amount, invoice_id),
            )
        except sqlite3.IntegrityError:
            return False, f"'{doc_ref}' already exists for this supplier — choose a different Doc Ref."
        for field_name, old_val, new_val in changes:
            cur.execute(
                """INSERT INTO invoice_corrections (invoice_id, statement_id, field_name, old_value, new_value)
                   VALUES (?, ?, ?, ?, ?)""",
                (invoice_id, statement_id, field_name, old_val, new_val),
            )
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('INVOICE_EDITED', 'invoices', ?, ?)",
            (invoice_id, f"{len(changes)} field(s) changed: {', '.join(c[0] for c in changes)}"),
        )
        conn.commit()
    if statement_id is not None:
        recompute_statement_total(statement_id)
        sync_invoice_payment_status(statement_id)
    return True, f"Invoice updated ({len(changes)} field(s) changed)."


def get_batch_balance(statement_id: int) -> tuple[float, float, float]:
    """(net_total, active_checks_sum, remaining_balance) for a batch. Uncached (q_live) since
    this drives issuance eligibility and the auto-lock decision — both correctness-critical."""
    net_row = q_live("SELECT net_total FROM v_batch_net_totals WHERE statement_id = ?", (statement_id,))
    net_total = float(net_row["net_total"][0]) if not net_row.empty else 0.0
    sum_row = q_live(
        "SELECT COALESCE(SUM(amount), 0) AS s FROM checks_issued WHERE statement_id = ? AND status = 'Active'",
        (statement_id,),
    )
    active_sum = float(sum_row["s"][0])
    # Bank adjustments (migration 053): the checks stay as recorded; the bank's difference is carried by a check-amount adjustment and a
    # matching batch adjustment, so the batch owes exactly what the bank paid (the two cancel out in what is left).
    from engines.bank_statement_service import adjustment_totals
    batch_adj, check_adj = adjustment_totals(statement_id=statement_id)
    net_total, active_sum = net_total + batch_adj, active_sum + check_adj
    return net_total, active_sum, net_total - active_sum


def get_batch_memo_status(statement_id: int) -> dict:
    """Credit Memo Deduction Guard (migration 026): whether this batch's credit memos (if any)
    still need confirming before checks can be issued. unapplied_count/total_amount describe
    what's on the batch right now regardless of confirmed state -- the caller decides whether to
    actually block (unapplied_count > 0 and not confirmed)."""
    memo_row = q_live(
        "SELECT COUNT(*) AS n, COALESCE(SUM(amount), 0) AS total FROM credit_memos WHERE statement_id = ?",
        (statement_id,),
    )
    stmt_row = q_live("SELECT credit_memos_confirmed FROM order_statements WHERE statement_id = ?", (statement_id,))
    confirmed = bool(int(stmt_row["credit_memos_confirmed"][0])) if not stmt_row.empty else True
    return {
        "unapplied_count": int(memo_row["n"][0]),
        "total_amount": float(memo_row["total"][0]),
        "confirmed": confirmed,
    }


def get_invoices_with_remaining(statement_id: int) -> pd.DataFrame:
    """This batch's non-cancelled invoices with how much of each is still unallocated to any
    check — feeds the mandatory check-to-invoice allocation step (2026-09-14, migration 025).
    'Remaining' subtracts every existing allocation regardless of whether that check has cleared
    yet, since allocating reserves the invoice amount the moment it's made — the same dollar
    can't be double-booked to two different checks. Sorted oldest due date first, matching the
    order the auto-fill allocation preview drains invoices in."""
    df = q_live(
        """SELECT i.invoice_id, i.doc_ref, i.due_date, i.amount,
                  i.amount - COALESCE((SELECT SUM(a.amount) FROM invoice_check_allocations a WHERE a.invoice_id = i.invoice_id), 0) AS remaining
           FROM invoices i WHERE i.statement_id = ? AND i.status != 'Cancelled'
           ORDER BY i.due_date ASC, i.invoice_id ASC""",
        (statement_id,),
    )
    if df.empty:
        return df
    return df[df["remaining"] > 0.005].reset_index(drop=True)


def lock_statement(statement_id: int) -> tuple[bool, str]:
    """Hard rule: a batch can never be locked while its active checks exceed the net total —
    overpayment must be corrected (or the offending check cancelled) first, not locked past."""
    net_total, active_sum, remaining = get_batch_balance(statement_id)
    if remaining < -0.005:
        return False, f"Cannot lock — checks issued ({php(active_sum)}) exceed the net total ({php(net_total)}) by {php(-remaining)}. Correct or cancel a check first."
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("UPDATE order_statements SET is_locked = 1 WHERE statement_id = ?", (statement_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('BATCH_LOCKED', 'order_statements', ?, ?)",
            (statement_id, "Batch re-locked"),
        )
        conn.commit()
    # Thermal statement printing, step 3: edited after an unlock and re-locked -> a new statement version, and "Needs reprint" if an
    # earlier version was already printed. Imported here (not at the top) to avoid a circular import.
    from engines.statement_version_service import on_relock
    if on_relock(statement_id):
        return True, "Batch locked. The statement changed after it was printed, so it is marked Needs reprint."
    return True, "Batch locked."


def new_draft_ref(table: str, column: str) -> str:
    """Short draft reference in the form DRAFT-YY-MM-NN, e.g. DRAFT-26-09-01, DRAFT-26-09-02 -- the
    year, the month, then a running number that restarts each month."""
    prefix = f"DRAFT-{manila_now().strftime('%y-%m')}-"
    with get_conn() as conn:
        used = [r[0][len(prefix):] for r in conn.execute(f"SELECT {column} FROM {table} WHERE {column} LIKE ?", (prefix + "%",))]
    highest = max((int(x) for x in used if x.isdigit()), default=0)
    return f"{prefix}{highest + 1:02d}"


def parse_less_pct(trade_discounts_repr) -> float:
    """Best-effort read of the first discount rate out of trade_discounts' stringified-list
    storage (e.g. "[10.0]" or "[]"), for showing/editing a single Less % in the UI."""
    try:
        rates = ast.literal_eval(str(trade_discounts_repr)) if trade_discounts_repr else []
        return float(rates[0]) if rates else 0.0
    except (ValueError, SyntaxError, TypeError, IndexError):
        return 0.0


def parse_terms_days(terms) -> int:
    """Days to add to an invoice date to get a suggested due date from the supplier's agreed
    Terms picklist (COD/30/60/90/120). A suggestion, not a hard lock — see
    resolve_due_date() below, which recomputes Due Date from this whenever Date Received changes
    but still lets Due Date be freely overridden afterward for negotiated leeway."""
    text = cell_text(terms).strip().upper()
    if text == "COD" or not text:
        return 0
    try:
        return int(text)
    except ValueError:
        return 0


def recompute_statement_total(statement_id: int) -> None:
    """Keeps order_statements.total_amount in sync with SUM(invoices.amount) for that batch.
    Must be called after any invoice insert/update/delete/re-link against a statement_id."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT COALESCE(SUM(amount), 0.0) FROM invoices WHERE statement_id = ?", (statement_id,)
        )
        new_total = cur.fetchone()[0]
        cur.execute("UPDATE order_statements SET total_amount = ? WHERE statement_id = ?", (new_total, statement_id))
        conn.commit()


def resolve_due_date(invoice_date, terms) -> date:
    """Due Date is a fully computed reference field — Date Received + this supplier's Terms,
    recomputed unconditionally on every save. Originally tried preserving a manual Due Date edit
    (comparing against its prior value), matching the old Phase 1 "stays independently editable
    for negotiated leeway" rule — but the user confirmed (2026-09-14) they never edit Due Date
    directly and just want it to always reflect the current Date Received + Terms with no
    exceptions, so the override-preservation logic was dropped rather than debugged further."""
    return add_terms_bank_rule(invoice_date, terms)


def split_remaining_evenly(remaining_balance: float, num_checks: int) -> list[float]:
    """Even split of a balance across N checks in whole pesos (no centavos) — e.g. 33,500 / 3
    -> [11166, 11166, 11168]. Every row but the last floors to a whole peso; the last row takes
    whatever is left (including any centavos on the original balance) so the rows always sum
    exactly to the balance."""
    if num_checks <= 0:
        return []
    base = float(int(remaining_balance // num_checks))
    amounts = [base] * num_checks
    amounts[-1] = round(remaining_balance - base * (num_checks - 1), 2)
    return amounts


def sync_invoice_payment_status(statement_id: int) -> None:
    """Marks this batch's invoices Paid/Partially Paid/Unpaid based on how much has actually
    CLEARED (check_date already reached — same "effective_status" convention as v_checks_ledger,
    schema.sql) — not merely issued. A postdated check hasn't actually left the bank yet, so it
    shouldn't mark an invoice Paid just because a check has been written for it. Deliberately
    separate from get_batch_balance()/lock_statement()'s issuance-based "remaining", which
    correctly stays issuance-based since the batch lock/export workflow is tied to handing checks
    to the supplier to collect, not to them clearing.

    Per-invoice tracking (2026-09-14, migration 025): once any check issued against this batch
    has been allocated to specific invoices (invoice_check_allocations), every invoice in the
    batch switches to a per-invoice computation — each invoice's own cleared allocations vs its
    own amount — instead of the old blanket "every invoice in the batch gets the same status"
    behavior. A batch that has never had an allocated check yet (fully legacy) keeps the original
    batch-level blanket computation untouched. Going-forward only, by design: a legacy check's
    amount is invisible at the per-invoice level once a batch switches to per-invoice mode — only
    its contribution to the batch-level balance (get_batch_balance) stays intact.

    Called after anything that changes a batch's checks (issue, correct amount, replace,
    cancel) or its net total (invoice edit, credit memo)."""
    has_allocations = q_live(
        """SELECT COUNT(*) AS n FROM invoice_check_allocations a
           JOIN checks_issued c ON c.check_id = a.check_id WHERE c.statement_id = ?""",
        (statement_id,),
    )["n"][0] > 0

    if not has_allocations:
        net_row = q_live("SELECT net_total FROM v_batch_net_totals WHERE statement_id = ?", (statement_id,))
        net_total = float(net_row["net_total"][0]) if not net_row.empty else 0.0
        cleared_row = q_live(
            "SELECT COALESCE(SUM(amount), 0) AS s FROM checks_issued WHERE statement_id = ? AND status = 'Active' AND check_date < manila_today()",
            (statement_id,),
        )
        cleared_sum = float(cleared_row["s"][0])
        remaining_uncleared = net_total - cleared_sum
        if net_total > 0 and remaining_uncleared <= 0.005:
            new_status = "Paid"
        elif cleared_sum > 0.005:
            new_status = "Partially Paid"
        else:
            new_status = "Unpaid"
        with get_conn() as conn:
            conn.execute(
                "UPDATE invoices SET status = ? WHERE statement_id = ? AND status != 'Cancelled'",
                (new_status, statement_id),
            )
            conn.commit()
        return

    invoices_df = q_live("SELECT invoice_id, amount FROM invoices WHERE statement_id = ? AND status != 'Cancelled'", (statement_id,))
    cleared_df = q_live(
        """SELECT a.invoice_id, COALESCE(SUM(a.amount), 0) AS cleared
           FROM invoice_check_allocations a JOIN checks_issued c ON c.check_id = a.check_id
           WHERE c.statement_id = ? AND c.status = 'Active' AND c.check_date < manila_today()
           GROUP BY a.invoice_id""",
        (statement_id,),
    )
    cleared_map = dict(zip(cleared_df["invoice_id"], cleared_df["cleared"])) if not cleared_df.empty else {}
    with get_conn() as conn:
        for _, inv in invoices_df.iterrows():
            cleared = float(cleared_map.get(inv["invoice_id"], 0.0))
            inv_amount = float(inv["amount"])
            remaining = inv_amount - cleared
            if inv_amount > 0 and remaining <= 0.005:
                new_status = "Paid"
            elif cleared > 0.005:
                new_status = "Partially Paid"
            else:
                new_status = "Unpaid"
            conn.execute("UPDATE invoices SET status = ? WHERE invoice_id = ?", (new_status, int(inv["invoice_id"])))
        conn.commit()


def unlock_statement(statement_id: int) -> tuple[bool, str]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT status FROM order_statements WHERE statement_id = ?", (statement_id,))
        row = cur.fetchone()
        if not row:
            return False, "Order statement not found."
        if row[0] == "Cancelled":
            return False, "This batch is cancelled and can't be unlocked."
        cur.execute("UPDATE order_statements SET is_locked = 0 WHERE statement_id = ?", (statement_id,))
        cur.execute(
            "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('BATCH_UNLOCKED', 'order_statements', ?, ?)",
            (statement_id, "Batch unlocked for correction/replacement"),
        )
        conn.commit()
    return True, "Batch unlocked."


def waterfall_allocate_preview(check_rows: list[dict], invoices_df: pd.DataFrame) -> pd.DataFrame:
    """Auto-fills which invoice(s) each about-to-be-issued check pays, oldest invoice due date
    first — check_rows in the order they'll be issued (row 0 first), draining each invoice's
    remaining balance before moving to the next one, and moving to the next check once the
    current one runs out. Pure preview: returns one row per (check row, invoice) pair with a
    suggested allocated amount, fully editable in the UI before the checks are actually issued."""
    remaining_by_invoice = dict(zip(invoices_df["invoice_id"], invoices_df["remaining"])) if not invoices_df.empty else {}
    invoice_order = list(invoices_df["invoice_id"]) if not invoices_df.empty else []
    doc_ref_by_invoice = dict(zip(invoices_df["invoice_id"], invoices_df["doc_ref"])) if not invoices_df.empty else {}
    preview_rows = []
    inv_cursor = 0
    for row_idx, row in enumerate(check_rows):
        check_left = float(row["amount"])
        while check_left > 0.005 and inv_cursor < len(invoice_order):
            invoice_id = invoice_order[inv_cursor]
            inv_left = remaining_by_invoice[invoice_id]
            if inv_left <= 0.005:
                inv_cursor += 1
                continue
            take = min(check_left, inv_left)
            preview_rows.append({
                "check_row": row_idx + 1,
                "check_date": row["check_date"],
                "invoice_id": invoice_id,
                "doc_ref": doc_ref_by_invoice[invoice_id],
                "allocated": round(take, 2),
            })
            remaining_by_invoice[invoice_id] -= take
            check_left -= take
            if remaining_by_invoice[invoice_id] <= 0.005:
                inv_cursor += 1
    return pd.DataFrame(preview_rows, columns=["check_row", "check_date", "invoice_id", "doc_ref", "allocated"])
