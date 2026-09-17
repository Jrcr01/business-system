"""Phase 8c: Reconstruct pending (postdated, not-yet-cleared) transactions.

Phase 8a/8b deliberately imported master data only, no transaction history. This script fills
in the one piece of transaction history that actually matters going forward: real checks/
installments that were ALREADY WRITTEN in the old Access system but haven't cleared yet
(check_date in the future relative to today) -- these are real financial commitments, not
historical noise, so they need to exist in the new system's own check/installment tracking.

Per the user's explicit simplifications during this session:
  - No invoice-level detail is reconstructed ("disregard the invoices because i can easily look
    it up from ms access") -- this is check/installment-level only.
  - No per-legacy-order grouping either ("make one batch them all in one as from ms access") --
    every supplier's postdated checks land in a single consolidated batch per supplier, not one
    batch per old Access order_id.
  - Everything created here is tagged "LEGACY-MS-ACCESS-..." so it's clearly identifiable as
    migrated from Access, per the user's request to keep migrated data separately marked.

Source: data/source/TblPayeeChecks.txt (cp1252, comma-separated, no header row). TblOrder.txt/
TblOrderDetail.txt are no longer read by this script given the above.

What gets created, per postdated (date > today) entry in TblPayeeChecks.txt:
  - SUPPLIER (also the 4 singleton categories folded into SUPPLIER in Phase 8a): one
    consolidated order_statement per supplier (statement_ref "LEGACY-MS-ACCESS-<payee_id>",
    created once, reused for every postdated check that supplier has), holding a real
    checks_issued row per check (status='Active', printed_at=NULL -- recorded/parked, not yet
    printed). total_amount is set to the sum of the checks attached, since there are no invoices
    to auto-derive it from.
  - LENDER: checks_issued.loan_note_id set directly on that lender's existing Phase 8a
    placeholder loan_note (the single "Pending Setup" note created per lender) -- not split into
    separate notes per legacy LN1/LN2/etc. tag; a real refinement but a bigger job, left for
    later if wanted.
  - BANK_LOAN: NOT a checks_issued row at all -- this app tracks bank loan payments as
    bank_loan_installments (due_date + amount, no check concept, per the existing "untracked
    cash" design). Matched to the specific bank_loans row via legacy_payee_id (each legacy
    bank-loan payee became its own bank_loans placeholder in Phase 8a). Also flips
    has_amortization to 1 on any bank loan that gains a real installment this way.

Deliberately NOT done: nothing about already-cleared (date <= today) history is touched -- this
is additive, forward-looking data only, consistent with the "fresh start" rule the rest of
Phase 8a/8b followed. Run directly:

    python phase8c_reconstruct_pending_transactions.py

against the TEST db (default) to review before promoting to live the same way Phase 8b did.
"""
import csv
import os
import sqlite3
from collections import defaultdict
from datetime import date

import phase8a_master_data_setup as p8a

PROJECT_ROOT = p8a.PROJECT_ROOT
PAYEE_CHECKS_PATH = os.path.join(PROJECT_ROOT, "data", "source", "TblPayeeChecks.txt")
TEST_DB_PATH = p8a.TEST_DB_PATH
REPORT_PATH = os.path.join(PROJECT_ROOT, "report_8c.txt")

# The cleared-vs-postdated cutoff. Real date.today() rather than a fixed date, so re-running
# this script later against a refreshed export (same legacy files, more rows appended in Access
# since the last pull) needs no manual edit here -- whatever's dated on or before the day the
# script actually runs is historical, everything after is still outstanding, matching whatever
# "today" really is at that moment rather than a frozen validation-day snapshot.
TODAY = date.today()


def parse_amount(raw: str) -> float:
    raw = raw.strip()
    if not raw:
        return 0.0
    neg = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()").lstrip("$").replace(",", "")
    if not raw:
        return 0.0
    val = float(raw)
    return -val if neg else val


def parse_mdy(raw: str):
    raw = raw.strip().split(" ")[0]
    if not raw:
        return None
    m, d, y = raw.split("/")
    try:
        return date(int(y), int(m), int(d))
    except ValueError:
        return None


def parse_postdated_checks(path: str = PAYEE_CHECKS_PATH, today: date = TODAY) -> list:
    """Every check with a real amount and check_date > today."""
    checks = []
    with open(path, encoding="cp1252", errors="replace") as f:
        for row in csv.reader(f):
            if not row or not row[0].strip():
                continue
            amt = parse_amount(row[4])
            if amt == 0:
                continue
            d = parse_mdy(row[2])
            if d is None or d <= today:
                continue
            checks.append({
                "legacy_check_id": int(row[0]),
                "legacy_payee_id": int(row[1]),
                "check_date": d,
                "check_number": row[3].strip(),
                "amount": amt,
            })
    return checks


def _creditor_id_by_legacy_payee(conn: sqlite3.Connection) -> dict:
    return {row[0]: row[1] for row in conn.execute(
        "SELECT legacy_payee_id, creditor_id FROM creditors WHERE legacy_payee_id IS NOT NULL"
    )}


def _bank_loan_id_by_legacy_payee(conn: sqlite3.Connection) -> dict:
    """bank_loans has no legacy_payee_id column of its own -- unlike creditors/cardholders,
    it can't have one directly, since several bank_loans rows share a single creditor (one per
    issuing bank, per Phase 8a's grouping). Each row's own legacy origin is instead encoded in
    loan_ref, set to "PENDING-<legacy payee_id>" by create_bank_loan_placeholder()
    (phase8a_master_data_setup.py) -- parsed back out here for an exact per-loan mapping."""
    mapping = {}
    for bank_loan_id, loan_ref in conn.execute("SELECT bank_loan_id, loan_ref FROM bank_loans WHERE loan_ref LIKE 'PENDING-%'"):
        try:
            payee_id = int(loan_ref.split("-", 1)[1])
        except (IndexError, ValueError):
            continue
        mapping[payee_id] = bank_loan_id
    return mapping


def _loan_note_id_by_legacy_payee(conn: sqlite3.Connection) -> dict:
    return {row[0]: row[1] for row in conn.execute(
        "SELECT c.legacy_payee_id, ln.loan_note_id FROM loan_notes ln "
        "JOIN creditors c ON ln.creditor_id = c.creditor_id WHERE c.legacy_payee_id IS NOT NULL"
    )}


def reconstruct_pending_transactions(conn: sqlite3.Connection) -> dict:
    # Not idempotent by design -- checks_issued/bank_loan_installments have no legacy-id column
    # to dedupe against, so a second run against a target that already has Phase 8c data would
    # silently create duplicate checks/installments rather than skip them. Refuse clearly
    # instead: rebuild the target fresh first (phase8a_master_data_setup.py for TEST, or restore
    # from the pre-8c backup for live) before re-running this.
    already_applied = conn.execute(
        "SELECT COUNT(*) FROM order_statements WHERE statement_ref LIKE 'LEGACY-MS-ACCESS-%'"
    ).fetchone()[0]
    if already_applied:
        raise RuntimeError(
            "This database already has Phase 8c data (LEGACY-MS-ACCESS-* statements found) -- "
            "running this again would create duplicates, not update anything. Rebuild the "
            "target fresh first."
        )

    postdated = parse_postdated_checks()

    creditor_by_payee = _creditor_id_by_legacy_payee(conn)
    bank_loan_by_payee = _bank_loan_id_by_legacy_payee(conn)
    loan_note_by_payee = _loan_note_id_by_legacy_payee(conn)

    bank_account_id = conn.execute("SELECT bank_account_id FROM bank_accounts ORDER BY bank_account_id LIMIT 1").fetchone()[0]

    statement_by_payee: dict = {}
    statement_running_total: dict = defaultdict(float)
    stats = {
        "supplier_statements_created": 0, "supplier_checks_created": 0,
        "lender_checks_created": 0, "bank_loan_installments_created": 0,
        "skipped_no_creditor": [], "skipped_no_bank_loan": [], "skipped_no_loan_note": [],
    }

    def get_or_create_supplier_statement(payee_id: int):
        if payee_id in statement_by_payee:
            return statement_by_payee[payee_id]
        creditor_id = creditor_by_payee.get(payee_id)
        if creditor_id is None:
            stats["skipped_no_creditor"].append(payee_id)
            return None
        statement_ref = f"LEGACY-MS-ACCESS-{payee_id}"
        cur = conn.execute(
            "INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, ?, 0, 'Issued')",
            (creditor_id, statement_ref),
        )
        statement_id = cur.lastrowid
        stats["supplier_statements_created"] += 1
        statement_by_payee[payee_id] = statement_id
        return statement_id

    payee_type = {}
    rows = p8a.parse_payee_tagged_csv()
    buckets = p8a.categorize_and_resolve_payees(rows)
    for kind in ("suppliers", "lenders", "bank_loans"):
        for p in buckets[kind]:
            payee_type[p["PayeeId"]] = kind

    for chk in postdated:
        payee_id = chk["legacy_payee_id"]
        kind = payee_type.get(payee_id)

        if kind == "bank_loans":
            bank_loan_id = bank_loan_by_payee.get(payee_id)
            if bank_loan_id is None:
                stats["skipped_no_bank_loan"].append(payee_id)
                continue
            next_no = conn.execute(
                "SELECT COALESCE(MAX(installment_no), 0) + 1 FROM bank_loan_installments WHERE bank_loan_id = ?", (bank_loan_id,)
            ).fetchone()[0]
            conn.execute(
                "INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (?, ?, ?, ?)",
                (bank_loan_id, next_no, chk["check_date"].isoformat(), chk["amount"]),
            )
            conn.execute("UPDATE bank_loans SET has_amortization = 1 WHERE bank_loan_id = ?", (bank_loan_id,))
            stats["bank_loan_installments_created"] += 1
            continue

        if kind == "lenders":
            loan_note_id = loan_note_by_payee.get(payee_id)
            if loan_note_id is None:
                stats["skipped_no_loan_note"].append(payee_id)
                continue
            creditor_id = creditor_by_payee.get(payee_id)
            conn.execute(
                "INSERT INTO checks_issued (creditor_id, bank_account_id, loan_note_id, check_number, check_date, amount, status) "
                "VALUES (?, ?, ?, ?, ?, ?, 'Active')",
                (creditor_id, bank_account_id, loan_note_id, chk["check_number"], chk["check_date"].isoformat(), chk["amount"]),
            )
            stats["lender_checks_created"] += 1
            continue

        # suppliers (and any singleton category folded into SUPPLIER in Phase 8a)
        creditor_id = creditor_by_payee.get(payee_id)
        if creditor_id is None:
            stats["skipped_no_creditor"].append(payee_id)
            continue
        statement_id = get_or_create_supplier_statement(payee_id)
        if statement_id is None:
            continue
        conn.execute(
            "INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_number, check_date, amount, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'Active')",
            (creditor_id, bank_account_id, statement_id, chk["check_number"], chk["check_date"].isoformat(), chk["amount"]),
        )
        statement_running_total[statement_id] += chk["amount"]
        stats["supplier_checks_created"] += 1

    # No invoices exist to drive order_statements' usual auto-recompute trigger -- set each
    # reconstructed statement's total directly from the checks actually attached to it.
    for statement_id, total in statement_running_total.items():
        conn.execute("UPDATE order_statements SET total_amount = ? WHERE statement_id = ?", (round(total, 2), statement_id))

    conn.commit()
    return stats


def write_report(stats: dict, path: str = REPORT_PATH) -> None:
    lines = [
        "=" * 78, "PHASE 8c: RECONSTRUCTED PENDING (POSTDATED) TRANSACTIONS -- REPORT",
        f"Run date: {date.today().isoformat()}", "=" * 78, "",
        "SUPPLIERS",
        f"  Consolidated batches created (one per supplier, tagged LEGACY-MS-ACCESS-<payee_id>): {stats['supplier_statements_created']}",
        f"  Postdated checks attached: {stats['supplier_checks_created']}",
        "", "LENDERS",
        f"  Postdated checks (attached to existing Pending Setup loan note): {stats['lender_checks_created']}",
        "", "BANK LOANS",
        f"  Installments created: {stats['bank_loan_installments_created']}",
        "", "SKIPPED (no matching creditor/bank_loan/loan_note found)",
        f"  No creditor match: {sorted(set(stats['skipped_no_creditor']))}",
        f"  No bank_loan match: {sorted(set(stats['skipped_no_bank_loan']))}",
        f"  No loan_note match: {sorted(set(stats['skipped_no_loan_note']))}",
        "=" * 78,
    ]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def main():
    conn = sqlite3.connect(TEST_DB_PATH)
    try:
        stats = reconstruct_pending_transactions(conn)
    finally:
        conn.close()
    write_report(stats)
    print(f"Applied against {TEST_DB_PATH}")
    print(f"Wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
