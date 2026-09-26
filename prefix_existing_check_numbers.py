"""One-time data change (2026-09-20): every check number becomes the FULL 10-digit number.

Checks migrated from Access are stored as 7 digits (e.g. 2554250). The bank's real number is the
prefix 399 plus those 7 digits (3992554250) -- and the checkbooks you register use the full number as
printed. This adds the 399 prefix to every existing 7-digit-or-shorter numeric check number (and to
spoiled-check numbers) so they all read the same way.

    python prefix_existing_check_numbers.py --dry-run     # count only, change nothing
    python prefix_existing_check_numbers.py               # live database, backs up first
    python prefix_existing_check_numbers.py --db <file>   # another database

Stops without changing anything if two numbers would collide. Safe to run twice (already-10-digit
numbers are left alone).
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

from db_location import live_db_path

PREFIX = "399"


def new_number(old: str) -> str | None:
    text = (old or "").strip()
    if text.isdigit() and len(text) <= 7:
        return PREFIX + text.zfill(7)
    return None


def plan(conn: sqlite3.Connection, table: str, id_col: str) -> list:
    rows = conn.execute(f"SELECT {id_col}, bank_account_id, check_number FROM {table} WHERE check_number IS NOT NULL").fetchall()
    return [(rid, acct, old, new_number(old)) for rid, acct, old in rows if new_number(old)]


def collisions(conn: sqlite3.Connection, table: str, changes: list) -> list:
    existing = {(acct, num) for acct, num in conn.execute(f"SELECT bank_account_id, check_number FROM {table} WHERE check_number IS NOT NULL")}
    bad, seen = [], set()
    for rid, acct, old, new in changes:
        if (acct, new) in existing or (acct, new) in seen:
            bad.append((old, new))
        seen.add((acct, new))
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    path = a.db or live_db_path()
    if not os.path.exists(path):
        print("ERROR: no database at", path); return 1
    conn = sqlite3.connect(path)
    checks = plan(conn, "checks_issued", "check_id")
    spoiled = plan(conn, "spoiled_checks", "spoiled_id") if conn.execute("SELECT 1 FROM sqlite_master WHERE name='spoiled_checks'").fetchone() else []
    print(f"Database: {path}")
    print(f"Checks to renumber: {len(checks)}   Spoiled-check numbers to renumber: {len(spoiled)}")
    if checks:
        print("Example:", ", ".join(f"{o} -> {n}" for _, _, o, n in checks[:3]))
    bad = collisions(conn, "checks_issued", checks) + collisions(conn, "spoiled_checks", spoiled) if spoiled or checks else []
    if bad:
        print("ERROR: these would collide with a number already on file -- nothing changed:", bad[:10]); return 1
    if a.dry_run or not (checks or spoiled):
        print("Nothing changed."); return 0
    backup = f"{path}.bak_pre_prefix_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(path, backup); print("Backed up to", backup)
    with conn:
        conn.executemany("UPDATE checks_issued SET check_number = ? WHERE check_id = ?", [(n, rid) for rid, _, _, n in checks])
        if spoiled:
            conn.executemany("UPDATE spoiled_checks SET check_number = ? WHERE spoiled_id = ?", [(n, rid) for rid, _, _, n in spoiled])
    left = conn.execute("SELECT COUNT(*) FROM checks_issued WHERE check_number GLOB '[0-9]*' AND LENGTH(check_number) <= 7").fetchone()[0]
    print(f"Renumbered {len(checks)} check(s). Short numbers remaining: {left}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
