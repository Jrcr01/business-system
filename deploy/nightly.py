"""Server nightly job (server step F3.1): the audit log check + seal (engines/audit_seal.py, as the laptop's daily backup does), then a
backup of the database with SQLite's own backup API (safe while the ERP runs) into /srv/slicks/backups, newest 30 kept; then the original bank
statement files: older than 5 years removed (audited), the rest copied to /srv/slicks/backups/bank (owner, 2026-10-09).
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
    try:
        statement_files()
    except Exception as e:
        print(f"Statement files step could not run: {e}")
    try:
        from datetime import timedelta
        from engines.server_health import errors_on
        found = errors_on(manila_now().date() - timedelta(days=1)) or errors_on(manila_now().date())
        print("Errors in the server log:", found[0] if found else "could not read", *(found[1] if found else []))
    except Exception as e:                                   # noqa: BLE001
        print(f"Error count could not run: {e}")
    return 0


def statement_files() -> None:
    import shutil
    from engines.bank_statement_service import bank_dir, prune_statement_files
    folder, copy_to = bank_dir(), os.path.join(DEST, "bank")
    gone = prune_statement_files()
    for name in gone:
        print("Statement file past 5 years, removed:", name)
        try:
            os.remove(os.path.join(copy_to, name))
        except FileNotFoundError:
            pass
    if not os.path.isdir(folder):
        return
    os.makedirs(copy_to, exist_ok=True)
    new = 0
    for name in os.listdir(folder):
        src, dst = os.path.join(folder, name), os.path.join(copy_to, name)
        if os.path.isfile(src) and not os.path.exists(dst):
            shutil.copy2(src, dst)
            new += 1
    print(f"Statement files: {len(os.listdir(folder))} kept, {new} newly backed up, {len(gone)} removed")


if __name__ == "__main__":
    sys.exit(main())
