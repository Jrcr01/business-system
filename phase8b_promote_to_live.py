"""Phase 8b: Master Data Setup (Production cutover).

One-time script that backs up the live database, then rebuilds it from schema.sql and runs the
exact same import logic as Phase 8a (phase8a_master_data_setup.py) directly against it --
replacing the development test fixtures currently in data/ap_disbursement.db (Berto/Berta/
Berti/Bertz/Berting, "MALAPIT NA MAGAWA", "Slick Auto Parts Inc.", etc. -- all scaffolding built
up while testing features, not real business data) with the real, user-reviewed master data
from report_8a.txt.

Run once, only after Phase 8a's TEST run (data/ap_disbursement_TEST.db, report_8a.txt) has been
reviewed and approved:

    python phase8b_promote_to_live.py

Always backs up data/ap_disbursement.db to data/ap_disbursement.db.bak_pre8b_<timestamp> first,
same naming convention as every other pre-migration backup already in data/. Writes report_8b.txt
(identical shape to report_8a.txt) against the live result.
"""
import os
import shutil
import sqlite3
from datetime import datetime

import phase8a_master_data_setup as p8a

PROJECT_ROOT = p8a.PROJECT_ROOT
LIVE_DB_PATH = os.path.join(PROJECT_ROOT, "data", "ap_disbursement.db")
REPORT_PATH = os.path.join(PROJECT_ROOT, "report_8b.txt")


def backup_live_db(db_path: str = LIVE_DB_PATH) -> str:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"No live database at {db_path} -- nothing to back up.")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.bak_pre8b_{timestamp}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def main():
    backup_path = backup_live_db()
    print(f"Backed up live db to {backup_path}")

    p8a.build_test_database(LIVE_DB_PATH)  # wipes + rebuilds LIVE_DB_PATH from schema.sql
    conn = sqlite3.connect(LIVE_DB_PATH)
    try:
        summary = p8a.run_migration(conn)
    finally:
        conn.close()
    p8a.write_report(summary, REPORT_PATH)

    ok = summary["integrity"]["validation_passed"] and not summary["validation"]["duplicates"]
    print(f"Rebuilt {LIVE_DB_PATH} with real master data.")
    print(f"Wrote {REPORT_PATH}")
    print("READY" if ok else "REVIEW NEEDED -- see report_8b.txt")


if __name__ == "__main__":
    main()
