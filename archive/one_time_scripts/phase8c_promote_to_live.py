"""Phase 8c: Reconstruct pending transactions (Production cutover).

Backs up the live database, then applies the same reconstruction as
phase8c_reconstruct_pending_transactions.py directly against it. Unlike Phase 8b, this does NOT
wipe/rebuild the target first -- it's additive on top of whatever master data (Phase 8a/8b)
already lives there. Run once, only after reviewing the TEST result (report_8c.txt):

    python phase8c_promote_to_live.py

Always backs up data/ap_disbursement.db to data/ap_disbursement.db.bak_pre8c_<timestamp> first.
Refuses to run (see reconstruct_pending_transactions()'s own guard) if the live db already has
Phase 8c data in it, rather than creating duplicates.
"""
import os
import shutil
import sqlite3
from datetime import datetime

import phase8a_master_data_setup as p8a
import phase8c_reconstruct_pending_transactions as p8c

PROJECT_ROOT = p8a.PROJECT_ROOT
LIVE_DB_PATH = os.path.join(PROJECT_ROOT, "data", "ap_disbursement.db")
REPORT_PATH = os.path.join(PROJECT_ROOT, "report_8c.txt")


def backup_live_db(db_path: str = LIVE_DB_PATH) -> str:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"No live database at {db_path} -- nothing to back up.")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.bak_pre8c_{timestamp}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def main():
    backup_path = backup_live_db()
    print(f"Backed up live db to {backup_path}")

    conn = sqlite3.connect(LIVE_DB_PATH)
    try:
        stats = p8c.reconstruct_pending_transactions(conn)
    finally:
        conn.close()
    p8c.write_report(stats, REPORT_PATH)

    print(f"Applied against {LIVE_DB_PATH}")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
