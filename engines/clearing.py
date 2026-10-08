"""When is a check paid? (owner, 2026-10-08) Only when it is CLEARED: the bank cleared it (an imported bank statement), someone ticked it cleared, or it
was settled outside the bank (the payee returned it and was paid otherwise). A check whose date has passed is NOT paid by that alone: until it clears it
stays owed ("waiting for the bank"). This replaces the old date rule (a past-dated check counted as paid).

Internal control: a tick is checked against the bank. When a bank statement is imported, a ticked check that the statement shows gets the bank's date
(final); a ticked check that a statement covering its tick date does NOT show goes on the Review list (CLEARED_NOT_ON_BANK).

  * cleared_sql(alias) / owed_sql(alias)       -- the one SQL condition every report and rule uses
  * mark_cleared / unmark_cleared              -- the manual tick
  * waiting_for_bank()                         -- written checks not cleared yet
  * check_ticks_against_bank()                 -- run after a bank statement import
  * cutoff_preview() / apply_cutoff()          -- once, at go-live: checks before the first bank statement are "cleared before go-live" """
from __future__ import annotations

from datetime import date

import pandas as pd

from engines.clock import manila_today
from engines.common import get_conn, write_tx
from engines.permissions import require as require_permission

CUTOFF_NOTE = "cleared before go-live"


def cleared_sql(alias: str = "c") -> str:
    p = f"{alias}." if alias else ""
    return f"({p}bank_cleared_date IS NOT NULL OR {p}manual_cleared_at IS NOT NULL OR {p}settled_outside_at IS NOT NULL)"


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


def _audit(conn, action: str, check_id: int, text: str) -> None:
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'checks_issued', ?, ?)",
                 (action, check_id, text))


def mark_cleared(check_id: int, on: date | None = None, note: str = "") -> tuple[bool, str]:
    """The manual tick: the check is paid from `on` (default today). Refused for a check the bank already cleared, a voided one, or one with no number."""
    ok, why = require_permission("bank.use")
    if not ok:
        return False, why
    on = on or manila_today()
    if on > manila_today():
        return False, "A check can't be cleared on a future date."
    with write_tx() as conn:
        row = conn.execute("SELECT check_number, status, bank_cleared_date, manual_cleared_at FROM checks_issued WHERE check_id = ?", (check_id,)).fetchone()
        if not row:
            return False, "Check not found."
        number, status, bank, manual = row
        if status != "Active":
            return False, f"This check is {status.lower()}, not active."
        if not number:
            return False, "This check has no number yet: it can't have cleared."
        if bank:
            return False, f"The bank already cleared this check on {str(bank)[:10]}."
        conn.execute("UPDATE checks_issued SET manual_cleared_at = ?, manual_cleared_by = acting_user(), manual_cleared_note = ? WHERE check_id = ?",
                     (on.isoformat(), (note or "").strip() or None, check_id))
        _audit(conn, "CHECK_MARKED_CLEARED", check_id, f"{number} cleared {on:%b %d, %Y}" + (f" ({note.strip()})" if (note or "").strip() else ""))
    resync([check_id])
    return True, f"Check {number} marked cleared on {on:%b %d, %Y}. The bank statement will confirm it."


def unmark_cleared(check_id: int) -> tuple[bool, str]:
    ok, why = require_permission("bank.use")
    if not ok:
        return False, why
    with write_tx() as conn:
        row = conn.execute("SELECT check_number, manual_cleared_at FROM checks_issued WHERE check_id = ?", (check_id,)).fetchone()
        if not row or not row[1]:
            return False, "This check isn't ticked cleared."
        conn.execute("UPDATE checks_issued SET manual_cleared_at = NULL, manual_cleared_by = NULL, manual_cleared_note = NULL WHERE check_id = ?", (check_id,))
        _audit(conn, "CHECK_UNMARKED_CLEARED", check_id, f"{row[0]}: the cleared tick of {str(row[1])[:10]} taken back")
    resync([check_id])
    return True, f"Check {row[0]} is owed again (waiting for the bank)."


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
                 f"Check {num} ({amount:,.2f}) was ticked cleared on {str(on)[:10]}, but the bank statement covering that date does not show it")
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
        return {"first_day": None, "before": pd.DataFrame(columns=cols), "inside": pd.DataFrame(columns=cols)}
    with get_conn() as conn:
        base = f"""SELECT c.check_id, c.check_number, cr.creditor_name AS payee, c.check_date, c.amount FROM checks_issued c
                   JOIN creditors cr ON cr.creditor_id = c.creditor_id
                   WHERE c.status = 'Active' AND {owed_sql('c')}"""
        before = pd.read_sql_query(base + " AND c.check_date < ? ORDER BY c.check_date", conn, params=(first.isoformat(),))
        inside = pd.read_sql_query(base + " AND c.check_date >= ? AND c.check_date < ? AND c.check_number IS NOT NULL AND c.check_number != '' ORDER BY c.check_date",
                                   conn, params=(first.isoformat(), manila_today().isoformat()))
    return {"first_day": first, "before": before, "inside": inside}


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
    with write_tx() as conn:
        for cid in ids:
            conn.execute("""UPDATE checks_issued SET manual_cleared_at = check_date, manual_cleared_by = acting_user(), manual_cleared_note = ?
                            WHERE check_id = ? AND manual_cleared_at IS NULL AND bank_cleared_date IS NULL""", (CUTOFF_NOTE, cid))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CLEARING_CUTOFF', 'checks_issued', NULL, ?)",
                     (f"{len(ids)} check(s) dated before {prev['first_day']} marked {CUTOFF_NOTE}; {len(prev['inside'])} left for review",))
    resync(ids)
    return True, f"{len(ids)} check(s) dated before {prev['first_day']:%b %d, %Y} marked {CUTOFF_NOTE}. {len(prev['inside'])} check(s) inside the statement period are listed for you."
