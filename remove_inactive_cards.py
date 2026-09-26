"""One-off cleanup (2026-09-19): removes credit cards marked inactive in the legacy Access data.

Backs up the database first. A card is only deleted if it has no statements and no invoice paid
through it -- anything with history is left alone and listed instead.

    python remove_inactive_cards.py                 # live database (asks nothing, backs up first)
    python remove_inactive_cards.py --db <file>     # another database, e.g. a copy
    python remove_inactive_cards.py --dry-run       # list only, change nothing
"""
import argparse, os, shutil, sqlite3
from datetime import datetime

from db_location import live_db_path
LIVE = live_db_path()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=LIVE)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    rows = conn.execute(
        """SELECT cc.card_id, cr.creditor_name, cc.card_name, cc.card_number_last4,
                  (SELECT COUNT(*) FROM cc_statements s WHERE s.card_id = cc.card_id),
                  (SELECT COUNT(*) FROM invoices i WHERE i.paid_via_card_id = cc.card_id)
           FROM credit_cards cc JOIN creditors cr USING (creditor_id) WHERE cc.is_active = 0 ORDER BY cc.card_id"""
    ).fetchall()
    removable = [r for r in rows if r[4] == 0 and r[5] == 0]
    kept = [r for r in rows if r not in removable]
    print(f"Inactive cards found: {len(rows)}")
    for r in removable: print(f"  will remove: {r[1]} {r[2]} *{r[3]}")
    for r in kept: print(f"  KEPT (has {r[4]} statements / {r[5]} invoices): {r[1]} {r[2]} *{r[3]}")
    if a.dry_run or not removable:
        print("Nothing changed."); return
    backup = f"{a.db}.bak_pre_card_cleanup_{datetime.now():%Y%m%d_%H%M%S}"
    shutil.copy2(a.db, backup); print("Backed up to", backup)
    conn.executemany("DELETE FROM credit_cards WHERE card_id = ?", [(r[0],) for r in removable])
    conn.commit()
    print(f"Removed {len(removable)} card(s). Cards remaining: {conn.execute('SELECT COUNT(*) FROM credit_cards').fetchone()[0]}")

if __name__ == "__main__":
    main()
