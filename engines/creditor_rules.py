"""Creditors (suppliers, lenders, banks, card issuers): add, edit, deactivate/delete.

Moved out of app.py unchanged (2026-09-27) so the rules can be read and tested without the screens; app.py imports
every name back, so app.<name> keeps working."""
from __future__ import annotations


from engines.bank_names import expand_bank_name
from engines.name_case import smart_proper_case
from engines.common import get_conn
from engines.statement_rules import DEFAULT_SUPPLIER_TERMS


CREDITOR_REFERENCE_TABLES = (
    "order_statements", "invoices", "loan_notes", "bank_loans", "checks_issued", "credit_cards",
)


def storage_type(creditor_type: str) -> tuple[str, int]:
    """('LESSOR' -> ('LENDER', 1)): a lessor is stored as a LENDER-type creditor flagged is_lessor (the type list is a CHECK constraint used everywhere)."""
    return ("LENDER", 1) if creditor_type == "LESSOR" else (creditor_type, 0)


def add_creditor(name: str, creditor_type: str, tin: str, contact_person: str, contact_number: str, email: str, terms: str = "") -> int:
    creditor_type, is_lessor = storage_type(creditor_type)
    if creditor_type in ("BANK_LOAN", "CREDIT_CARD"):
        name = expand_bank_name(name)
    name = smart_proper_case(name)  # ALL CAPS / all lowercase becomes proper case; mixed case is left as typed
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO creditors (creditor_name, creditor_type, tin, contact_person, contact_number, email, terms, is_lessor)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (name, creditor_type, tin, contact_person, contact_number, email, terms or (DEFAULT_SUPPLIER_TERMS if creditor_type == "SUPPLIER" else None), is_lessor),
        )
        conn.commit()
        return cur.lastrowid


def get_or_create_card_issuer(name: str) -> int:
    """Issuer as a plain attribute of a card (2026-09-19): the card form just takes a bank name;
    this finds the matching CREDIT_CARD creditor behind the scenes (case-insensitive, reactivating
    one that was deactivated) or creates it. The creditors table stays as the storage, but nobody
    has to manage issuers as records of their own anymore."""
    clean = expand_bank_name(name)
    if not clean:
        raise ValueError("Issuer name is required.")
    with get_conn() as conn:
        row = conn.execute(
            "SELECT creditor_id, is_active FROM creditors WHERE creditor_type = 'CREDIT_CARD' AND LOWER(creditor_name) = LOWER(?) ORDER BY is_active DESC LIMIT 1",
            (clean,),
        ).fetchone()
        if row:
            if not row[1]:
                conn.execute("UPDATE creditors SET is_active = 1 WHERE creditor_id = ?", (row[0],))
                conn.commit()
            return row[0]
    return add_creditor(clean, "CREDIT_CARD", "", "", "", "")


def update_creditor(creditor_id: int, name: str, creditor_type: str, tin: str, contact_person: str, contact_number: str, email: str, terms: str) -> None:
    """Edits a creditor's full record, including creditor_type and terms — both now editable
    in-place in the Creditor Master grid. A type change is only safe to apply for a creditor with
    no transaction history yet (checked by the caller via creditor_reference_counts()), since
    reclassifying one that's already in use would silently move it out of the pickers that filter
    by type elsewhere (batches, loan notes, bank loans)."""
    creditor_type, is_lessor = storage_type(creditor_type)
    if creditor_type in ("BANK_LOAN", "CREDIT_CARD"):
        name = expand_bank_name(name)
    name = smart_proper_case(name)
    with get_conn() as conn:
        conn.execute(
            "UPDATE creditors SET creditor_name = ?, creditor_type = ?, is_lessor = ?, tin = ?, contact_person = ?, contact_number = ?, email = ?, terms = ? WHERE creditor_id = ?",
            (name, creditor_type, is_lessor, tin, contact_person, contact_number, email, terms or None, creditor_id),
        )
        conn.commit()


def creditor_reference_counts(creditor_id: int) -> dict:
    """How many rows in each transactional table point at this creditor -- the basis for deciding
    hard-delete (zero references anywhere, safe to remove outright) vs. soft-delete/inactivate
    (real history exists, removing the row would break it)."""
    counts = {}
    with get_conn() as conn:
        for table in CREDITOR_REFERENCE_TABLES:
            n = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE creditor_id = ?", (creditor_id,)).fetchone()[0]
            if n:
                counts[table] = n
    return counts


def delete_or_deactivate_creditor(creditor_id: int) -> tuple[bool, str]:
    """Hard-deletes a creditor with zero references anywhere; otherwise soft-deletes it
    (is_active = 0) so its transaction history stays intact but it stops appearing as pickable
    for new batches/loan notes/bank loans (creditor_options() already filters on is_active)."""
    refs = creditor_reference_counts(creditor_id)
    with get_conn() as conn:
        if refs:
            conn.execute("UPDATE creditors SET is_active = 0 WHERE creditor_id = ?", (creditor_id,))
            conn.commit()
            detail = ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in refs.items())
            return True, f"Creditor has existing history ({detail}) — marked inactive instead of deleted. It stays visible here and keeps its history, just won't appear when starting new transactions."
        conn.execute("DELETE FROM creditors WHERE creditor_id = ?", (creditor_id,))
        conn.commit()
        return True, "Creditor had no transaction history — deleted outright."


def reactivate_creditor(creditor_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE creditors SET is_active = 1 WHERE creditor_id = ?", (creditor_id,))
        conn.commit()


def deactivate_creditor(creditor_id: int) -> None:
    """Plain, reversible is_active toggle — used when unchecking Active in the Creditor Master
    grid. Deliberately not delete_or_deactivate_creditor(): a checkbox uncheck should always just
    flip the flag (reversible by checking it again), never hard-delete."""
    with get_conn() as conn:
        conn.execute("UPDATE creditors SET is_active = 0 WHERE creditor_id = ?", (creditor_id,))
        conn.commit()
