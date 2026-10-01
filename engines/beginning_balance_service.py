"""Beginning balance (from MS Access) (owner, 2026-09-30: "there should be a beginning balance instead, no archiving").

The batches brought over from MS Access hold checks but no invoices (the import set each batch total equal to its checks), so every all-time report showed
invoices <> checks issued. Each such batch gets ONE flagged invoice line, "Beginning balance (from MS Access)", equal to its total and tied to its active
checks, so invoices = checks. Nothing already recorded changes: no check, batch total or status is touched. The line is flagged (invoices.is_opening_balance)
so aging, days-to-pay, the overview's invoice count, the saved-report invoice list and the duplicate / vendor-statement matchers leave it out; balances,
histories and batch statements show it as a balance. Only batches whose net total already equals their active checks are done; any other is listed for the owner."""
from __future__ import annotations

from engines.common import get_conn
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
       DATE(os.created_at, '+8 hours') AS created,
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
    with get_conn(DB_PATH) as conn:
        rows = _rows(conn)
    return pd.DataFrame(rows, columns=COLUMNS + ["creditor_id", "created"])[COLUMNS] if rows else pd.DataFrame(columns=COLUMNS)


def apply_beginning_balances() -> tuple[bool, str]:
    """Add the beginning-balance line to every legacy batch that is ready (one transaction), tie each active check to it, and audit each batch.
    Safe to run again: a batch that already has an invoice is skipped."""
    added, total_added, skipped = [], 0.0, 0
    with get_conn(DB_PATH) as conn:
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
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BEGINNING_BALANCE_ADDED', 'invoices', ?, ?)",
                         (invoice_id, f"{BEGINNING_REF}: PHP {amount:,.2f} added to batch {r['batch']} ({r['supplier']}), tied to its {r['checks']} active check(s); no check or batch total changed"))
            added.append(sid)
            total_added += amount
    for sid in added:
        sync_invoice_payment_status(sid)
    msg = f"Added the beginning-balance line to {len(added)} batch(es), PHP {total_added:,.2f} in all."
    if skipped:
        msg += f" {skipped} batch(es) were left because their total does not equal their checks."
    return True, msg


# ---------------------------------------------------------------------------------------------------------------------------------------
# The same label on migrated loans (owner, 2026-10-01: "label the loan notes and bank loans too").
# ---------------------------------------------------------------------------------------------------------------------------------------
LABEL = BEGINNING_REF
LABEL_COLUMNS = ["kind", "id", "ref", "holder"]
_TABLES = {"loan_note": ("loan_notes", "loan_note_id", "note_ref"), "bank_loan": ("bank_loans", "bank_loan_id", "loan_ref")}


def loan_label_candidates() -> pd.DataFrame:
    """Loan notes still named PENDING-... (the import's placeholders) and bank loans imported as a schedule only (no start date and no principal) that
    are not labelled yet and have never had a label removed on purpose."""
    with get_conn(DB_PATH) as conn:
        notes = conn.execute("""SELECT 'loan_note', ln.loan_note_id, ln.note_ref, cr.creditor_name FROM loan_notes ln JOIN creditors cr ON cr.creditor_id = ln.creditor_id
                                WHERE ln.note_ref LIKE 'PENDING-%' AND ln.status != 'Cancelled' AND ln.is_beginning_balance = 0 AND ln.beginning_label_removed = 0
                                ORDER BY ln.loan_note_id""").fetchall()
        banks = conn.execute("""SELECT 'bank_loan', bl.bank_loan_id, bl.loan_ref, cr.creditor_name FROM bank_loans bl JOIN creditors cr ON cr.creditor_id = bl.creditor_id
                                WHERE bl.start_date IS NULL AND bl.principal_amount IS NULL AND bl.is_cancelled = 0 AND bl.is_beginning_balance = 0
                                  AND bl.beginning_label_removed = 0 ORDER BY bl.bank_loan_id""").fetchall()
    return pd.DataFrame(notes + banks, columns=LABEL_COLUMNS)


def apply_loan_labels() -> tuple[bool, str]:
    """Tag every candidate. Only the label is set: no amount, status, schedule or check changes. Audited per record. Safe to run again."""
    cands = loan_label_candidates()
    with get_conn(DB_PATH) as conn:
        for r in cands.itertuples():
            table, pk, _ref = _TABLES[r.kind]
            conn.execute(f"UPDATE {table} SET is_beginning_balance = 1 WHERE {pk} = ?", (int(r.id),))
            conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BEGINNING_BALANCE_LABELLED', ?, ?, ?)",
                         (table, int(r.id), f"{r.ref} ({r.holder}) labelled \"{LABEL}\" — interest / terms not split out yet; nothing else changed"))
    n_notes = int((cands["kind"] == "loan_note").sum()) if not cands.empty else 0
    n_banks = int((cands["kind"] == "bank_loan").sum()) if not cands.empty else 0
    return True, f"Labelled {n_notes} loan note(s) and {n_banks} bank loan(s)."


def remove_beginning_label(kind: str, record_id: int) -> tuple[bool, str]:
    """Take the label off by hand (real terms are known). It is never put back."""
    if kind not in _TABLES:
        return False, "Unknown record type."
    table, pk, ref_col = _TABLES[kind]
    with get_conn(DB_PATH) as conn:
        row = conn.execute(f"SELECT {ref_col}, is_beginning_balance FROM {table} WHERE {pk} = ?", (int(record_id),)).fetchone()
        if not row:
            return False, "Record not found."
        if not row[1]:
            return False, f"{row[0]} has no beginning-balance label."
        conn.execute(f"UPDATE {table} SET is_beginning_balance = 0, beginning_label_removed = 1 WHERE {pk} = ?", (int(record_id),))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), 'BEGINNING_BALANCE_LABEL_REMOVED', ?, ?, ?)",
                     (table, int(record_id), f"{row[0]}: beginning-balance label removed by hand (real terms known)"))
    return True, f"{row[0]}: label removed."
