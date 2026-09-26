"""One-time clean-up of records with nothing on them (no checks, no invoices, no amounts due):
  1. loan notes with no checks (the empty 'Pending Setup' placeholders)
  2. lenders left with nothing on file (no loan notes, checks or invoices)
  3. bank loans with no repayment rows at all and no checks (the empty 'Closed' placeholders)
Anything with checks, an installment schedule, or an amount still due is left alone. Bank (creditor) names stay.

    python clean_empty_records.py           -> only shows what would be deleted (changes nothing)
    python clean_empty_records.py --apply   -> makes a backup copy, then deletes them
Optional: --db <path>.  (This replaces delete_empty_loan_notes.py -- it does that step too.)"""
import shutil
import sqlite3
import sys
from datetime import datetime

args = sys.argv[1:]
db = args[args.index("--db") + 1] if "--db" in args else r"C:\AP_Data\ap_disbursement.db"
apply = "--apply" in args
conn = sqlite3.connect(db)
q = lambda sql, p=(): conn.execute(sql, p).fetchall()

notes = q("""SELECT ln.loan_note_id, ln.note_ref, cr.creditor_name FROM loan_notes ln JOIN creditors cr USING (creditor_id)
             WHERE NOT EXISTS (SELECT 1 FROM checks_issued k WHERE k.loan_note_id = ln.loan_note_id) ORDER BY 1""")
note_ids = {n[0] for n in notes}
bank_loans = q("""SELECT bl.bank_loan_id, bl.loan_ref, cr.creditor_name FROM bank_loans bl JOIN creditors cr USING (creditor_id)
                  WHERE NOT EXISTS (SELECT 1 FROM bank_loan_installments i WHERE i.bank_loan_id = bl.bank_loan_id)
                    AND NOT EXISTS (SELECT 1 FROM checks_issued k WHERE k.creditor_id = bl.creditor_id) ORDER BY 1""")
bl_ids = {b[0] for b in bank_loans}

lenders = []
for cid, name in q("SELECT creditor_id, creditor_name FROM creditors WHERE creditor_type = 'LENDER' ORDER BY creditor_id"):
    if q("SELECT 1 FROM checks_issued WHERE creditor_id = ? LIMIT 1", (cid,)) or q("SELECT 1 FROM invoices WHERE creditor_id = ? LIMIT 1", (cid,)) \
            or q("SELECT 1 FROM order_statements WHERE creditor_id = ? LIMIT 1", (cid,)):
        continue
    own_notes = {r[0] for r in q("SELECT loan_note_id FROM loan_notes WHERE creditor_id = ?", (cid,))}
    if own_notes <= note_ids:
        lenders.append((cid, name))

print(f"Loan notes with no checks: {len(notes)}")
for n in notes: print(f"  #{n[0]}  {n[1]}  {n[2]}")
print(f"\nLenders left with nothing on file: {len(lenders)}")
for l in lenders: print(f"  #{l[0]}  {l[1]}")
print(f"\nBank loans with no repayment rows and no checks: {len(bank_loans)}")
for b in bank_loans: print(f"  #{b[0]}  {b[2]}  -  {b[1]}")
kept_notes = q("SELECT COUNT(*) FROM loan_notes")[0][0] - len(notes)
kept_bl = q("SELECT COUNT(*) FROM bank_loans")[0][0] - len(bank_loans)
print(f"\nKept: {kept_notes} loan notes, {q(chr(83)+'ELECT COUNT(*) FROM creditors WHERE creditor_type=?', ('LENDER',))[0][0] - len(lenders)} lenders, {kept_bl} bank loans.")
if not apply:
    print("\nNothing changed. Run again with --apply to delete them.")
    sys.exit(0)
if not (notes or lenders or bank_loans):
    print("Nothing to delete.")
    sys.exit(0)
backup = db + f".before_clean_empty_{datetime.now():%Y%m%d_%H%M%S}"
shutil.copy2(db, backup)
print("Backup saved:", backup)
log = lambda action, entity, eid, text: conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES (?, ?, ?, ?)", (action, entity, eid, text))
for nid, ref, name in notes:
    conn.execute("DELETE FROM loan_installments WHERE loan_note_id = ?", (nid,))
    conn.execute("DELETE FROM loan_notes WHERE loan_note_id = ?", (nid,))
    log("LOAN_NOTE_DELETED", "loan_notes", nid, f"Deleted loan note {ref} ({name}) with no checks on record")
for cid, name in lenders:
    conn.execute("DELETE FROM creditors WHERE creditor_id = ?", (cid,))
    log("CREDITOR_DELETED", "creditors", cid, f"Deleted lender {name} with nothing on file")
for blid, ref, name in bank_loans:
    conn.execute("DELETE FROM bank_loans WHERE bank_loan_id = ?", (blid,))
    log("BANK_LOAN_DELETED", "bank_loans", blid, f"Deleted bank loan {ref} ({name}) with no repayment rows and no checks")
conn.commit()
print("Integrity:", conn.execute("PRAGMA integrity_check").fetchone()[0])
print(f"Deleted {len(notes)} loan note(s), {len(lenders)} lender(s), {len(bank_loans)} bank loan(s).")
