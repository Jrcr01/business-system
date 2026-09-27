"""Where the LIVE database file is. One place so every script and the app agree.

Order: AP_DB_PATH environment variable, then the first line of live_db_path.txt (written by
move_database_out_of_drive.py), then the original data/ap_disbursement.db.
"""
import os

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
