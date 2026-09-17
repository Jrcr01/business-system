"""Phase 8d: Dormant master data cleanup (Production cutover).

Backs up the live database, then applies the same cleanup as
phase8d_cleanup_dormant_master_data.py directly against it -- deactivating suppliers/lenders
with no Active check on file and closing bank loans with no installments on file. Additive/
idempotent by nature (only touches still-active rows), so unlike Phase 8c there's no risk in
running this more than once. Run once real data is on live (after Phase 8b and Phase 8c):

    python phase8d_promote_to_live.py

Always backs up data/ap_disbursement.db to data/ap_disbursement.db.bak_pre8d_<timestamp> first.
"""
import os
import shutil
import sqlite3
from datetime import datetime

import phase8a_master_data_setup as p8a
import phase8d_cleanup_dormant_master_data as p8d

PROJECT_ROOT = p8a.PROJECT_ROOT
LIVE_DB_PATH = os.path.join(PROJECT_ROOT, "data", "ap_disbursement.db")
REPORT_PATH = os.path.join(PROJECT_ROOT, "report_8d.txt")


def backup_live_db(db_path: str = LIVE_DB_PATH) -> str:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"No live database at {db_path} -- nothing to back up.")
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = f"{db_path}.bak_pre8d_{timestamp}"
    shutil.copy2(db_path, backup_path)
    return backup_path


def main():
    backup_path = backup_live_db()
    print(f"Backed up live db to {backup_path}")

    conn = sqlite3.connect(LIVE_DB_PATH)
    try:
        stats = p8d.cleanup_dormant_master_data(conn)
    finally:
        conn.close()
    p8d.write_report(stats, REPORT_PATH)

    print(f"Applied against {LIVE_DB_PATH}")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
