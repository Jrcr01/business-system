"""Moves the LIVE database out of the Google Drive folder into a plain local folder, so Drive's
syncing can never touch the file while the app is writing to it. Backups (backup_live_db.py) still
go to Drive.

    python move_database_out_of_drive.py --dry-run     # show what it would do
    python move_database_out_of_drive.py               # move it to C:\\AP_Data

What it does, in order (and stops at the first problem, leaving everything as it was):
  1. refuses to run while the live app is on (port 8501) -- use "Stop AP System" first
  2. copies the database to the new folder and checks the copy is identical (integrity check +
     row counts of the main tables)
  3. writes live_db_path.txt so the app and every script now use the new location
  4. renames the OLD file to ap_disbursement.db.moved_<time> as a safety net (delete it later)
"""
import argparse
import os
import shutil
import socket
import sqlite3
import sys
from datetime import datetime

import db_location

DEFAULT_DEST_DIR = r"C:\AP_Data"
TABLES = ["creditors", "checks_issued", "order_statements", "invoices", "loan_notes", "bank_loans",
          "bank_loan_installments", "credit_cards", "cc_statements", "audit_logs"]


def app_is_running(port: int = 8501) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def counts(path: str) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in TABLES}
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dest-dir", default=DEFAULT_DEST_DIR)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--skip-running-check", action="store_true", help="for testing only")
    a = ap.parse_args()

    src = db_location.live_db_path()
    dest = os.path.join(a.dest_dir, "ap_disbursement.db")
    print(f"Current database : {src}")
    print(f"New location     : {dest}")
    if os.path.abspath(src) == os.path.abspath(dest):
        print("Already there -- nothing to do."); return 0
    if not os.path.exists(src):
        print("ERROR: current database not found."); return 1
    if not a.skip_running_check and app_is_running():
        print("ERROR: the live app is running. Double-click 'Stop AP System' first, then run this again."); return 1
    if os.path.exists(dest):
        print(f"ERROR: {dest} already exists -- not overwriting it."); return 1
    if a.dry_run:
        print("Dry run -- nothing changed."); return 0

    os.makedirs(a.dest_dir, exist_ok=True)
    shutil.copy2(src, dest)
    conn = sqlite3.connect(dest)
    ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    conn.close()
    if not ok or counts(src) != counts(dest):
        os.remove(dest)
        print("ERROR: the copy did not match the original -- removed it, nothing was changed."); return 1
    print("Copy verified (integrity OK, all table row counts identical).")

    with open(db_location.POINTER, "w", encoding="utf-8") as f:
        f.write(dest + "\n")
    print(f"Wrote {db_location.POINTER}")
    old = f"{src}.moved_{datetime.now():%Y%m%d_%H%M%S}"
    os.rename(src, old)
    print(f"Old file kept as {old}")
    print("Done. Start the app with 'Start AP System' and check your data is all there.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
