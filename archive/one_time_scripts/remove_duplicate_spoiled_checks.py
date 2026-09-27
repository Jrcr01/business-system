"""Removes duplicate 'spoiled check' log entries (the same check number logged more than once), keeping the FIRST entry of each number.

    python remove_duplicate_spoiled_checks.py           -> only shows what would be removed (changes nothing)
    python remove_duplicate_spoiled_checks.py --apply   -> makes a backup copy, then removes them
Optional: --db <path>."""
import shutil
import sqlite3
import sys
from datetime import datetime

args = sys.argv[1:]
db = args[args.index("--db") + 1] if "--db" in args else r"C:\AP_Data\ap_disbursement.db"
apply = "--apply" in args
conn = sqlite3.connect(db)
dups = conn.execute(
    """SELECT s.spoiled_id, s.bank_account_id, s.check_number, s.logged_at FROM spoiled_checks s
       WHERE s.spoiled_id > (SELECT MIN(x.spoiled_id) FROM spoiled_checks x WHERE x.bank_account_id = s.bank_account_id AND x.check_number = s.check_number)
       ORDER BY s.spoiled_id"""
).fetchall()
print(f"Duplicate spoiled entries to remove: {len(dups)} (the first entry of each number is kept)")
for d in dups:
    print(f"  entry #{d[0]}  check {d[2]}  logged {d[3]}")
if not apply:
    print("\nNothing changed. Run again with --apply to remove them.")
    sys.exit(0)
if not dups:
    print("Nothing to remove.")
    sys.exit(0)
backup = db + f".before_dedupe_spoiled_{datetime.now():%Y%m%d_%H%M%S}"
shutil.copy2(db, backup)
print("Backup saved:", backup)
for d in dups:
    conn.execute("DELETE FROM spoiled_checks WHERE spoiled_id = ?", (d[0],))
    conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, old_values) VALUES ('SPOILED_DUPLICATE_REMOVED', 'spoiled_checks', ?, ?)",
                 (d[0], f"duplicate log of check {d[2]} removed"))
conn.commit()
print("Integrity:", conn.execute("PRAGMA integrity_check").fetchone()[0])
print(f"Removed {len(dups)} duplicate entr(y/ies).")
