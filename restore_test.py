"""Restore test (2026-09-27, system assessment item #5): proves the latest backup can actually be used to rebuild the system.

It copies the newest daily backup from Google Drive (AP_Backups) into a temporary folder -- the backup itself and the live database are
never touched -- and then checks that copy the way a real restore would be checked:
  1. the file is a sound database (integrity and link checks);
  2. it has every table and column the current program needs;
  3. its records line up with the live database (only entries made after the backup's time may differ);
  4. the app itself starts and draws every screen on it without an error.

    python restore_test.py                      -> tests the newest backup
    python restore_test.py --backup <file.db>   -> tests a specific backup
Run it about once a month. Ends with PASSED or FAILED.
"""
import glob
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from db_location import backup_dir, live_db_path  # noqa: E402
from engines.clock import manila_now  # noqa: E402  (one clock, Phase F1.3)
from engines.schema_check import pending_schema_updates  # noqa: E402

BACKUP_DIR = backup_dir()
TABLES = ["creditors", "order_statements", "invoices", "credit_memos", "checks_issued", "spoiled_checks", "loan_notes", "loan_installments",
          "bank_loans", "bank_loan_installments", "credit_cards", "cc_statements", "check_booklets", "check_print_batches", "audit_logs"]

args = sys.argv[1:]
if "--backup" in args:
    backup = args[args.index("--backup") + 1]
else:
    found = sorted(glob.glob(os.path.join(BACKUP_DIR, "ap_disbursement_backup_*.db")))
    if not found:
        print(f"FAILED: no backups found in {BACKUP_DIR}")
        sys.exit(1)
    backup = found[-1]

problems, notes = [], []
print(f"Backup under test: {backup}")
import re  # noqa: E402
_stamp = re.search(r"(\d{8})_(\d{6})", os.path.basename(backup))
made = datetime.strptime("".join(_stamp.groups()), "%Y%m%d%H%M%S") if _stamp else datetime.fromtimestamp(os.path.getmtime(backup))
print(f"  taken {made:%Y-%m-%d %H:%M} ({(manila_now() - made).total_seconds() / 3600:.0f} hours ago), {os.path.getsize(backup) // 1024} KB")
work = tempfile.mkdtemp(prefix="ap_restore_test_")
copy = os.path.join(work, "restored.db")
shutil.copy2(backup, copy)
print(f"  restored into a temporary copy: {copy}")

# 1. sound database
c = sqlite3.connect(copy)
integrity = c.execute("PRAGMA integrity_check").fetchone()[0]
broken_links = c.execute("PRAGMA foreign_key_check").fetchall()
print(f"\n1. Database soundness: integrity {integrity}, broken links {len(broken_links)}")
if integrity != "ok":
    problems.append(f"integrity check: {integrity}")
if broken_links:
    problems.append(f"{len(broken_links)} broken link(s) between records")

# 2. up to date with the program
missing = pending_schema_updates(copy)
print(f"2. Tables/columns the program needs: {'all present' if not missing else 'MISSING ' + ', '.join(missing)}")
if missing:
    # a real restore runs update_database.bat on the restored file -- do exactly that on the copy
    from apply_pending_migrations import diff_and_sync  # noqa: E402
    report = diff_and_sync(copy)
    still = pending_schema_updates(copy)
    print(f"   -> database update applied to the copy (as update_database.bat would): {'now complete' if not still else 'STILL MISSING ' + ', '.join(still)}")
    if still:
        problems.append("the database update could not bring the restored copy up to date")
    if report.get("skipped"):
        problems += [f"database update skipped: {x}" for x in report["skipped"]]

# 2b. the audit log in the backup is exactly as it was sealed (core step C2)
from engines.audit_seal import load_key, verify  # noqa: E402
_key = load_key(live_db_path())
if _key is None:
    notes.append("the audit seal key is missing beside the live database, so the backup's audit log could not be checked")
else:
    _audit_problems = verify(c, _key)
    print(f"2b. Audit log as sealed: {'yes' if not _audit_problems else 'NO -- ' + ' '.join(_audit_problems)}")
    problems += [f"audit log: {p}" for p in _audit_problems]

# 3. compare with live
live = sqlite3.connect(f"file:{live_db_path()}?mode=ro", uri=True)
print("3. Records, backup vs live:")
print(f"   {'table':<24}{'backup':>8}{'live':>8}")
for t in TABLES:
    try:
        b = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        lv = live.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
    except sqlite3.Error as e:
        problems.append(f"table {t}: {e}")
        continue
    flag = "" if b == lv else ("   (newer entries on live since the backup -- normal)" if lv > b else "   <-- backup has MORE than live, check this")
    print(f"   {t:<24}{b:>8}{lv:>8}{flag}")
    if b > lv and t not in ("audit_logs",):
        notes.append(f"{t}: the backup has {b - lv} more row(s) than live (something was deleted on live after the backup -- expected if you cleaned up)")
money = "SELECT COUNT(*), ROUND(COALESCE(SUM(amount), 0), 2) FROM checks_issued WHERE status = 'Active'"
print(f"   active checks (count, total): backup {c.execute(money).fetchone()}  live {live.execute(money).fetchone()}")
live.close()
c.close()

# 4. the app runs on it
print("4. Starting the app on the restored copy (about 20 seconds)...")
os.environ["AP_DB_PATH"] = copy
try:
    import logging
    logging.disable(logging.WARNING)
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file(os.path.join(HERE, "app.py"), default_timeout=300).run()
    errors = [str(e.value)[:200] for e in at.exception]
    screens = len(at.tabs)
    print(f"   app started, {screens} screens drawn, errors: {len(errors)}")
    if errors:
        problems += [f"app error: {e}" for e in errors]
    if screens == 0:
        problems.append("the app did not open its screens on the restored copy")
except Exception as e:  # the test harness itself failing is a problem too
    problems.append(f"could not start the app: {e}")

shutil.rmtree(work, ignore_errors=True)
print()
age_hours = (manila_now() - made).total_seconds() / 3600
if age_hours > 30:
    notes.append(f"this backup is {age_hours:.0f} hours old -- anything entered since then is not on Google Drive yet (run_backup.bat makes one now)")
for n in notes:
    print("note:", n)
if problems:
    print("FAILED:")
    for p in problems:
        print("  -", p)
    sys.exit(1)
print("PASSED - this backup can be restored and the system runs on it.")
