"""One-off data fix (2026-09-19): the AMEX card was loaded with "AMEX" as its issuing bank, but AMEX is the
card network (like Visa/Mastercard/JCB) -- the bank is BDO. Moves that card to BDO, keeps AMEX as its network,
and removes the now-empty "AMEX" issuer entry. Backs up first.

    python fix_amex_issuer.py --dry-run
    python fix_amex_issuer.py                 # live database
    python fix_amex_issuer.py --db <file>     # another database
"""
import argparse, os, shutil, sqlite3
from datetime import datetime

from db_location import live_db_path
LIVE = live_db_path()


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--db", default=LIVE); ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    conn = sqlite3.connect(a.db)
    amex = conn.execute("SELECT creditor_id FROM creditors WHERE creditor_type='CREDIT_CARD' AND UPPER(creditor_name)='AMEX'").fetchone()
    if not amex:
        print("No 'AMEX' issuer found -- nothing to do."); return
    cards = conn.execute("SELECT card_id, card_name, card_number_last4 FROM credit_cards WHERE creditor_id = ?", (amex[0],)).fetchall()
    print(f"Cards currently under issuer AMEX: {cards}")
    if a.dry_run:
        print("Dry run -- nothing changed."); return
    backup = f"{a.db}.bak_pre_amex_fix_{datetime.now():%Y%m%d_%H%M%S}"; shutil.copy2(a.db, backup); print("Backed up to", backup)
    bdo = conn.execute("SELECT creditor_id FROM creditors WHERE creditor_type='CREDIT_CARD' AND UPPER(creditor_name)='BDO' ORDER BY is_active DESC LIMIT 1").fetchone()
    if bdo:
        bdo_id = bdo[0]; conn.execute("UPDATE creditors SET is_active = 1 WHERE creditor_id = ?", (bdo_id,))
    else:
        bdo_id = conn.execute("INSERT INTO creditors (creditor_name, creditor_type) VALUES ('BDO', 'CREDIT_CARD')").lastrowid
    conn.execute("UPDATE credit_cards SET creditor_id = ?, network = COALESCE(NULLIF(network, ''), 'AMEX') WHERE creditor_id = ?", (bdo_id, amex[0]))
    still = conn.execute("SELECT COUNT(*) FROM credit_cards WHERE creditor_id = ?", (amex[0],)).fetchone()[0]
    if still == 0:
        conn.execute("DELETE FROM creditors WHERE creditor_id = ?", (amex[0],)); print("Removed the empty AMEX issuer.")
    conn.commit(); print(f"Moved {len(cards)} card(s) to BDO.")

if __name__ == "__main__":
    main()
