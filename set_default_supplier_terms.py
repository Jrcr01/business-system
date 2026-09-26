"""One-time: gives every supplier that has NO terms yet the default of 120 days. Suppliers that
already have terms (COD, 30, ...) are left alone. Backs up first.

    python set_default_supplier_terms.py [--dry-run] [--db <file>]
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

from db_location import live_db_path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    path = a.db or live_db_path()
    if not os.path.exists(path):
        print("ERROR: no database at", path); return 1
    conn = sqlite3.connect(path)
    n = conn.execute("SELECT COUNT(*) FROM creditors WHERE creditor_type = 'SUPPLIER' AND (terms IS NULL OR TRIM(terms) = '')").fetchone()[0]
    print(f"Database: {path}\nSuppliers with no terms yet: {n}")
    if a.dry_run or not n:
        print("Nothing changed."); return 0
    backup = f"{path}.bak_pre_terms_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(path, backup); print("Backed up to", backup)
    with conn:
        conn.execute("UPDATE creditors SET terms = '120' WHERE creditor_type = 'SUPPLIER' AND (terms IS NULL OR TRIM(terms) = '')")
    print(f"Set {n} supplier(s) to 120 days.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
