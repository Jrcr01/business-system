"""PDC release (stage B5, 2026-10-02). A post-dated check goes through three hands: it is printed, the supplier COLLECTS it (release), and later the bank clears it.
Until now the system knew the first and the last. This records the middle one, so the owner can see which printed checks are still in the office and which a supplier
already holds (and can therefore deposit on the check date).

  * for_release()           -- printed checks nobody has collected yet (handwritten ones are handed over when recorded, so they never wait)
  * mark_released(ids, ...) -- the supplier collected them: the date and who took them
  * undo_release(id)        -- a mistake, until the bank has cleared the check
  * recent_releases() / release_summary()"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from engines.permissions import require as require_permission
from engines.clock import manila_today
from engines.common import get_conn, write_tx

WAITING = """c.status = 'Active' AND c.printed_at IS NOT NULL AND c.is_handwritten = 0 AND c.released_date IS NULL
             AND c.bank_cleared_date IS NULL AND c.settled_outside_at IS NULL"""


def _day(value) -> date | None:
    if value is None or value == "":
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value)[:10])


def for_release(today: date | None = None) -> pd.DataFrame:
    """Columns: check_id, supplier, check_number, check_date, amount, printed_on, days_waiting. Oldest printed first."""
    today = today or manila_today()
    cols = ["check_id", "supplier", "check_number", "check_date", "amount", "printed_on", "days_waiting"]
    with get_conn() as conn:
        df = pd.read_sql_query(
            f"""SELECT c.check_id, cr.creditor_name AS supplier, c.check_number, c.check_date, c.amount, DATE(c.printed_at) AS printed_on
                FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id WHERE {WAITING} ORDER BY c.printed_at, c.check_id""", conn)
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["days_waiting"] = df["printed_on"].map(lambda d: max((today - _day(d)).days, 0))
    return df[cols]


def release_summary(today: date | None = None) -> dict:
    df = for_release(today)
    return {"waiting": len(df), "amount": float(df["amount"].sum()) if not df.empty else 0.0, "oldest_days": int(df["days_waiting"].max()) if not df.empty else 0}


def mark_released(check_ids: list[int], released_date, released_to: str = "") -> tuple[bool, str]:
    """Records that the supplier collected these checks. All or nothing: an unknown, unprinted, cancelled, handwritten or already released check stops the save."""
    _ok, _msg = require_permission('check.release')
    if not _ok:
        return False, _msg
    ids = [int(c) for c in check_ids or []]
    when = _day(released_date)
    who = " ".join(str(released_to or "").split()) or None
    if not ids:
        return False, "Tick at least one check."
    if when is None:
        return False, "Enter the date the checks were collected."
    with write_tx() as conn:
        found = []
        for cid in ids:
            row = conn.execute(
                "SELECT c.check_number, c.amount, c.status, c.printed_at, c.is_handwritten, c.released_date, cr.creditor_name FROM checks_issued c "
                "JOIN creditors cr ON cr.creditor_id = c.creditor_id WHERE c.check_id = ?", (cid,)).fetchone()
            if not row:
                return False, f"Check {cid} was not found."
            number, amount, status, printed_at, handwritten, released, supplier = row
            label = f"{supplier} check {number or cid}"
            if status != "Active":
                return False, f"{label} is {status.lower()}, so it cannot be released."
            if handwritten:
                return False, f"{label} was written by hand and is handed over when recorded."
            if printed_at is None:
                return False, f"{label} has not been printed yet."
            if released is not None:
                return False, f"{label} was already released on {released}."
            if when < _day(printed_at):
                return False, f"{label}: the collection date cannot be before it was printed ({str(printed_at)[:10]})."
            found.append((cid, label, float(amount)))
        for cid, label, amount in found:
            conn.execute("UPDATE checks_issued SET released_date = ?, released_to = ? WHERE check_id = ?", (str(when), who, cid))
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECK_RELEASED', 'checks_issued', ?, ?)",
                         (cid, f"{label} for {amount:,.2f} released on {when}" + (f" to {who}" if who else "")))
    return True, f"{len(found)} check(s) released" + (f" to {who}" if who else "") + f" on {when:%b %d, %Y}."


def undo_release(check_id: int) -> tuple[bool, str]:
    """Takes a release back (it was marked by mistake). Refused once the bank has cleared the check."""
    _ok, _msg = require_permission('check.release')
    if not _ok:
        return False, _msg
    with write_tx() as conn:
        row = conn.execute("SELECT released_date, released_to, bank_cleared_date, check_number FROM checks_issued WHERE check_id = ?", (int(check_id),)).fetchone()
        if not row:
            return False, "Check not found."
        if row[0] is None:
            return False, "This check was not released."
        if row[2] is not None:
            return False, "The bank has already cleared this check, so its release can't be undone."
        conn.execute("UPDATE checks_issued SET released_date = NULL, released_to = NULL WHERE check_id = ?", (int(check_id),))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values) VALUES (acting_user(), 'CHECK_RELEASE_UNDONE', 'checks_issued', ?, ?)",
                     (int(check_id), f"check {row[3] or check_id}: was released {row[0]}" + (f" to {row[1]}" if row[1] else "")))
    return True, "Release undone: the check is waiting for release again."


def recent_releases(days: int = 30, today: date | None = None) -> pd.DataFrame:
    """Checks released in the last `days` days, newest first: check_id, supplier, check_number, check_date, amount, released_date, released_to, cleared (the bank has it)."""
    today = today or manila_today()
    since = today - timedelta(days=days)
    with get_conn() as conn:
        return pd.read_sql_query(
            """SELECT c.check_id, cr.creditor_name AS supplier, c.check_number, c.check_date, c.amount, c.released_date, c.released_to,
                      CASE WHEN c.bank_cleared_date IS NOT NULL THEN 1 ELSE 0 END AS cleared
               FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
               WHERE c.released_date IS NOT NULL AND c.released_date >= ? ORDER BY c.released_date DESC, c.check_id DESC""", conn, params=(str(since),))


def preview_release_before(cutoff) -> dict:
    """One-time catch-up, read-only: how many printed checks (and how much) were printed on or before `cutoff` and so are assumed already collected."""
    day = _day(cutoff)
    df = for_release()
    old = df[df["printed_on"].map(lambda d: _day(d) <= day)] if not df.empty and day else df.iloc[0:0]
    return {"checks": len(old), "amount": float(old["amount"].sum()) if not old.empty else 0.0}


def release_before(cutoff, note: str = "before release tracking") -> tuple[bool, str]:
    """One-time catch-up: every check printed on or before `cutoff` and still waiting is marked released ON ITS PRINT DATE, 'collected by' = the note. For checks that
    were already with their suppliers when release tracking began. One audit line for the whole batch."""
    _ok, _msg = require_permission('check.release')
    if not _ok:
        return False, _msg
    day = _day(cutoff)
    if day is None:
        return False, "Pick the cutoff date."
    with write_tx() as conn:
        n, amount = conn.execute(f"SELECT COUNT(*), COALESCE(SUM(c.amount), 0) FROM checks_issued c WHERE {WAITING} AND DATE(c.printed_at) <= ?", (str(day),)).fetchone()
        if not n:
            return False, "No waiting check was printed on or before that date."
        conn.execute(f"UPDATE checks_issued AS c SET released_date = DATE(c.printed_at), released_to = ? WHERE {WAITING} AND DATE(c.printed_at) <= ?", (note, str(day)))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'CHECKS_RELEASED_BULK', 'checks_issued', NULL, ?)",
                     (f"{n} check(s) for {amount:,.2f} printed on or before {day} marked as already with their suppliers ({note})",))
    return True, f"{n} check(s) marked as already collected."
