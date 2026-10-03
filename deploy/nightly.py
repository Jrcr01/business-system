"""Server nightly job (server step F3.1): the audit log check + seal (engines/audit_seal.py, as the laptop's daily backup does), then a
backup of the database with SQLite's own backup API (safe while the ERP runs) into /srv/slicks/backups, newest 30 kept.
Run by the slicks-erp-nightly timer at 18:00 Manila time. Prints one line per step (kept by systemd: journalctl -u slicks-erp-nightly)."""
import glob
import os
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, HERE)

from db_location import safe_copy  # noqa: E402
from engines.audit_seal import seal_and_verify  # noqa: E402
from engines.clock import manila_now  # noqa: E402

DB = os.environ["WEB_DB_PATH"]
DEST = os.environ.get("AP_BACKUP_DIR", "/srv/slicks/backups")
KEEP = int(os.environ.get("ERP_BACKUP_KEEP", "30"))
PREFIX = "slicks_erp_backup_"


def main() -> int:
    try:
        seal = seal_and_verify(DB)
        print("Audit log check:", "OK" if seal["ok"] else "PROBLEM -- " + " ".join(seal["problems"]),
              f"({seal['newly_sealed']} new sealed, {seal['sealed_total']} in all)")
    except Exception as e:                                   # the backup must still run
        print(f"Audit log check could not run: {e}")
    os.makedirs(DEST, exist_ok=True)
    out = os.path.join(DEST, f"{PREFIX}{manila_now():%Y%m%d_%H%M%S}.db")
    safe_copy(DB, out)
    print(f"Backed up {DB} -> {out} ({os.path.getsize(out) // 1024} KB)")
    for old in sorted(glob.glob(os.path.join(DEST, PREFIX + "*.db")))[:-KEEP]:
        os.remove(old)
        print("Removed old backup", os.path.basename(old))
    return 0


if __name__ == "__main__":
    sys.exit(main())
