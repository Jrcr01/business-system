"""'Looks like a double recording' checks (2026-09-21), limited to what matters per track:
every track. Most important: suppliers -- no duplicate invoices or checks; private loan notes --
no duplicate checks; bank loans -- the monthly payable (schedule rows) actually due; credit cards --
no duplicate statement (same card, due date and amount due).

Hard rules already stop exact repeats (same invoice number for a supplier, same check number, same
loan reference). These checks catch the softer kind -- the same thing entered twice under a
different reference (an invoice re-keyed with a new number, the same schedule uploaded onto two
loans, the same check amount and date issued twice). Each function only READS and returns a list
of plain-language warnings (empty = nothing suspicious); the app shows them in a pop-up and lets
the user decide whether to continue.
"""
import os
import re
import sqlite3

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def _php(v) -> str:
    return f"PHP {float(v):,.2f}"


def _conn():
    return sqlite3.connect(DB_PATH)


_DOC_PREFIXES = ("INVOICE", "INV", "SI", "DR", "OR", "CI", "SO")


def normalize_doc_ref(text) -> str:
    """Canonical form of an invoice number for comparing: capitals only, punctuation and spaces removed, a
    document-type prefix (SI, INV, DR, OR, CI, SO) dropped when a number follows it, leading zeros dropped.
    So "SI-00123", "SI 123", "inv123" and "123" all become "123"."""
    t = re.sub(r"[^A-Z0-9]", "", str(text or "").upper())
    for prefix in _DOC_PREFIXES:
        if t.startswith(prefix) and len(t) > len(prefix) and t[len(prefix)].isdigit():
            t = t[len(prefix):]
            break
    return (t.lstrip("0") or "0") if t.isdigit() or t.lstrip("0").isdigit() else t


def invoice_warnings(creditor_id: int, rows: list) -> list[str]:
    """rows: (doc_ref, invoice_date, gross_amount) for each invoice about to be added."""
    out, seen, seen_norm = [], set(), {}
    with _conn() as conn:
        on_file = conn.execute(
            """SELECT i.doc_ref, os.statement_ref FROM invoices i LEFT JOIN order_statements os ON os.statement_id = i.statement_id
               WHERE i.creditor_id = ? AND i.status != 'Cancelled'""", (creditor_id,)).fetchall()
        norm_on_file = {}
        for ref, batch in on_file:
            norm_on_file.setdefault(normalize_doc_ref(ref), (ref, batch))
        for doc_ref, inv_date, gross in rows:
            key = (str(doc_ref).strip().lower(), round(float(gross), 2))
            if key in seen:
                out.append(f"Invoice {doc_ref} ({_php(gross)}) appears more than once in what you are adding.")
            seen.add(key)
            norm = normalize_doc_ref(doc_ref)
            earlier = seen_norm.get(norm)
            if earlier is not None and earlier.strip().lower() != str(doc_ref).strip().lower():
                out.append(f"Invoice {doc_ref} looks like the same number as {earlier} in what you are adding, written differently.")
            seen_norm.setdefault(norm, str(doc_ref))
            hit_ref = norm_on_file.get(norm)
            if hit_ref and hit_ref[0].strip().lower() != str(doc_ref).strip().lower():
                out.append(f"Invoice {doc_ref} looks like the same number as {hit_ref[0]} already on file for this supplier"
                           + (f" (batch {hit_ref[1]})" if hit_ref[1] else "") + " -- just written differently.")
            hit = conn.execute(
                """SELECT i.doc_ref, os.statement_ref FROM invoices i LEFT JOIN order_statements os ON os.statement_id = i.statement_id
                   WHERE i.creditor_id = ? AND i.status != 'Cancelled' AND ABS(i.gross_amount - ?) < 0.005
                     AND i.invoice_date = ? AND LOWER(i.doc_ref) != LOWER(?) LIMIT 1""",
                (creditor_id, float(gross), str(inv_date), str(doc_ref).strip()),
            ).fetchone()
            if hit:
                out.append(f"Invoice {doc_ref}: this supplier already has invoice {hit[0]} for {_php(gross)} on the same date"
                           + (f" (batch {hit[1]})." if hit[1] else "."))
    return out


def check_warnings(creditor_id: int, rows: list) -> list[str]:
    """rows: (amount, check_date) for each check about to be issued to this payee."""
    out = []
    with _conn() as conn:
        for amount, check_date in rows:
            hit = conn.execute(
                """SELECT check_number, status FROM checks_issued WHERE creditor_id = ? AND status IN ('Active', 'Cleared')
                   AND ABS(amount - ?) < 0.005 AND check_date = ? LIMIT 1""",
                (creditor_id, float(amount), str(check_date)),
            ).fetchone()
            if hit:
                out.append(f"A check for {_php(amount)} dated {check_date} was already issued to this payee"
                           + (f" (check {hit[0]})." if hit[0] else " (not yet numbered)."))
    return out


def credit_memo_warnings(creditor_id: int, memo_ref: str, memo_date, amount: float) -> list[str]:
    with _conn() as conn:
        hit = conn.execute(
            """SELECT cm.memo_ref, os.statement_ref FROM credit_memos cm JOIN order_statements os ON os.statement_id = cm.statement_id
               WHERE os.creditor_id = ? AND (LOWER(cm.memo_ref) = LOWER(?) OR (ABS(cm.amount - ?) < 0.005 AND cm.memo_date = ?)) LIMIT 1""",
            (creditor_id, memo_ref.strip(), float(amount), str(memo_date)),
        ).fetchone()
    return [f"This supplier already has credit memo {hit[0]} (batch {hit[1]}) with the same reference, or the same amount on the same date."] if hit else []


def loan_note_warnings(creditor_id: int, issue_date, principal: float) -> list[str]:
    with _conn() as conn:
        hit = conn.execute(
            """SELECT note_ref, issue_date FROM loan_notes WHERE creditor_id = ? AND status != 'Cancelled'
               AND ABS(COALESCE(principal_amount, 0) - ?) < 0.005 AND ABS(JULIANDAY(issue_date) - JULIANDAY(?)) <= 31 LIMIT 1""",
            (creditor_id, float(principal), str(issue_date)),
        ).fetchone()
    return [f"This lender already has loan note {hit[0]} for {_php(principal)} dated {hit[1]}, within a month of this one. "
            "A second loan of the same amount is fine if it is backed by its own separate loan note -- otherwise it may be a double entry."] if hit else []


def loan_installment_warnings(loan_note_id: int, rows: list) -> list[str]:
    """rows: (due_date, principal, interest)."""
    out = []
    with _conn() as conn:
        for due, principal, interest in rows:
            hit = conn.execute(
                "SELECT installment_no FROM loan_installments WHERE loan_note_id = ? AND due_date = ? LIMIT 1",
                (loan_note_id, str(due)),
            ).fetchone()
            if hit:
                out.append(f"This loan note already has installment #{hit[0]} due {due}.")
    return out


def bank_loan_warnings(creditor_id: int, account_number: str | None, principal: float, start_date) -> list[str]:
    out = []
    with _conn() as conn:
        if account_number and account_number.strip():
            hit = conn.execute("SELECT loan_ref FROM bank_loans WHERE account_number = ? AND is_cancelled = 0 LIMIT 1", (account_number.strip(),)).fetchone()
            if hit:
                out.append(f"Loan {hit[0]} already uses this account number.")
        hit = conn.execute(
            """SELECT loan_ref FROM bank_loans WHERE creditor_id = ? AND is_cancelled = 0
               AND ABS(COALESCE(principal_amount, 0) - ?) < 0.005 AND start_date = ? LIMIT 1""",
            (creditor_id, float(principal), str(start_date)),
        ).fetchone()
        if hit:
            out.append(f"This bank already has loan {hit[0]} with the same principal and start date.")
    return out


def bank_schedule_warnings(bank_loan_id: int, rows: list) -> list[str]:
    """rows: (due_date, amount) about to be added to this loan. Flags a due date already on this
    loan, and a schedule that matches another loan's (the same schedule loaded onto two loans)."""
    out = []
    pairs = {(str(d)[:10], round(float(a), 2)) for d, a in rows}
    with _conn() as conn:
        for due, _ in rows:
            if conn.execute("SELECT 1 FROM bank_loan_installments WHERE bank_loan_id = ? AND due_date = ? LIMIT 1", (bank_loan_id, str(due)[:10])).fetchone():
                out.append(f"This loan already has an installment due {str(due)[:10]}.")
        need = min(3, len(pairs))
        if need:
            for other_id, ref in conn.execute("SELECT bank_loan_id, loan_ref FROM bank_loans WHERE bank_loan_id != ? AND is_cancelled = 0", (bank_loan_id,)).fetchall():
                theirs = {(r[0][:10], round(r[1], 2)) for r in conn.execute("SELECT due_date, amount FROM bank_loan_installments WHERE bank_loan_id = ?", (other_id,))}
                if len(pairs & theirs) >= need:
                    out.append(f"{len(pairs & theirs)} of these installments (same date and amount) are already on loan {ref} -- the same schedule may be on two loans.")
    return out


def cc_statement_warnings(card_id: int, due_date, total_due: float) -> list[str]:
    """Same card, same due date and same amount due = the same statement recorded twice."""
    with _conn() as conn:
        hit = conn.execute(
            """SELECT statement_month, due_date FROM cc_statements WHERE card_id = ? AND status != 'Cancelled'
               AND due_date = ? AND ABS(total_amount_due - ?) < 0.005 LIMIT 1""",
            (card_id, str(due_date), float(total_due)),
        ).fetchone()
    return [f"This card already has a statement due {hit[1]} for the same amount due ({_php(total_due)})."] if hit else []


def cc_statement_month_warnings(card_id: int, statement_month: str, total_due: float, due_date=None) -> list[str]:
    """One statement per card per month: a second one for the same month is almost always the same statement typed
    twice. Also flags a total far from the card's previous statement (a typo like an extra zero).
    With `due_date` (owner, 2026-09-29): the months compared are STATEMENT-DATE months worked out from each due date and the card's
    statement day (engines/cc_dates.py) -- old statements were mostly labelled with their due month, which would raise false alarms."""
    out = []
    with _conn() as conn:
        if due_date is not None:
            from engines.cc_dates import statement_month_for

            day_row = conn.execute("SELECT statement_day FROM credit_cards WHERE card_id = ?", (card_id,)).fetchone()
            day = day_row[0] if day_row else None
            new_month = statement_month_for(due_date, day)
            same = next((r for r in conn.execute(
                "SELECT due_date, total_amount_due FROM cc_statements WHERE card_id = ? AND status != 'Cancelled' AND due_date IS NOT NULL",
                (card_id,)) if statement_month_for(r[0], day) == new_month), None)
            statement_month = new_month
        else:
            same = conn.execute(
                """SELECT due_date, total_amount_due FROM cc_statements WHERE card_id = ? AND status != 'Cancelled'
                   AND COALESCE(statement_month, SUBSTR(due_date, 1, 7)) = ? LIMIT 1""",
                (card_id, statement_month),
            ).fetchone()
        last = conn.execute(
            "SELECT total_amount_due FROM cc_statements WHERE card_id = ? AND status != 'Cancelled' ORDER BY due_date DESC, cc_statement_id DESC LIMIT 1",
            (card_id,),
        ).fetchone()
    if same:
        out.append(f"This card already has a statement for {statement_month} (due {same[0]}, {_php(same[1])}). A newer statement for the same month replaces it -- edit that one instead unless this is really a second statement.")
    if last and last[0] and float(last[0]) > 0 and (float(total_due) > 2 * float(last[0]) or float(total_due) < 0.5 * float(last[0])):
        out.append(f"The total {_php(total_due)} is very different from this card's last statement ({_php(last[0])}). Please double-check the digits.")
    return out


def credit_card_warnings(issuer_id_or_name, card_name: str, last4: str | None) -> list[str]:
    with _conn() as conn:
        hit = conn.execute(
            "SELECT card_name, card_number_last4 FROM credit_cards WHERE LOWER(card_name) = LOWER(?) AND COALESCE(card_number_last4, '') = COALESCE(?, '') LIMIT 1",
            (card_name.strip(), (last4 or "").strip()),
        ).fetchone()
    return [f"A card named {hit[0]} ({hit[1] or 'no last 4'}) is already on file."] if hit else []


def creditor_warnings(name: str, creditor_type: str) -> list[str]:
    with _conn() as conn:
        hit = conn.execute(
            "SELECT creditor_name, creditor_type FROM creditors WHERE LOWER(TRIM(creditor_name)) = LOWER(TRIM(?)) LIMIT 1", (name,),
        ).fetchone()
    return [f"'{hit[0]}' is already on file (as {hit[1].replace('_', ' ').title()})."] if hit else []


def loan_check_mismatches(loan_note_id: int, proposed: dict | None = None) -> list[str]:
    """A loan note's checks must match its repayment schedule: each check pays one installment, for
    that row's principal + interest, on that row's due date (e.g. principal 100,000 + interest 9,000
    on the first date -> a 109,000 check; principal 100,000 + interest 6,000 on the next -> 106,000).
    Returns one line per Active check that doesn't. `proposed` ({check_id: (amount, check_date)})
    tests edits that haven't been saved yet, and then ONLY those checks are reported."""
    out = []
    with _conn() as conn:
        rows = conn.execute(
            """SELECT c.check_id, c.check_number, c.amount, c.check_date, li.installment_no, li.principal, li.interest, li.due_date
               FROM checks_issued c LEFT JOIN loan_installments li ON li.check_id = c.check_id
               WHERE c.loan_note_id = ? AND c.status = 'Active' ORDER BY c.check_date""",
            (loan_note_id,),
        ).fetchall()
    with _conn() as conn:
        has_schedule = conn.execute("SELECT 1 FROM loan_installments WHERE loan_note_id = ? LIMIT 1", (loan_note_id,)).fetchone() is not None
    for check_id, number, amount, check_date, inst_no, principal, interest, due in rows:
        if proposed is not None and check_id not in proposed:
            continue
        if proposed and check_id in proposed:
            amount, check_date = float(proposed[check_id][0]), str(proposed[check_id][1])[:10]
        label = f"Check {number or '(not yet numbered)'}"
        if inst_no is None:
            if not has_schedule:
                continue     # migrated note with no repayment schedule on file -- nothing to match against
            out.append(f"{label} for {_php(amount)} dated {str(check_date)[:10]} is not tied to any row of the repayment schedule.")
            continue
        expected = float(principal) + float(interest)
        problems = []
        if abs(float(amount) - expected) > 0.005:
            problems.append(f"amount is {_php(amount)} but the schedule says {_php(expected)} (principal {_php(principal)} + interest {_php(interest)})")
        if str(check_date)[:10] != str(due)[:10]:
            problems.append(f"dated {str(check_date)[:10]} but the installment is due {str(due)[:10]}")
        if problems:
            out.append(f"{label} (installment #{inst_no}): " + "; ".join(problems) + ".")
    return out


