"""Where the LIVE database file is. One place so every script and the app agree.

Order: AP_DB_PATH environment variable, then the first line of live_db_path.txt (written by
move_database_out_of_drive.py), then the original data/ap_disbursement.db.
"""
import os
import sqlite3

ROOT = os.path.dirname(os.path.abspath(__file__))
DEFAULT = os.path.join(ROOT, "data", "ap_disbursement.db")
POINTER = os.path.join(ROOT, "live_db_path.txt")


def live_db_path() -> str:
    env = os.environ.get("AP_DB_PATH")
    if env:
        return env
    if os.path.exists(POINTER):
        with open(POINTER, encoding="utf-8") as f:
            line = f.readline().strip()
        if line:
            return line
    return DEFAULT


def backup_dir() -> str:
    """Where the daily backups go (2026-09-27, one place for it): the AP_BACKUP_DIR environment variable, else the AP_Backups folder
    next to the project folder -- on this laptop H:\\My Drive\\Claude9OS\\AP_Backups, i.e. on Google Drive, off this machine."""
    return os.environ.get("AP_BACKUP_DIR") or os.path.join(os.path.dirname(ROOT), "AP_Backups")


def drive_dir() -> str:
    """Where the Google Drive login files live (thermal statement QR copies, 2026-09-27): credentials.json (the OAuth client the owner
    downloads) and token.json (written by drive_connect.bat). Outside the project folder and git: the AP_DRIVE_DIR environment variable,
    else a "drive" folder beside the LIVE database (C:/Slicks/Data/drive) -- the same for the live and the test app."""
    env = os.environ.get("AP_DRIVE_DIR")
    if env:
        return env
    live = None
    if os.path.exists(POINTER):
        with open(POINTER, encoding="utf-8") as f:
            live = f.readline().strip() or None
    return os.path.join(os.path.dirname(live), "drive") if live else os.path.join(os.path.expanduser("~"), "AP_Drive")


def safe_copy(src: str, dst: str) -> str:
    """Copy a database file with SQLite's own backup routine (Phase F1.2, 2026-09-28). Since the database runs in WAL mode, the newest
    saves can sit in <db>-wal until SQLite checkpoints them, so a plain file copy (shutil.copy) could miss them; this never does, and it
    is safe while the app is open. The copy is one self-contained file. Returns dst."""
    source = sqlite3.connect(f"file:{src}?mode=ro", uri=True)
    try:
        target = sqlite3.connect(dst)
        try:
            source.backup(target)
            target.execute("PRAGMA journal_mode = DELETE")        # a copy is a plain single file (no -wal beside it)
        finally:
            target.close()
    finally:
        source.close()
    return dst
