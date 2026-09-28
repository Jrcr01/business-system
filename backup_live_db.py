"""Daily backup of the live AP database.

Uses SQLite's own backup routine, which gives a consistent copy even while the app is open, then
checks the copy is readable. Keeps the newest 30 backups and deletes older ones.

Also (2026-09-27) saves the program's complete git history as ONE file (a git bundle) in a code_backups folder next to the
live database -- on this laptop C:\\AP_Data\\code_backups, i.e. on the laptop's own disk, NOT on Google Drive. The program files
and their .git folder live on Google Drive, and Drive's syncing can damage a .git folder; this keeps a clean copy elsewhere.
Restore with:  git clone <the .bundle file> Project_Checke     Keeps the newest 30.

    python backup_live_db.py                      # live db -> H:\\My Drive\\Claude9OS\\AP_Backups
    python backup_live_db.py --dest D:\\Backups   # somewhere else
    python backup_live_db.py --keep 60            # keep more days
"""
import argparse
import glob
import os
import shutil
import sqlite3
import subprocess
import sys
from datetime import datetime

from db_location import backup_dir, live_db_path
from engines.clock import manila_now  # noqa: E402  (one clock, Phase F1.3)

DEFAULT_DEST = backup_dir()   # db_location.py: the AP_Backups folder next to the project (Google Drive)
PREFIX = "ap_disbursement_backup_"
CODE_PREFIX = "ap_code_"
HERE = os.path.dirname(os.path.abspath(__file__))


def backup_code(dest_dir: str, keep: int) -> str:
    """Saves the full git history of the program as one bundle file in dest_dir. Returns a one-line report; never fails the
    database backup (a missing git or repository is just reported)."""
    git = shutil.which("git")
    if not git or not os.path.isdir(os.path.join(HERE, ".git")):
        return "Code history: skipped (git or the repository not found)."
    os.makedirs(dest_dir, exist_ok=True)
    out = os.path.join(dest_dir, f"{CODE_PREFIX}{manila_now():%Y%m%d_%H%M%S}.bundle")
    made = subprocess.run([git, "-C", HERE, "bundle", "create", out, "--all"], capture_output=True, text=True)
    if made.returncode != 0 or not os.path.exists(out):
        return f"Code history: NOT saved ({(made.stderr or '').strip()[:200]})"
    ok = subprocess.run([git, "-C", HERE, "bundle", "verify", out], capture_output=True, text=True).returncode == 0
    if not ok:
        os.remove(out)
        return "Code history: NOT saved (the bundle failed its own check and was discarded)."
    old = sorted(glob.glob(os.path.join(dest_dir, CODE_PREFIX + "*.bundle")))
    for path in old[:-keep] if keep > 0 else []:
        os.remove(path)
    return f"Code history saved -> {out} ({os.path.getsize(out) // 1024} KB)"


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
    out = os.path.join(a.dest, f"{PREFIX}{manila_now():%Y%m%d_%H%M%S}.db")

    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    dst = sqlite3.connect(out)
    try:
        src.backup(dst)                                   # the backup API includes saves still in the live -wal file
        dst.execute("PRAGMA journal_mode = DELETE")       # the copy is one plain file: no -wal / -shm on Google Drive (Phase F1.2)
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
    # next to the database that was backed up: C:\AP_Data\code_backups for the live one (a test's own temp folder in the tests)
    print(backup_code(os.path.join(os.path.dirname(os.path.abspath(src_path)), "code_backups"), a.keep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
