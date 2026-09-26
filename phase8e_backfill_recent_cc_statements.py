"""OPTIONAL: load the last few months of PAST credit card statements from the Access export, so the
"Missing statement check" (Credit cards tab) has real history to compare against from day one.

Live only holds upcoming statements (phase 8c imports due-today-and-later only), so without this the
check can't tell what each card had last month. Past statements are stored as Paid (their due date has
passed) with just the total -- no rolling-balance breakdown, same as every imported statement.

    python phase8e_backfill_recent_cc_statements.py --dry-run     # count only, change nothing
    python phase8e_backfill_recent_cc_statements.py               # live database, backs up first
    python phase8e_backfill_recent_cc_statements.py --months 4    # go back further (default 3)

Safe to re-run: a statement already on file (same card, due date, amount) is skipped, never duplicated.
"""
import argparse, os, shutil, sqlite3
from datetime import date, datetime

import phase8c_reconstruct_pending_transactions as p8c

ROOT = os.path.dirname(os.path.abspath(__file__))
from db_location import live_db_path
LIVE = live_db_path()


def month_floor(d: date, back: int) -> date:
    m = d.year * 12 + (d.month - 1) - back
    return date(m // 12, m % 12 + 1, 1)


def load_past(months: int, today: date) -> list:
    start = month_floor(today, months)
    out = []
    with open(p8c.CC_STATEMENT_PATH, encoding="cp1252", errors="replace") as f:
        import csv
        for row in csv.reader(f):
            if len(row) < 4 or not row[1].strip().isdigit():
                continue
            d = p8c.parse_mdy(row[2])
            amt = p8c.parse_amount(row[3])
            if d is None or amt <= 0 or not (start <= d < today):
                continue
            out.append({"legacy_card_id": int(row[1]), "due_date": d, "total_amount_due": amt})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=LIVE)
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    today = date.today()
    stmts = load_past(a.months, today)
    conn = sqlite3.connect(a.db)
    card_by_legacy = {r[0]: r[1] for r in conn.execute("SELECT legacy_card_id, card_id FROM credit_cards WHERE legacy_card_id IS NOT NULL")}
    todo, dup, nocard = [], 0, 0
    for s in stmts:
        cid = card_by_legacy.get(s["legacy_card_id"])
        if cid is None:
            nocard += 1; continue
        if conn.execute("SELECT 1 FROM cc_statements WHERE card_id=? AND due_date=? AND ABS(total_amount_due-?)<0.005", (cid, str(s["due_date"]), s["total_amount_due"])).fetchone():
            dup += 1; continue
        todo.append((cid, str(s["due_date"])[:7], str(s["due_date"]), s["total_amount_due"]))
    print(f"Past statements in the last {a.months} month(s): {len(stmts)} | to add: {len(todo)} | already on file: {dup} | card not found: {nocard}")
    if a.dry_run or not todo:
        print("Nothing changed."); return
    backup = f"{a.db}.bak_pre8e_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(a.db, backup); print("Backed up to", backup)
    conn.executemany("INSERT INTO cc_statements (card_id, statement_month, due_date, total_amount_due, status) VALUES (?, ?, ?, ?, 'Paid')", todo)
    conn.commit()
    print(f"Added {len(todo)} past statement(s).")


if __name__ == "__main__":
    main()
