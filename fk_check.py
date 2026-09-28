"""Broken-link check (Phase F1, step 1 -- 2026-09-28). READ-ONLY.

Copies the database with SQLite's own backup routine (safe while the app is open) into a temporary file and runs
PRAGMA foreign_key_check on the COPY, so the real database is never touched. Lists every row that points at a record that does not exist
(e.g. an allocation pointing to a deleted invoice). Needed before F1 step 2 switches foreign-key enforcement on for every connection:
a broken link already in the data would then make ordinary saves fail. Fixes, if any are ever found, go through a preview / --apply script.

    python fk_check.py                      # the live database
    python fk_check.py --db data/ap_disbursement_TEST.db

Exit code 0 = no broken links, 1 = some found (listed), 2 = the database could not be read.
First run, 2026-09-28: live and TEST -- 33 tables, 41 declared links, 0 broken.
"""
import argparse
import os
import sqlite3
import sys
import tempfile
from collections import Counter

from db_location import live_db_path


def check(db_path: str) -> list[dict]:
    """Broken links in a copy of db_path: [{'table', 'rowid', 'parent', 'column', 'value'}]."""
    src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    fd, copy = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        dst = sqlite3.connect(copy)
        src.backup(dst)
        src.close()
        out = []
        for table, rowid, parent, fkid in dst.execute("PRAGMA foreign_key_check").fetchall():
            fk = [f for f in dst.execute(f"PRAGMA foreign_key_list({table})") if f[0] == fkid]
            col = fk[0][3] if fk else "?"
            value = dst.execute(f'SELECT "{col}" FROM "{table}" WHERE rowid = ?', (rowid,)).fetchone() if fk else None
            out.append({"table": table, "rowid": rowid, "parent": parent, "column": col, "value": value[0] if value else None})
        dst.close()
        return out
    finally:
        os.remove(copy)


def main() -> int:
    ap = argparse.ArgumentParser(description="List rows that point at records that do not exist (read-only, runs on a copy).")
    ap.add_argument("--db", default=os.environ.get("AP_DB_PATH") or live_db_path())
    db = ap.parse_args().db
    try:
        broken = check(db)
    except sqlite3.Error as e:
        print(f"Could not read {db}: {e}")
        return 2
    print(f"Database: {db}")
    if not broken:
        print("No broken links -- every row points at a record that exists.")
        return 0
    print(f"{len(broken)} broken link(s):")
    for (table, parent), n in Counter((b["table"], b["parent"]) for b in broken).items():
        print(f"  {table} -> {parent}: {n}")
    for b in broken[:200]:
        print(f"  {b['table']} row {b['rowid']}: {b['column']} = {b['value']} (no such {b['parent']})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
