"""One-time: replaces the all-CAPITALS creditor names with proper case.

Names come back exactly as they were before the capitals change (from the backup that change made:
*.bak_pre_uppercase_*); a name that was ALL CAPS even then is converted to proper case instead
(BDO stays BDO). Names added since the backup are converted the same way. A name someone edited after
the capitals change is left alone. Backs up first. Safe to run twice.

    python restore_creditor_case.py [--dry-run] [--db <file>]
"""
import argparse
import glob
import os
import shutil
import sqlite3
import sys
from datetime import datetime

from db_location import live_db_path
from engines.name_case import smart_proper_case


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    path = a.db or live_db_path()
    if not os.path.exists(path):
        print("ERROR: no database at", path); return 1
    original = {}
    backups = sorted(glob.glob(path + ".bak_pre_uppercase_*"))
    if backups:
        old = sqlite3.connect(backups[0])   # the OLDEST such backup = the state before capitals
        original = dict(old.execute("SELECT creditor_id, creditor_name FROM creditors").fetchall())
        old.close()
        print("Original spellings read from", os.path.basename(backups[0]))
    else:
        print("No pre-capitals backup found -- converting every name with the proper-case rule.")
    conn = sqlite3.connect(path)
    restored, converted = [], []
    for cid, name in conn.execute("SELECT creditor_id, creditor_name FROM creditors").fetchall():
        if name != name.upper() and any(c.islower() for c in name):
            continue                      # already has lowercase letters -- someone's own spelling
        before = original.get(cid)
        if before is not None and before.upper() == name.upper() and any(c.islower() for c in before):
            new = before                  # was mixed case before: restore exactly
            kind = restored
        else:
            new = smart_proper_case(before if before is not None and before.upper() == name.upper() else name)
            kind = converted
        if new != name:
            kind.append((cid, name, new))
    print(f"Database: {path}")
    print(f"Restored to their earlier spelling: {len(restored)}   Converted to proper case (were all caps): {len(converted)}")
    if converted:
        print("Please eyeball these converted ones:")
        for _, old_name, new in converted:
            print(f"   {old_name}  ->  {new}")
    if a.dry_run or not (restored or converted):
        print("Nothing changed."); return 0
    backup = f"{path}.bak_pre_propercase_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(path, backup); print("Backed up to", backup)
    with conn:
        conn.executemany("UPDATE creditors SET creditor_name = ? WHERE creditor_id = ?", [(new, cid) for cid, _, new in restored + converted])
    print(f"Updated {len(restored) + len(converted)} name(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
