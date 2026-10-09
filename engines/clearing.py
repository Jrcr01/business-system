"""When is a check paid? (owner, 2026-10-08) Only when it is CLEARED: the bank cleared it (an imported bank statement), someone ticked it cleared, or it
was settled outside the bank (the payee returned it and was paid otherwise). A check whose date has passed is NOT paid by that alone: until it clears it
stays owed ("waiting for the bank"). This replaces the old date rule (a past-dated check counted as paid).

Internal control: a tick is checked against the bank. When a bank statement is imported, a ticked check that the statement shows gets the bank's date
(final); a ticked check that a statement covering its tick date does NOT show goes on the Review list (CLEARED_NOT_ON_BANK).

  * cleared_sql(alias) / owed_sql(alias)       -- the one SQL condition every report and rule uses
  * mark_cleared / unmark_cleared              -- the manual tick
  * waiting_for_bank()                         -- written checks not cleared yet
  * check_ticks_against_bank()                 -- run after a bank statement import
  * cutoff_preview() / apply_cutoff()          -- once, at go-live: checks before the first bank statement are "cleared before go-live"

Daily payables (owner, 2026-10-09): the same rule for the other tracks. A bank-loan installment is paid when it is ticked (mark_loan_cleared) or a bank line
is matched to it; a card statement when its payment is recorded (pay_card -> cc_service.settle_cc_statement_via_cash). daily_payables() is the checklist:
every item of the four tracks still to pay, plus the ones ticked and not yet on a bank statement.""" 
from __future__ import annotations

from datetime import date

import pandas as pd

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.permissions import require as require_permission
from engines.common import us_date

CUTOFF_NOTE = "cleared before go-live"


def cleared_sql(alias: str = "c") -> str:
    p = f"{alias}." if alias else ""
    return f"({p}bank_cleared_date IS NOT NULL OR {p}manual_cleared_at IS NOT NULL OR {p}settled_outside_at IS NOT NULL)"


def bank_loan_cleared_sql(alias: str = "bli") -> str:
    """A bank-loan installment is paid when ticked (cleared_at) or a bank line is matched to it (daily payables, owner 2026-10-09) -- not by its date."""
    p = f"{alias}." if alias else ""
    return (f"({p}cleared_at IS NOT NULL OR {p}installment_id IN (SELECT matched_installment_id FROM bank_transactions "
            f"WHERE matched_installment_id IS NOT NULL))")


def bank_loan_owed_sql(alias: str = "bli") -> str:
    return f"NOT {bank_loan_cleared_sql(alias)}"


def owed_sql(alias: str = "c") -> str:
    """Still to be paid: an active check not cleared, whatever its date."""
    return f"NOT {cleared_sql(alias)}"


def resync(check_ids: list[int]) -> None:
    """After checks clear (or a clearing is taken back): the invoices of their batches and the loan notes they pay are brought up to date."""
    if not check_ids:
        return
    from engines.loan_schedule_service import _recompute_schedule
    from engines.statement_rules import sync_invoice_payment_status
    marks = ",".join("?" * len(check_ids))
    with get_conn() as conn:
        rows = conn.execute(f"SELECT DISTINCT statement_id, loan_note_id FROM checks_issued WHERE check_id IN ({marks})", [int(i) for i in check_ids]).fetchall()
    for sid in {r[0] for r in rows if r[0]}:
        sync_invoice_payment_status(int(sid))
    notes = {r[1] for r in rows if r[1]}
    if notes:
        with write_tx() as conn:
            cur = conn.cursor()
            for nid in notes:
                try:
                    _recompute_schedule(cur, int(nid))
                except Exception:                               # noqa: BLE001 -- a placeholder note without terms: nothing to settle
                    pass


def resync_all() -> None:
    """Every batch's invoice status and every loan note brought up to the clearing rule (once, when the rule replaced the date rule; safe to repeat)."""
    with get_conn() as conn:
        ids = [r[0] for r in conn.execute("SELECT check_id FROM checks_issued WHERE status = 'Active'")]
    resync(ids)
    with write_tx() as conn:
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CLEARING_RESYNC', 'checks_issued', NULL, ?)",
                     (f"invoice and loan statuses refreshed under the clearing rule ({len(ids)} active check(s))",))


def _audit(conn, action: str, check_id: int, text: str) -> None:
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'checks_issued', ?, ?)",
                 (action, check_id, text))


def mark_cleared(check_id: int, on: date | None = None, note: str = "") -> tuple[bool, str]:
    """The manual tick: the check is paid from `on` (default today). Refused for a check the bank already cleared, a voided one, one with no number, or one dated
    after `on` (a post-dated check can't clear before its date)."""
    ok, why = require_permission("payables.mark_paid")
    if not ok:
        return False, why
    on = on or manila_today()
    if on > manila_today():
        return False, "A check can't be cleared on a future date."
    with write_tx() as conn:
        row = conn.execute("SELECT check_number, status, bank_cleared_date, manual_cleared_at, check_date FROM checks_issued WHERE check_id = ?", (check_id,)).fetchone()
        if not row:
            return False, "Check not found."
        number, status, bank, manual, dated = row
        if dated and str(dated)[:10] > on.isoformat():                # a post-dated check can't clear before its date (owner, 2026-10-09)
            return False, f"Check {number} is dated {us_date(dated)}: it can't clear before its date."
        if status != "Active":
            return False, f"This check is {status.lower()}, not active."
        if not number:
            return False, "This check has no number yet: it can't have cleared."
        if bank:
            return False, f"The bank already cleared this check on {us_date(bank)}."
        conn.execute("UPDATE checks_issued SET manual_cleared_at = ?, manual_cleared_by = acting_user(), manual_cleared_note = ? WHERE check_id = ?",
                     (on.isoformat(), (note or "").strip() or None, check_id))
        _audit(conn, "CHECK_MARKED_CLEARED", check_id, f"{number} cleared {on:%m/%d/%Y}" + (f" ({note.strip()})" if (note or "").strip() else ""))
    resync([check_id])
    return True, f"Check {number} marked cleared on {on:%m/%d/%Y}. The bank statement will confirm it."


def unmark_cleared(check_id: int) -> tuple[bool, str]:
    ok, why = require_permission("payables.mark_paid")
    if not ok:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT check_number, manual_cleared_at FROM checks_issued WHERE check_id = ?", (check_id,)).fetchone()
        if not row or not row[1]:
            return False, "This check isn't ticked cleared."
        conn.execute("UPDATE checks_issued SET manual_cleared_at = NULL, manual_cleared_by = NULL, manual_cleared_note = NULL WHERE check_id = ?", (check_id,))
        _audit(conn, "CHECK_UNMARKED_CLEARED", check_id, f"{row[0]}: the cleared tick of {us_date(row[1])} taken back")
    resync([check_id])
    return True, f"Check {row[0]} is owed again (waiting for the bank)."


def mark_loan_cleared(installment_id: int, on: date | None = None, note: str = "") -> tuple[bool, str]:
    """The tick for a bank-loan installment (auto-debit): paid on `on` (default today). Not a future date, not before its due date; one the bank already
    shows needs no tick."""
    ok, why = require_permission("payables.mark_paid")
    if not ok:
        return False, why
    on = on or manila_today()
    if on > manila_today():
        return False, "A payment can't be marked paid on a future date."
    with write_tx() as conn:
        row = conn.execute(f"""SELECT bli.due_date, bli.amount, bl.loan_ref, bli.cleared_at, {bank_loan_cleared_sql('bli')}
                               FROM bank_loan_installments bli JOIN bank_loans bl ON bl.bank_loan_id = bli.bank_loan_id WHERE bli.installment_id = ?""",
                           (installment_id,)).fetchone()
        if not row:
            return False, "Bank-loan payment not found."
        due, amount, ref, ticked, done = row
        if done:
            return False, f"{ref} due {us_date(due)} is already paid" + (f" (ticked {us_date(ticked)})." if ticked else " (on the bank statement).")
        if str(due)[:10] > on.isoformat():
            return False, f"{ref} is due {us_date(due)}: it can't be paid before its date."
        conn.execute("UPDATE bank_loan_installments SET cleared_at = ?, cleared_by = acting_user(), cleared_note = ? WHERE installment_id = ?",
                     (on.isoformat(), (note or "").strip() or None, installment_id))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BANK_LOAN_MARKED_PAID', 'bank_loan_installments', ?, ?)",
                     (installment_id, f"{ref} due {us_date(due)} ({float(amount):,.2f}) paid {on:%m/%d/%Y}" + (f" ({note.strip()})" if (note or "").strip() else "")))
    return True, f"{ref} ({float(amount):,.2f}) marked paid on {on:%m/%d/%Y}. The bank statement will confirm it."


def unmark_loan_cleared(installment_id: int) -> tuple[bool, str]:
    ok, why = require_permission("payables.mark_paid")
    if not ok:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT cleared_at FROM bank_loan_installments WHERE installment_id = ?", (installment_id,)).fetchone()
        if not row or not row[0]:
            return False, "This bank-loan payment isn't ticked paid."
        conn.execute("UPDATE bank_loan_installments SET cleared_at = NULL, cleared_by = NULL, cleared_note = NULL WHERE installment_id = ?", (installment_id,))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values) VALUES (acting_user(), 'BANK_LOAN_UNMARKED_PAID', 'bank_loan_installments', ?, ?)",
                     (installment_id, f"the paid tick of {us_date(row[0])} taken back"))
    return True, "The bank-loan payment is owed again."


def _card_left(conn, cc_statement_id: int) -> float:
    total, paid = conn.execute("SELECT total_amount_due, COALESCE(amount_paid, 0) FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,)).fetchone()
    return round(max(float(total or 0) - float(paid or 0), 0.0), 2)


def _card_restate(conn, cc_statement_id: int, delta: float) -> str:
    """Adds `delta` (a payment, or minus one taken back) to the statement's paid amount -- which may already hold what was paid before payments were
    recorded one by one -- then: what is left carries to the next statement (unpaid_balance_next), status Unpaid / Partially Paid / Paid, and
    payment_date = the latest recorded payment's (posting) date."""
    total, paid = conn.execute("SELECT total_amount_due, COALESCE(amount_paid, 0) FROM cc_statements WHERE cc_statement_id = ?", (cc_statement_id,)).fetchone()
    total = float(total or 0)
    paid = round(min(max(float(paid or 0) + delta, 0.0), total), 2)
    left = round(max(total - paid, 0.0), 2)
    status = "Paid" if left <= 0.005 else ("Partially Paid" if paid > 0.005 else "Unpaid")
    last = conn.execute("SELECT MAX(payment_date) FROM cc_payments WHERE cc_statement_id = ?", (cc_statement_id,)).fetchone()[0]
    conn.execute("""UPDATE cc_statements SET amount_paid = ?, unpaid_balance_next = ?, status = ?, payment_date = COALESCE(?, payment_date),
                    payment_method = CASE WHEN ? > 0 THEN 'Cash / Online' ELSE payment_method END WHERE cc_statement_id = ?""",
                 (paid, left, status, last, paid, cc_statement_id))
    return status


def pay_card(cc_statement_id: int, on: date | None = None, note: str = "", amount: float | None = None) -> tuple[bool, str]:
    """A card payment from the daily payables (owner, 2026-10-09: the co-owners' daily way; the owner's way is the next SOA,
    card_rules.confirm_cc_payment_from_next_statement, which stays the final word and flags a difference): full or PARTIAL. It is recorded on the card statement (cc_payments) with the date it
    posts (`on`); the statement's paid amount, what carries over and its status (Partially Paid / Paid) follow the payments on file. `amount` None = all
    that is left."""
    ok, why = require_permission("payables.mark_paid")
    if not ok:
        return False, why
    on = on or manila_today()
    if on > manila_today():
        return False, "A payment can't be recorded on a future date."
    with write_tx() as conn:
        row = conn.execute("""SELECT cs.status, cs.card_id, cc.card_name || ' - ' || cc.card_number_last4 FROM cc_statements cs
                              JOIN credit_cards cc ON cc.card_id = cs.card_id WHERE cs.cc_statement_id = ?""", (cc_statement_id,)).fetchone()
        if not row:
            return False, "Card statement not found."
        status, card_id, card = row
        if status in ("Paid", "Cancelled"):
            return False, f"{card}: this statement is already {status.lower()}."
        left = _card_left(conn, cc_statement_id)
        pay = round(float(left if amount is None else amount), 2)
        if pay <= 0:
            return False, f"{card}: type an amount more than 0."
        if pay > left + 0.005:
            return False, f"{card}: {pay:,.2f} is more than the {left:,.2f} still to pay on this statement."
        conn.execute("INSERT INTO cc_payments (cc_statement_id, card_id, payment_date, amount, method, note) VALUES (?, ?, ?, ?, 'Cash / Online', ?)",
                     (cc_statement_id, card_id, on.isoformat(), pay, (note or "").strip() or None))
        new_status = _card_restate(conn, cc_statement_id, pay)
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CC_PAYMENT_RECORDED', 'cc_statements', ?, ?)",
                     (cc_statement_id, f"{card}: paid {pay:,.2f} on {on:%m/%d/%Y}" + (f" ({note.strip()})" if (note or "").strip() else "") + f"; now {new_status}"))
    rest = round(left - pay, 2)
    return True, f"{card}: {pay:,.2f} recorded as paid on {on:%m/%d/%Y}" + (f"; the other {rest:,.2f} carries to the next statement." if rest > 0.005 else " (fully paid).")


def unpay_card(payment_id: int) -> tuple[bool, str]:
    """Takes back a card payment recorded here; the statement's paid amount and status follow."""
    ok, why = require_permission("payables.mark_paid")
    if not ok:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT cc_statement_id, amount, payment_date FROM cc_payments WHERE payment_id = ?", (payment_id,)).fetchone()
        if not row:
            return False, "Card payment not found."
        conn.execute("DELETE FROM cc_payments WHERE payment_id = ?", (payment_id,))
        _card_restate(conn, int(row[0]), -float(row[1]))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values) VALUES (acting_user(), 'CC_PAYMENT_TAKEN_BACK', 'cc_statements', ?, ?)",
                     (int(row[0]), f"payment of {float(row[1]):,.2f} on {us_date(row[2])} taken back"))
    return True, f"The card payment of {float(row[1]):,.2f} is taken back; that amount is owed again."


def daily_payables() -> pd.DataFrame:
    """The daily payables checklist (owner, 2026-10-09): one row per item still to pay -- written checks (supplier and lender) not cleared, bank-loan
    installments not paid, card statements not paid -- plus the ones ticked and not yet confirmed by the bank. Columns: kind ('check' | 'loan' | 'card'),
    item_id, day (date), payee, ref, amount, track, ticked (date or None), note, due (its day has come)."""
    today = manila_today()
    with get_conn() as conn:
        checks = pd.read_sql_query(
            f"""SELECT 'check' AS kind, c.check_id AS item_id, c.check_date AS day, cr.creditor_name AS payee, c.check_number AS ref, c.amount,
                       CASE WHEN c.statement_id IS NOT NULL THEN 'Supplier' ELSE 'Lender' END AS track, c.manual_cleared_at AS ticked, c.manual_cleared_note AS note
                FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
                WHERE c.status = 'Active' AND c.check_number IS NOT NULL AND c.check_number != '' AND c.bank_cleared_date IS NULL AND c.settled_outside_at IS NULL
                  AND COALESCE(c.manual_cleared_note, '') != ?""", conn, params=(CUTOFF_NOTE,))
        loans = pd.read_sql_query(
            f"""SELECT 'loan' AS kind, bli.installment_id AS item_id, bli.due_date AS day, cr.creditor_name AS payee, bl.loan_ref AS ref, bli.amount,
                       'Bank loan' AS track, bli.cleared_at AS ticked, bli.cleared_note AS note
                FROM bank_loan_installments bli JOIN bank_loans bl ON bl.bank_loan_id = bli.bank_loan_id JOIN creditors cr ON cr.creditor_id = bl.creditor_id
                WHERE bl.is_cancelled = 0 AND COALESCE(bli.cleared_note, '') != ?
                  AND bli.installment_id NOT IN (SELECT matched_installment_id FROM bank_transactions WHERE matched_installment_id IS NOT NULL)""",
            conn, params=(CUTOFF_NOTE,))
        cards = pd.read_sql_query(                             # what is still to pay on each open statement (after any partial payments)
            """SELECT 'card' AS kind, cs.cc_statement_id AS item_id, cs.due_date AS day, cr.creditor_name AS payee,
                      cc.card_name || ' - ' || cc.card_number_last4 AS ref,
                      ROUND(cs.total_amount_due - COALESCE(cs.amount_paid, 0), 2) AS amount,
                      'Card' AS track, NULL AS ticked,
                      COALESCE(cs.amount_paid, 0) AS paid_so_far, NULL AS note
               FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id JOIN creditors cr ON cr.creditor_id = cc.creditor_id
               WHERE cs.status = 'Unpaid' AND COALESCE(cs.amount_paid, 0) <= 0.005           -- a payment, even partial, settles it here: the rest carries to the next statement (owner, 2026-10-09)
                 AND cs.total_amount_due > 0.005""", conn)
        card_paid = pd.read_sql_query(                         # the card payments recorded lately, each on its posting date, until the bank shows them
            """SELECT 'cardpay' AS kind, p.payment_id AS item_id, cs.due_date AS day, cr.creditor_name AS payee,
                      cc.card_name || ' - ' || cc.card_number_last4 AS ref, p.amount, 'Card' AS track, p.payment_date AS ticked, p.note
               FROM cc_payments p JOIN cc_statements cs ON cs.cc_statement_id = p.cc_statement_id JOIN credit_cards cc ON cc.card_id = p.card_id
               JOIN creditors cr ON cr.creditor_id = cc.creditor_id
               WHERE p.payment_date >= ? AND cs.paid_confirmed = 0                 -- until the next SOA confirms it (the owner's way)
                 AND p.cc_statement_id NOT IN (SELECT matched_cc_statement_id FROM bank_transactions WHERE matched_cc_statement_id IS NOT NULL)""",
            conn, params=((today - pd.Timedelta(days=60)).isoformat(),))
    if not cards.empty:
        cards["note"] = [f"paid so far {p:,.2f}" if p and p > 0.005 else None for p in cards.pop("paid_so_far")]
    else:
        cards = cards.drop(columns=["paid_so_far"])
    df = pd.concat([checks, loans, cards, card_paid], ignore_index=True)
    if df.empty:
        return df.assign(due=pd.Series(dtype=bool))
    df["day"] = df["day"].astype(str).str[:10]
    df["ticked"] = df["ticked"].where(df["ticked"].notna(), None)
    df["due"] = df["day"] <= today.isoformat()
    order = {"Lender": 0, "Bank loan": 1, "Supplier": 2, "Card": 3}
    df["_o"] = df["track"].map(order)
    return df.sort_values(["day", "_o", "amount"]).drop(columns="_o").reset_index(drop=True)


def waiting_for_bank() -> pd.DataFrame:
    """Written checks (with a number) not cleared yet, oldest first: check_id, check_number, payee, check_date, amount, past (its date has passed),
    plus the ticked ones not yet on a bank statement (ticked = True)."""
    with get_conn() as conn:
        df = pd.read_sql_query(
            f"""SELECT c.check_id, c.check_number, cr.creditor_name AS payee, c.check_date, c.amount, c.released_date,
                       c.manual_cleared_at, c.manual_cleared_note,
                       CASE WHEN c.statement_id IS NOT NULL THEN 'Supplier' ELSE 'Lender' END AS track
                FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
                WHERE c.status = 'Active' AND c.check_number IS NOT NULL AND c.check_number != ''
                  AND c.bank_cleared_date IS NULL AND c.settled_outside_at IS NULL
                ORDER BY c.check_date, c.check_number""", conn)
    today = manila_today().isoformat()
    df["past"] = df["check_date"].astype(str).str[:10] < today
    df["ticked"] = df["manual_cleared_at"].notna()
    return df


def check_ticks_against_bank() -> int:
    """After a bank statement import: a ticked check that the imported statements cover (its tick date is inside them) but that the bank did not clear
    goes on the Review list. Returns how many were flagged."""
    from engines.review_flags import flag
    with write_tx() as conn:
        span = conn.execute("SELECT MIN(posting_date), MAX(posting_date) FROM bank_transactions").fetchone()
        if not span or not span[0]:
            return 0
        rows = conn.execute("""SELECT check_id, check_number, manual_cleared_at, amount FROM checks_issued
                               WHERE status = 'Active' AND manual_cleared_at IS NOT NULL AND bank_cleared_date IS NULL AND settled_outside_at IS NULL
                                 AND manual_cleared_at BETWEEN ? AND ? AND COALESCE(manual_cleared_note, '') != ?""",
                            (span[0], span[1], CUTOFF_NOTE)).fetchall()
        for cid, num, on, amount in rows:
            flag(conn, "CLEARED_NOT_ON_BANK", "checks_issued", cid,
                 f"Check {num} ({amount:,.2f}) was ticked cleared on {us_date(on)}, but the bank statement covering that date does not show it")
    return len(rows)


def _first_statement_day() -> date | None:
    with get_conn() as conn:
        first = conn.execute("SELECT MIN(posting_date) FROM bank_transactions").fetchone()[0]
    return date.fromisoformat(str(first)[:10]) if first else None


def cutoff_preview() -> dict:
    """What the one-time cut-off would do: {'first_day', 'before' (DataFrame: written checks dated before the first bank statement, not cleared),
    'inside' (DataFrame: dated on or after it, before today, not on any statement and not ticked: for the owner to tick or leave owed)}."""
    first = _first_statement_day()
    cols = ["check_id", "check_number", "payee", "check_date", "amount"]
    if first is None:
        return {"first_day": None, "before": pd.DataFrame(columns=cols), "inside": pd.DataFrame(columns=cols),
                "loans_before": pd.DataFrame(columns=["installment_id", "loan_ref", "due_date", "amount"]),
                "cards_before": pd.DataFrame(columns=["cc_statement_id", "card", "due_date", "amount"])}
    with get_conn() as conn:
        base = f"""SELECT c.check_id, c.check_number, cr.creditor_name AS payee, c.check_date, c.amount FROM checks_issued c
                   JOIN creditors cr ON cr.creditor_id = c.creditor_id
                   WHERE c.status = 'Active' AND {owed_sql('c')}"""
        before = pd.read_sql_query(base + " AND c.check_date < ? ORDER BY c.check_date", conn, params=(first.isoformat(),))
        inside = pd.read_sql_query(base + " AND c.check_date >= ? AND c.check_date < ? AND c.check_number IS NOT NULL AND c.check_number != '' ORDER BY c.check_date",
                                   conn, params=(first.isoformat(), manila_today().isoformat()))
        loans_before = pd.read_sql_query(f"""SELECT bli.installment_id, bl.loan_ref, bli.due_date, bli.amount FROM bank_loan_installments bli
                                             JOIN bank_loans bl ON bl.bank_loan_id = bli.bank_loan_id
                                             WHERE bl.is_cancelled = 0 AND {bank_loan_owed_sql('bli')} AND bli.due_date < ? ORDER BY bli.due_date""",
                                          conn, params=(first.isoformat(),))
        cards_before = pd.read_sql_query("""SELECT cs.cc_statement_id, cc.card_name || ' - ' || cc.card_number_last4 AS card, cs.due_date,
                                                   CASE WHEN cs.unpaid_balance_next > 0 THEN cs.unpaid_balance_next ELSE cs.total_amount_due END AS amount
                                            FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id
                                            WHERE cs.status IN ('Unpaid', 'Partially Paid') AND cs.due_date < ? ORDER BY cs.due_date""",
                                         conn, params=(first.isoformat(),))
    return {"first_day": first, "before": before, "inside": inside, "loans_before": loans_before, "cards_before": cards_before}


def apply_cutoff() -> tuple[bool, str]:
    """Once, System admin: every check dated before the first bank statement and not cleared is marked 'cleared before go-live' on its own date.
    The ones inside the statement period are left for the owner to tick (or leave owed). Audited; a database copy is made by the screen first."""
    ok, why = require_permission("settings.edit")
    if not ok:
        return False, why
    prev = cutoff_preview()
    if prev["first_day"] is None:
        return False, "Import a bank statement first: the cut-off is the day it starts."
    ids = [int(i) for i in prev["before"]["check_id"]]
    loan_ids = [int(i) for i in prev["loans_before"]["installment_id"]]
    with write_tx() as conn:
        for iid in loan_ids:                                   # bank-loan installments due before the first statement: paid then (2026-10-09)
            conn.execute("""UPDATE bank_loan_installments SET cleared_at = due_date, cleared_by = acting_user(), cleared_note = ?
                            WHERE installment_id = ? AND cleared_at IS NULL""", (CUTOFF_NOTE, iid))
        for cid in ids:
            conn.execute("""UPDATE checks_issued SET manual_cleared_at = check_date, manual_cleared_by = acting_user(), manual_cleared_note = ?
                            WHERE check_id = ? AND manual_cleared_at IS NULL AND bank_cleared_date IS NULL""", (CUTOFF_NOTE, cid))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CLEARING_CUTOFF', 'checks_issued', NULL, ?)",
                     (f"{len(ids)} check(s) and {len(loan_ids)} bank-loan payment(s) dated before {prev['first_day']} marked {CUTOFF_NOTE}; "
                      f"{len(prev['inside'])} check(s) and {len(prev['cards_before'])} old card statement(s) left for review",))
    resync(ids)
    return True, (f"{len(ids)} check(s) and {len(loan_ids)} bank-loan payment(s) dated before {prev['first_day']:%m/%d/%Y} marked {CUTOFF_NOTE}. "
                  f"{len(prev['inside'])} check(s) inside the statement period and {len(prev['cards_before'])} old card statement(s) are listed for you.")
