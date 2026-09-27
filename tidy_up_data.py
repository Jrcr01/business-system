"""Data tidy-up (2026-09-27, system assessment):
  1. deletes the order statements that are completely empty -- no invoices, no checks (of any status), no credit memos, no typed-but-not-parked
     checks. The LEGACY-MS-ACCESS batches (migrated checks, no invoices) are NOT empty and are never touched.
  2. loan note PENDING-126 (Emelita Roxas): creates its repayment schedule from its existing check (principal = check amount, interest 0),
     exactly like the 'Create schedule from existing checks' button.
  3. the two bank-loan lenders that were not bank names (per user): the loan "PN8004 7053 5001 8969" belongs to PNB -- it is moved under the
     existing lender "Philippine National Bank" (its loan reference stays PN8004 7053 5001 8969) and the then-empty lender is removed;
     "Honda Giorno" is renamed "Inhouse Financing".

    python tidy_up_data.py           -> only shows what would change (changes nothing)
    python tidy_up_data.py --apply   -> makes a backup copy, then applies
Optional: --db <path>.
"""
import os
import shutil
import sqlite3
import sys
from datetime import datetime

args = sys.argv[1:]
db = args[args.index("--db") + 1] if "--db" in args else r"C:\AP_Data\ap_disbursement.db"
apply = "--apply" in args
EMELITA_NOTE_REF = "PENDING-126"
PN_LENDER, PNB_LENDER = "PN8004 7053 5001 8969", "Philippine National Bank"
GIORNO_OLD, GIORNO_NEW = "Honda Giorno", "Inhouse Financing"

conn = sqlite3.connect(db)
empty = conn.execute(
    """SELECT os.statement_id, os.statement_ref, cr.creditor_name, os.status, DATE(os.created_at, 'localtime')
       FROM order_statements os JOIN creditors cr ON cr.creditor_id = os.creditor_id
       WHERE NOT EXISTS (SELECT 1 FROM invoices i WHERE i.statement_id = os.statement_id)
         AND NOT EXISTS (SELECT 1 FROM checks_issued k WHERE k.statement_id = os.statement_id)
         AND NOT EXISTS (SELECT 1 FROM credit_memos m WHERE m.statement_id = os.statement_id)
         AND NOT EXISTS (SELECT 1 FROM park_drafts d WHERE d.statement_id = os.statement_id)
       ORDER BY os.statement_id"""
).fetchall()
print(f"1. Completely empty order statements to delete: {len(empty)}")
for sid, ref, name, status, created in empty:
    print(f"   #{sid}  {ref}  {name}  [{status}]  created {created}")

note = conn.execute(
    """SELECT ln.loan_note_id, cr.creditor_name, ln.principal_amount, ln.status,
              (SELECT COUNT(*) FROM loan_installments i WHERE i.loan_note_id = ln.loan_note_id)
       FROM loan_notes ln JOIN creditors cr ON cr.creditor_id = ln.creditor_id WHERE ln.note_ref = ?""", (EMELITA_NOTE_REF,)).fetchone()
unlinked = conn.execute(
    """SELECT check_number, check_date, amount FROM checks_issued c WHERE c.loan_note_id = ? AND c.status = 'Active'
         AND NOT EXISTS (SELECT 1 FROM loan_installments li WHERE li.check_id = c.check_id)""", (note[0] if note else -1,)).fetchall()
do_note = bool(note) and bool(unlinked)
print(f"\n2. Loan note {EMELITA_NOTE_REF}: " + (
    f"{note[1]}, principal {note[2]:,.2f}, {note[4]} schedule row(s) now -> will add {len(unlinked)} row(s) from its check(s): "
    + ", ".join(f"{n} {d} {a:,.2f}" for n, d, a in unlinked) if do_note else "nothing to do (not found, or its checks are already on the schedule)"))

def _bank_creditor(name):
    row = conn.execute("SELECT creditor_id FROM creditors WHERE creditor_name = ? AND creditor_type = 'BANK_LOAN'", (name,)).fetchone()
    return row[0] if row else None


pn_id, pnb_id, giorno_id = _bank_creditor(PN_LENDER), _bank_creditor(PNB_LENDER), _bank_creditor(GIORNO_OLD)
pn_loans = conn.execute("SELECT bank_loan_id, loan_ref FROM bank_loans WHERE creditor_id = ?", (pn_id,)).fetchall() if pn_id else []
pn_other_refs = sum(conn.execute(f"SELECT COUNT(*) FROM {t} WHERE creditor_id = ?", (pn_id,)).fetchone()[0]
                    for t in ("order_statements", "invoices", "loan_notes", "checks_issued", "credit_cards")) if pn_id else 0
do_pn = bool(pn_id and pnb_id)
do_giorno = bool(giorno_id)
print("\n3. Lender names:")
if do_pn:
    print(f"   '{PN_LENDER}': move {len(pn_loans)} bank loan(s) ({', '.join(r[1] for r in pn_loans)}) under '{PNB_LENDER}', then remove the empty lender"
          + (f" -- NOT removed: it still has {pn_other_refs} other record(s)" if pn_other_refs else ""))
else:
    print(f"   '{PN_LENDER}': nothing to do" + ("" if pnb_id else f" (no bank-loan lender named '{PNB_LENDER}' found)"))
print(f"   '{GIORNO_OLD}': " + (f"rename to '{GIORNO_NEW}'" if do_giorno else "nothing to do (already renamed or not found)"))
conn.close()

if not apply:
    print("\nNothing changed. Run again with --apply to apply.")
    sys.exit(0)
if not empty and not do_note and not do_pn and not do_giorno:
    print("Nothing to do.")
    sys.exit(0)

backup = db + f".before_tidy_up_{datetime.now():%Y%m%d_%H%M%S}"
shutil.copy2(db, backup)
print("\nBackup saved:", backup)
conn = sqlite3.connect(db)
for sid, ref, name, status, created in empty:
    conn.execute("DELETE FROM order_statements WHERE statement_id = ?", (sid,))
    conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, old_values) VALUES ('STATEMENT_DELETED', 'order_statements', ?, ?)",
                 (sid, f"empty order statement {ref} ({name}, {status}) deleted in the data tidy-up -- no invoices, checks or memos"))
conn.commit()
conn.close()
if empty:
    print(f"Deleted {len(empty)} empty order statement(s).")

if do_pn or do_giorno:
    conn = sqlite3.connect(db)
    if do_pn:
        for loan_id, ref in pn_loans:
            conn.execute("UPDATE bank_loans SET creditor_id = ? WHERE bank_loan_id = ?", (pnb_id, loan_id))
            conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, old_values, new_values) VALUES ('BANK_LOAN_LENDER_CHANGED', 'bank_loans', ?, ?, ?)",
                         (loan_id, f"lender '{PN_LENDER}'", f"lender '{PNB_LENDER}' (loan reference {ref} kept)"))
        if not pn_other_refs:
            conn.execute("DELETE FROM creditors WHERE creditor_id = ?", (pn_id,))
            conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, old_values) VALUES ('CREDITOR_DELETED', 'creditors', ?, ?)",
                         (pn_id, f"lender '{PN_LENDER}' removed -- its loan now sits under '{PNB_LENDER}'"))
        print(f"Moved {len(pn_loans)} loan(s) under '{PNB_LENDER}'" + ("" if pn_other_refs else f" and removed the empty lender '{PN_LENDER}'") + ".")
    if do_giorno:
        conn.execute("UPDATE creditors SET creditor_name = ? WHERE creditor_id = ?", (GIORNO_NEW, giorno_id))
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, old_values, new_values) VALUES ('CREDITOR_RENAMED', 'creditors', ?, ?, ?)",
                     (giorno_id, GIORNO_OLD, GIORNO_NEW))
        print(f"Renamed '{GIORNO_OLD}' to '{GIORNO_NEW}'.")
    conn.commit()
    conn.close()

if do_note:
    os.environ["AP_DB_PATH"] = db
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import engines.loan_schedule_service as ls
    ls.DB_PATH = db
    ok, msg = ls.create_schedule_from_checks(int(note[0]))
    print(f"Loan note {EMELITA_NOTE_REF}: {msg}")
    if not ok:
        sys.exit(1)

conn = sqlite3.connect(db)
print("Integrity:", conn.execute("PRAGMA integrity_check").fetchone()[0])
