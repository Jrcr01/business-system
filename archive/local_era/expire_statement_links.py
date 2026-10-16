"""Switch off the public Drive links of statement copies that are past their date (2026-09-27, owner: a QR copy stops working 3 months
after the batch's last check date). Runs nightly from run_backup.bat, right after the backup; the app also runs the same sweep once a day
when Payment run opens. Never fails the backup: if Drive is not signed in, offline, or the database does not have the tables yet, it
says so and exits 0. The files themselves stay in Drive, privately.

    python expire_statement_links.py            # the live database
    (AP_DB_PATH set -> that database instead, e.g. the test copy)
"""
import os
import sqlite3
import sys

import db_location

os.environ.setdefault("AP_DB_PATH", db_location.live_db_path())

from engines.db_engine import open_db  # noqa: E402
from engines.drive_service import DriveError, DriveNotConnected, GoogleDrive  # noqa: E402
from engines.statement_version_service import expire_public_links, expired_public_versions  # noqa: E402


def main() -> int:
    db = os.environ["AP_DB_PATH"]
    with open_db(db) as conn:                              # the one door (Phase F1.2)
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'statement_versions'").fetchone():
            print("Statement links: nothing to do (this database has no statement copies yet).")
            return 0
    due = expired_public_versions()
    if not due:
        print("Statement links: none past their date.")
        return 0
    try:
        result = expire_public_links(GoogleDrive())
    except (DriveNotConnected, DriveError) as e:
        print(f"Statement links: {len(due)} are past their date but Google Drive could not be reached ({e}). "
              "Run drive_connect.bat; they will be switched off next time.")
        return 0
    for sid, version_no, until in result["switched_off"]:
        print(f"Statement links: switched off batch {sid} v{version_no} (expired {until:%b %d, %Y}).")
    for sid, version_no, err in result["failed"]:
        print(f"Statement links: could not switch off batch {sid} v{version_no}: {err}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
