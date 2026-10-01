"""Beginning balance (from MS Access) (owner, 2026-09-30: "there should be a beginning balance instead, no archiving").

The batches brought over from MS Access hold checks but no invoices (the import set each batch total equal to its checks), so every all-time report showed
invoices <> checks issued. Each such batch gets ONE flagged invoice line, "Beginning balance (from MS Access)", equal to its total and tied to its active
checks, so invoices = checks. Nothing already recorded changes: no check, batch total or status is touched. The line is flagged (invoices.is_opening_balance)
so aging, days-to-pay, the overview's invoice count, the saved-report invoice list and the duplicate / vendor-statement matchers leave it out; balances,
histories and batch statements show it as a balance. Only batches whose net total already equals their active checks are done; any other is listed for the owner."""
from __future__ import annotations

import os
import sqlite3

import pandas as pd

from engines.statement_rules import sync_invoice_payment_status

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

BEGINNING_REF = "Beginning balance (from MS Access)"
COLUMNS = ["statement_id", "batch", "supplier", "batch_total", "checks_total", "checks", "ok", "note"]

_ELIGIBLE_SQL = """
SELECT os.statement_id, os.statement_ref AS batch, cr.creditor_name AS supplier, os.creditor_id, os.total_amount,
       os.total_amount - COALESCE((SELECT SUM(m.amount) FROM credit_memos m WHERE m.statement_id = os.statement_id), 0) AS net_total,
       DATE(os.created_at, 'localtime') AS created,
       COALESCE((SELECT SUM(k.amount) FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active'), 0) AS checks_total,
       (SELECT COUNT(*) FROM checks_issued k WHERE k.statement_id = os.statement_id AND k.status = 'Active') AS checks
FROM order_statements os JOIN creditors cr ON cr.creditor_id = os.creditor_id
WHERE os.statement_ref LIKE 'LEGACY-%' AND os.status != 'Cancelled'
  AND NOT EXISTS (SELECT 1 FROM invoices i WHERE i.statement_id = os.statement_id)
ORDER BY cr.creditor_name, os.statement_id
"""


def _rows(conn) -> list[dict]:
    conn.row_factory = sqlite3.Row
    out = []
    for r in conn.execute(_ELIGIBLE_SQL):
        total, net, checks_total = float(r["total_amount"] or 0), float(r["net_total"] or 0), float(r["checks_total"] or 0)
        ok = total > 0.005 and abs(net - checks_total) < 0.005
        note = "" if ok else (f"Batch total PHP {total:,.2f} (net {net:,.2f}) does not equal its active checks PHP {checks_total:,.2f} — left for you to look at." if total > 0.005
                              else "Batch has no total.")
        out.append({"statement_id": int(r["statement_id"]), "batch": r["batch"], "supplier": r["supplier"], "creditor_id": int(r["creditor_id"]), "batch_total": total,
                    "checks_total": checks_total, "checks": int(r["checks"]), "ok": ok, "note": note, "created": r["created"]})
    return out


def preview_beginning_balances() -> pd.DataFrame:
    """Every legacy batch that has no invoice yet, with what would be added (the batch total) and whether it can be done as is. Writes nothing."""
    with sqlite3.connect(DB_PATH) as conn:
        rows = _rows(conn)
    return pd.DataFrame(rows, columns=COLUMNS + ["creditor_id", "created"])[COLUMNS] if rows else pd.DataFrame(columns=COLUMNS)


def apply_beginning_balances() -> tuple[bool, str]:
    """Add the beginning-balance line to every legacy batch that is ready (one transaction), tie each active check to it, and audit each batch.
    Safe to run again: a batch that already has an invoice is skipped."""
    added, total_added, skipped = [], 0.0, 0
    with sqlite3.connect(DB_PATH) as conn:
        rows = _rows(conn)
        for r in rows:
            if not r["ok"]:
                skipped += 1
                continue
            sid, cid, amount = r["statement_id"], r["creditor_id"], round(r["batch_total"], 2)
            doc_ref = BEGINNING_REF
            if conn.execute("SELECT 1 FROM invoices WHERE creditor_id = ? AND doc_ref = ?", (cid, doc_ref)).fetchone():
                doc_ref = f"{BEGINNING_REF} {r['batch']}"
            inv_date = r["created"] or "2026-09-19"
            cur = conn.execute("""INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, trade_discounts, amount, status, statement_id, is_opening_balance)
                                  VALUES (?, ?, ?, ?, ?, '[]', ?, 'Unpaid', ?, 1)""", (cid, doc_ref, inv_date, inv_date, amount, amount, sid))
            invoice_id = cur.lastrowid
            for check_id, check_amount in conn.execute("SELECT check_id, amount FROM checks_issued WHERE statement_id = ? AND status = 'Active'", (sid,)).fetchall():
                conn.execute("INSERT INTO invoice_check_allocations (invoice_id, check_id, amount) VALUES (?, ?, ?)", (invoice_id, check_id, round(float(check_amount), 2)))
            conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('BEGINNING_BALANCE_ADDED', 'invoices', ?, ?)",
                         (invoice_id, f"{BEGINNING_REF}: PHP {amount:,.2f} added to batch {r['batch']} ({r['supplier']}), tied to its {r['checks']} active check(s); no check or batch total changed"))
            added.append(sid)
            total_added += amount
    for sid in added:
        sync_invoice_payment_status(sid)
    msg = f"Added the beginning-balance line to {len(added)} batch(es), PHP {total_added:,.2f} in all."
    if skipped:
        msg += f" {skipped} batch(es) were left because their total does not equal their checks."
    return True, msg
