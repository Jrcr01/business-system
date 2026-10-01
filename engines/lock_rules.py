"""Batch locking follows the money (owner, 2026-10-01).

A batch locks itself the moment its active checks cover its net total, and reopens itself when a change to a check leaves a balance again.
There is no Lock button to remember, and printing a statement never locks. Cancelled batches are locked for good and are never touched here.

  * sync_batch_lock(statement_id)      -- called after any change that can move the balance: locks when covered, reopens when not.
  * reopen_in_tx(cur, statement_id)    -- inside a check change's own transaction: reopens a locked batch so the change can be saved.
  * lock_catchup_preview() / apply_lock_catchup() -- one-time tidy-up of batches left out of step by the old manual locking."""
from __future__ import annotations

import pandas as pd

from engines.common import get_conn, q_live
from engines.statement_rules import get_batch_balance, lock_statement, unlock_statement

COVER_TOLERANCE = 0.005          # "covered" = nothing left to pay, to the centavo


def batch_is_covered(statement_id: int) -> bool:
    """Something to pay (net total above zero), checks written against it, and nothing left over. An empty batch is NOT covered."""
    net_total, active_sum, remaining = get_batch_balance(statement_id)
    return net_total > COVER_TOLERANCE and active_sum > COVER_TOLERANCE and abs(remaining) <= COVER_TOLERANCE


def sync_batch_lock(statement_id: int | None) -> str | None:
    """Brings the lock in line with the money. Returns 'locked', 'unlocked' or None (nothing to do)."""
    if statement_id is None:
        return None
    with get_conn() as conn:
        row = conn.execute("SELECT status, is_locked FROM order_statements WHERE statement_id = ?", (statement_id,)).fetchone()
    if not row or row[0] == "Cancelled":
        return None
    is_locked = bool(row[1])
    covered = batch_is_covered(statement_id)
    if covered and not is_locked:
        ok, _ = lock_statement(statement_id, "Batch locked automatically: its checks cover the full amount")
        return "locked" if ok else None
    if not covered and is_locked:
        ok, _ = unlock_statement(statement_id, "Batch reopened automatically: it has a balance to pay again")
        return "unlocked" if ok else None
    return None


def batch_is_cancelled(cur, statement_id: int | None) -> bool:
    if statement_id is None:
        return False
    row = cur.execute("SELECT status FROM order_statements WHERE statement_id = ?", (statement_id,)).fetchone()
    return bool(row and row[0] == "Cancelled")


def reopen_in_tx(cur, statement_id: int | None, why: str) -> bool:
    """For a check change that used to be refused on a locked batch: opens the batch inside the change's own transaction (so a refused or failed change
    leaves the lock alone). The caller calls sync_batch_lock() afterwards, which closes it again if the batch is still fully covered."""
    if statement_id is None:
        return False
    row = cur.execute("SELECT status, is_locked FROM order_statements WHERE statement_id = ?", (statement_id,)).fetchone()
    if not row or row[0] == "Cancelled" or not row[1]:
        return False
    cur.execute("UPDATE order_statements SET is_locked = 0 WHERE statement_id = ?", (statement_id,))
    cur.execute(
        "INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('BATCH_UNLOCKED', 'order_statements', ?, ?)",
        (statement_id, f"Batch reopened automatically: {why}"),
    )
    return True


# --- one-time catch-up --------------------------------------------------------------------------------------------------------------------------

def _catchup_rows() -> pd.DataFrame:
    batches = q_live(
        """SELECT os.statement_id, os.statement_ref, c.creditor_name AS supplier, os.is_locked
           FROM order_statements os JOIN creditors c ON c.creditor_id = os.creditor_id
           WHERE os.status != 'Cancelled'
             AND (os.is_locked = 1 OR EXISTS (SELECT 1 FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active'))
           ORDER BY os.statement_id"""
    )
    rows = []
    for r in batches.itertuples():
        net_total, active_sum, remaining = get_batch_balance(int(r.statement_id))
        rows.append({
            "statement_id": int(r.statement_id), "statement_ref": r.statement_ref, "supplier": r.supplier, "is_locked": int(r.is_locked),
            "net_total": net_total, "active_checks": active_sum, "remaining": remaining,
            "covered": net_total > COVER_TOLERANCE and active_sum > COVER_TOLERANCE and abs(remaining) <= COVER_TOLERANCE,
        })
    return pd.DataFrame(rows, columns=["statement_id", "statement_ref", "supplier", "is_locked", "net_total", "active_checks", "remaining", "covered"])


def lock_catchup_preview() -> dict[str, pd.DataFrame]:
    """Read-only. 'to_lock': fully covered batches that are still open. 'to_unlock': locked batches that still have something to pay
    (or are over-covered, which needs fixing before they can stay closed)."""
    df = _catchup_rows()
    return {
        "to_lock": df[df["covered"] & (df["is_locked"] == 0)].reset_index(drop=True),
        "to_unlock": df[(~df["covered"]) & (df["is_locked"] == 1)].reset_index(drop=True),
    }


def apply_lock_catchup() -> dict[str, int]:
    """Applies exactly what lock_catchup_preview() lists. Take a backup first; every change is in the audit log."""
    preview = lock_catchup_preview()
    locked = unlocked = 0
    for sid in preview["to_lock"]["statement_id"]:
        ok, _ = lock_statement(int(sid), "Batch locked by the one-time catch-up: its checks cover the full amount")
        locked += int(ok)
    for sid in preview["to_unlock"]["statement_id"]:
        ok, _ = unlock_statement(int(sid), "Batch reopened by the one-time catch-up: it still has a balance to pay")
        unlocked += int(ok)
    return {"locked": locked, "unlocked": unlocked}
