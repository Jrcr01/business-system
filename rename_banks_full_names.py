"""One-time: renames the bank records (bank loans and credit card issuers) from their abbreviations to
full names -- BDO -> BDO Unibank, Inc., and so on. Links to loans, cards and statements are by id, so
they are unaffected. Backs up first. Safe to run twice.

    python rename_banks_full_names.py [--dry-run] [--db <file>]
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

from db_location import live_db_path
from engines.bank_names import BANK_FULL_NAMES


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    path = a.db or live_db_path()
    if not os.path.exists(path):
        print("ERROR: no database at", path); return 1
    conn = sqlite3.connect(path)
    todo, clashes = [], []
    for cid, name, ctype in conn.execute("SELECT creditor_id, creditor_name, creditor_type FROM creditors WHERE creditor_type IN ('BANK_LOAN', 'CREDIT_CARD')").fetchall():
        full = BANK_FULL_NAMES.get(name.strip().upper())
        if not full or full == name:
            continue
        if conn.execute("SELECT 1 FROM creditors WHERE creditor_type = ? AND LOWER(creditor_name) = LOWER(?) AND creditor_id != ?", (ctype, full, cid)).fetchone():
            clashes.append((name, full, ctype))
        else:
            todo.append((cid, name, full, ctype))
    print(f"Database: {path}")
    for _, old, new, ctype in todo:
        print(f"  {ctype:11s} {old:6s} -> {new}")
    if clashes:
        print("ERROR: these would clash with a record that already has the full name -- nothing changed:", clashes); return 1
    if a.dry_run or not todo:
        print("Nothing changed."); return 0
    backup = f"{path}.bak_pre_bank_names_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(path, backup); print("Backed up to", backup)
    with conn:
        conn.executemany("UPDATE creditors SET creditor_name = ? WHERE creditor_id = ?", [(new, cid) for cid, _, new, _ in todo])
    print(f"Renamed {len(todo)} bank record(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
