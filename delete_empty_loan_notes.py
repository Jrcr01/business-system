"""Deletes loan notes that have NO checks on record (the empty 'Pending Setup' placeholders) and keeps every
note that has checks. Same rule as the app's 'Delete loan note' button, done in one go, with a backup first.

    python delete_empty_loan_notes.py           -> only shows what would be deleted (changes nothing)
    python delete_empty_loan_notes.py --apply   -> makes a backup copy, then deletes them
The lender (creditor) records stay; only the empty loan notes go. Optional: --db <path>."""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

args = sys.argv[1:]
db = args[args.index("--db") + 1] if "--db" in args else r"C:\AP_Data\ap_disbursement.db"
apply = "--apply" in args

conn = sqlite3.connect(db)
rows = conn.execute(
    """SELECT ln.loan_note_id, ln.note_ref, cr.creditor_name, ln.status,
              (SELECT COUNT(*) FROM loan_installments i WHERE i.loan_note_id = ln.loan_note_id)
       FROM loan_notes ln JOIN creditors cr USING (creditor_id)
       WHERE NOT EXISTS (SELECT 1 FROM checks_issued k WHERE k.loan_note_id = ln.loan_note_id) ORDER BY 1"""
).fetchall()
kept = conn.execute("SELECT COUNT(*) FROM loan_notes").fetchone()[0] - len(rows)
print(f"Loan notes with no checks (to delete): {len(rows)}   |   loan notes with checks (kept): {kept}")
for r in rows:
    print(f"  #{r[0]}  {r[1]}  {r[2]}  [{r[3]}]  schedule rows: {r[4]}")
if not apply:
    print("\nNothing changed. Run again with --apply to delete them.")
    sys.exit(0)
if not rows:
    print("Nothing to delete.")
    sys.exit(0)
backup = db + f".before_delete_loan_notes_{datetime.now():%Y%m%d_%H%M%S}"
shutil.copy2(db, backup)
print("Backup saved:", backup)
for r in rows:
    conn.execute("DELETE FROM loan_installments WHERE loan_note_id = ?", (r[0],))
    conn.execute("DELETE FROM loan_notes WHERE loan_note_id = ?", (r[0],))
    conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('LOAN_NOTE_DELETED', 'loan_notes', ?, ?)",
                 (r[0], f"Deleted loan note {r[1]} ({r[2]}) with no checks on record"))
conn.commit()
print("Integrity:", conn.execute("PRAGMA integrity_check").fetchone()[0])
print(f"Deleted {len(rows)} loan note(s). Kept {conn.execute('SELECT COUNT(*) FROM loan_notes').fetchone()[0]}.")
