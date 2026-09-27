"""Deletes ONE bank loan (and its schedule rows) from the live database -- for a duplicate entry.

    python delete_duplicate_bank_loan.py --id 19 --dry-run   # show what would go, change nothing
    python delete_duplicate_bank_loan.py --id 19             # backs up first, then deletes

Exit code 2 = that loan is already gone (not an error).

If the loan was entered under its own one-off payee name (e.g. a payee called after the loan
number) and that payee is used nowhere else, the empty payee is removed too.
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

from db_location import live_db_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", type=int, required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    path = a.db or live_db_path()
    if not os.path.exists(path):
        print("ERROR: no database at", path); return 1
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    row = conn.execute(
        "SELECT b.loan_ref, b.creditor_id, c.creditor_name FROM bank_loans b JOIN creditors c USING (creditor_id) WHERE b.bank_loan_id = ?",
        (a.id,),
    ).fetchone()
    if not row:
        print(f"Loan {a.id} is not on file (already deleted) -- nothing to do."); return 2
    ref, creditor_id, creditor_name = row
    n = conn.execute("SELECT COUNT(*) FROM bank_loan_installments WHERE bank_loan_id = ?", (a.id,)).fetchone()[0]
    others = conn.execute("SELECT COUNT(*) FROM bank_loans WHERE creditor_id = ? AND bank_loan_id != ?", (creditor_id, a.id)).fetchone()[0]
    print(f"Database: {path}")
    print(f"Loan {a.id}: {ref}  (payee: {creditor_name})  -- {n} schedule row(s)")
    if a.dry_run:
        print("Dry run -- nothing changed."); return 0
    backup = f"{path}.bak_pre_delete_loan_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(path, backup); print("Backed up to", backup)
    with conn:
        conn.execute("DELETE FROM bank_loan_installments WHERE bank_loan_id = ?", (a.id,))
        conn.execute("DELETE FROM bank_loans WHERE bank_loan_id = ?", (a.id,))
    print("Deleted the loan and its schedule.")
    if not others:
        try:
            with conn:
                conn.execute("DELETE FROM creditors WHERE creditor_id = ?", (creditor_id,))
            print(f"Also removed the now-empty payee '{creditor_name}'.")
        except sqlite3.IntegrityError:
            print(f"Payee '{creditor_name}' is used elsewhere, so it was kept.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
