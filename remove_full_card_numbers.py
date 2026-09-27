"""Removes full credit card numbers from the live database (2026-09-27, system assessment item #2).

The app only ever uses a card's last 4 digits; the full numbers came in with the legacy Access import and were copied into every daily
backup on Google Drive. This blanks credit_cards.card_number_full and masks any full card number written inside the audit log to its last 4
digits (e.g. **** **** **** 8271). Nothing else changes.

    python remove_full_card_numbers.py           -> only shows what would change (changes nothing)
    python remove_full_card_numbers.py --apply   -> makes a local backup copy, then removes them
Optional: --db <path>.

The local backup copy made by --apply still holds the full numbers (it stays in C:\\AP_Data, not on Google Drive). Once you have checked the
app works, you can delete it. The daily Google Drive backup made after this runs is clean; older ones still have the numbers until they age out.
"""
import re
import shutil
import sqlite3
import sys
from datetime import datetime

args = sys.argv[1:]
db = args[args.index("--db") + 1] if "--db" in args else r"C:\AP_Data\ap_disbursement.db"
apply = "--apply" in args

CARD = re.compile(r"(?<!\d)(\d{4})[- ]?(\d{4})[- ]?(\d{4})[- ]?(\d{3,4})(?!\d)")


def mask(text: str) -> str:
    return CARD.sub(lambda m: "**** **** **** " + m.group(4)[-4:], text)


conn = sqlite3.connect(db)
cards = conn.execute(
    "SELECT card_id, card_name, card_number_last4, card_number_full FROM credit_cards WHERE COALESCE(card_number_full, '') != ''").fetchall()
mismatch = [c for c in cards if re.sub(r"\D", "", c[3])[-4:] != (c[2] or "").strip()]
logs = [(lid, txt) for lid, txt in conn.execute("SELECT log_id, new_values FROM audit_logs WHERE new_values IS NOT NULL") if CARD.search(txt)]
logs += [(lid, txt) for lid, txt in conn.execute("SELECT log_id, old_values FROM audit_logs WHERE old_values IS NOT NULL") if CARD.search(txt)]

print(f"Cards with a full number stored: {len(cards)} (every one keeps its last 4 digits)")
if mismatch:
    print(f"  !! {len(mismatch)} card(s) whose stored last 4 does NOT match the full number -- fix these first, nothing will be changed:")
    for c in mismatch:
        print(f"     card #{c[0]} {c[1]}: last4 on file {c[2]!r}")
print(f"Audit log entries holding a full card number: {len(logs)} (will be masked to the last 4 digits)")
for lid, txt in logs:
    print(f"  entry #{lid}: {mask(txt)[:120]}")

if mismatch:
    sys.exit(1)
if not apply:
    print("\nNothing changed. Run again with --apply to remove them.")
    sys.exit(0)
if not cards and not logs:
    print("Nothing to remove.")
    sys.exit(0)

backup = db + f".before_remove_card_numbers_{datetime.now():%Y%m%d_%H%M%S}"
shutil.copy2(db, backup)
print("Local backup saved (still holds the full numbers -- delete it once you have checked the app):", backup)
conn.execute("UPDATE credit_cards SET card_number_full = NULL WHERE COALESCE(card_number_full, '') != ''")
for col in ("new_values", "old_values"):
    for lid, txt in conn.execute(f"SELECT log_id, {col} FROM audit_logs WHERE {col} IS NOT NULL").fetchall():
        if CARD.search(txt):
            conn.execute(f"UPDATE audit_logs SET {col} = ? WHERE log_id = ?", (mask(txt), lid))
conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('CARD_NUMBERS_REMOVED', 'credit_cards', NULL, ?)",
             (f"full card numbers removed from {len(cards)} card(s); {len(logs)} audit entr(y/ies) masked to last 4 digits",))
conn.commit()
conn.execute("VACUUM")          # also wipes the old values from free pages inside the database file
print("Integrity:", conn.execute("PRAGMA integrity_check").fetchone()[0])
left = conn.execute("SELECT COUNT(*) FROM credit_cards WHERE COALESCE(card_number_full, '') != ''").fetchone()[0]
print(f"Done. Full numbers left: {left}.")
