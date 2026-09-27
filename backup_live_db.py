"""Daily backup of the live AP database.

Uses SQLite's own backup routine, which gives a consistent copy even while the app is open, then
checks the copy is readable. Keeps the newest 30 backups and deletes older ones.

    python backup_live_db.py                      # live db -> H:\\My Drive\\Claude9OS\\AP_Backups
    python backup_live_db.py --dest D:\\Backups   # somewhere else
    python backup_live_db.py --keep 60            # keep more days
"""
import argparse
import glob
import os
import sqlite3
import sys
from datetime import datetime

from db_location import backup_dir, live_db_path

DEFAULT_DEST = backup_dir()   # db_location.py: the AP_Backups folder next to the project (Google Drive)
PREFIX = "ap_disbursement_backup_"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--dest", default=DEFAULT_DEST)
    ap.add_argument("--keep", type=int, default=30)
    a = ap.parse_args()
    src_path = a.db or live_db_path()
    if not os.path.exists(src_path):
        print(f"ERROR: no database at {src_path}")
        return 1
    os.makedirs(a.dest, exist_ok=True)
    out = os.path.join(a.dest, f"{PREFIX}{datetime.now():%Y%m%d_%H%M%S}.db")

    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()

    check = sqlite3.connect(out)
    try:
        ok = check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        checks = check.execute("SELECT COUNT(*) FROM checks_issued").fetchone()[0]
    finally:
        check.close()
    if not ok:
        os.remove(out)
        print("ERROR: the backup failed its integrity check and was discarded.")
        return 1
    print(f"Backed up {src_path} -> {out} ({os.path.getsize(out) // 1024} KB, {checks} checks on file)")

    old = sorted(glob.glob(os.path.join(a.dest, PREFIX + "*.db")))
    for path in old[:-a.keep] if a.keep > 0 else []:
        os.remove(path)
        print("Removed old backup", os.path.basename(path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
