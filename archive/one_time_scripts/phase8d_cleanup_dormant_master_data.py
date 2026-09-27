"""Phase 8d: Deactivate/close master data with no active payables.

Phase 8a/8c brought in every real legacy payee, but most have no actual current obligation --
no cleared/postdated check, no installment. Rather than delete anything (which would lose the
legacy_payee_id/loan_ref traceability), this uses the two reversible mechanisms this app already
has for exactly this purpose:

  - SUPPLIER/LENDER creditors with no Active check referencing them at all: set
    creditors.is_active = 0 -- the same toggle already exposed in Creditor Master's "Active"
    checkbox (deactivate_creditor() in app.py). Hides them from new-batch/new-loan-note pickers
    without losing history; re-checking the box in the UI reverses it instantly.
  - BANK_LOAN placeholders with zero bank_loan_installments rows: set bank_loans.status =
    'Closed'. Not a new concept -- _sync_loan_status() (engines/bank_loan_service.py) already
    auto-reopens a Closed loan to Active the moment a real installment gets added, so this is
    self-reversing the normal way a bank loan would ever become relevant again, no manual
    "reactivate" step needed.

A lender's loan_note itself is left alone (still "Pending Setup") -- only the creditor's
is_active flag changes, consistent with how a real Creditor Master deactivation already works
for every other track in this app.

Run directly (defaults to the TEST db, like phase8a/8c):

    python phase8d_cleanup_dormant_master_data.py
"""
import os
import sqlite3
from datetime import date

import phase8a_master_data_setup as p8a

PROJECT_ROOT = p8a.PROJECT_ROOT
TEST_DB_PATH = p8a.TEST_DB_PATH
REPORT_PATH = os.path.join(PROJECT_ROOT, "report_8d.txt")


def cleanup_dormant_master_data(conn: sqlite3.Connection) -> dict:
    supplier_ids = [r[0] for r in conn.execute(
        "SELECT creditor_id FROM creditors WHERE creditor_type = 'SUPPLIER' AND is_active = 1 "
        "AND creditor_id NOT IN (SELECT creditor_id FROM checks_issued WHERE status = 'Active')"
    )]
    lender_ids = [r[0] for r in conn.execute(
        "SELECT c.creditor_id FROM creditors c JOIN loan_notes ln ON ln.creditor_id = c.creditor_id "
        "WHERE c.creditor_type = 'LENDER' AND c.is_active = 1 AND ln.loan_note_id NOT IN "
        "(SELECT loan_note_id FROM checks_issued WHERE status = 'Active' AND loan_note_id IS NOT NULL)"
    )]
    for creditor_id in supplier_ids + lender_ids:
        conn.execute("UPDATE creditors SET is_active = 0 WHERE creditor_id = ?", (creditor_id,))

    bank_loan_ids = [r[0] for r in conn.execute(
        "SELECT bank_loan_id FROM bank_loans WHERE status = 'Active' "
        "AND bank_loan_id NOT IN (SELECT bank_loan_id FROM bank_loan_installments)"
    )]
    for bank_loan_id in bank_loan_ids:
        conn.execute("UPDATE bank_loans SET status = 'Closed' WHERE bank_loan_id = ?", (bank_loan_id,))

    conn.commit()
    return {
        "suppliers_deactivated": len(supplier_ids),
        "lenders_deactivated": len(lender_ids),
        "bank_loans_closed": len(bank_loan_ids),
    }


def write_report(stats: dict, path: str = REPORT_PATH) -> None:
    lines = [
        "=" * 78, "PHASE 8d: DORMANT MASTER DATA CLEANUP -- REPORT",
        f"Run date: {date.today().isoformat()}", "=" * 78, "",
        f"Suppliers deactivated (no Active check on file):    {stats['suppliers_deactivated']}",
        f"Lenders deactivated (no Active check on file):      {stats['lenders_deactivated']}",
        f"Bank loans closed (no installments on file):        {stats['bank_loans_closed']}",
        "",
        "All reversible: re-check \"Active\" in Creditor Master for a supplier/lender, or add a",
        "real installment to a bank loan (auto-reopens it to Active).",
        "=" * 78,
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    conn = sqlite3.connect(TEST_DB_PATH)
    try:
        stats = cleanup_dormant_master_data(conn)
    finally:
        conn.close()
    write_report(stats)
    print(f"Applied against {TEST_DB_PATH}")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
