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
TYPE_LABEL = {"SUPPLIER": "Supplier", "LENDER": "Lender", "LESSOR": "Lessor", "BANK_LOAN": "Bank loan", "CREDIT_CARD": "Card issuer"}
EDITABLE_TYPES = ("SUPPLIER", "LENDER", "LESSOR", "BANK_LOAN")
_FIELDS = ("creditor_name", "creditor_type", "is_lessor", "tin", "contact_person", "contact_number", "email", "terms", "lead_days", "is_active")


def _audit(conn, action: str, creditor_id: int, text: str, old: str | None = None) -> None:
    """Who added, changed, switched off or removed a creditor (2026-10-03: with several people working, every creditor change is in the audit log)."""
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values, new_values) VALUES (acting_user(), ?, 'creditors', ?, ?, ?)",
                 (action, creditor_id, old, text))


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
        new_id = cur.lastrowid
        _audit(conn, "CREDITOR_ADDED", new_id, f"{name} ({TYPE_LABEL.get('LESSOR' if is_lessor else creditor_type, creditor_type)})")
        conn.commit()
    _flag_new_payee(new_id, name, creditor_type)
    return new_id


def _flag_new_payee(creditor_id: int, name: str, creditor_type: str) -> None:
    """Anyone but the System admin may add a supplier or lender and use it at once; it lands on the Review list so the owner notices a new payee before a check is printed."""
    from engines.acting_user import acting_role
    if creditor_type not in ("SUPPLIER", "LENDER") or (acting_role() or "admin") == "admin":
        return
    from engines.common import write_tx
    from engines.review_flags import flag
    with write_tx() as conn:
        flag(conn, "NEW_PAYEE", "creditors", creditor_id, f"New {creditor_type.lower()}: {name}")


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
        before = _row(conn, creditor_id)
        conn.execute(
            "UPDATE creditors SET creditor_name = ?, creditor_type = ?, is_lessor = ?, tin = ?, contact_person = ?, contact_number = ?, email = ?, terms = ? WHERE creditor_id = ?",
            (name, creditor_type, is_lessor, tin, contact_person, contact_number, email, terms or None, creditor_id),
        )
        _audit_change(conn, creditor_id, before)
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
        name = _name(conn, creditor_id)
        if refs:
            conn.execute("UPDATE creditors SET is_active = 0 WHERE creditor_id = ?", (creditor_id,))
            _audit(conn, "CREDITOR_SWITCHED_OFF", creditor_id, f"{name}: switched off (has history)")
            conn.commit()
            detail = ", ".join(f"{n} {t.replace('_', ' ')}" for t, n in refs.items())
            return True, f"Creditor has existing history ({detail}) — marked inactive instead of deleted. It stays visible here and keeps its history, just won't appear when starting new transactions."
        conn.execute("DELETE FROM creditors WHERE creditor_id = ?", (creditor_id,))
        _audit(conn, "CREDITOR_DELETED", creditor_id, f"{name}: deleted (no history)")
        conn.commit()
        return True, "Creditor had no transaction history — deleted outright."


def reactivate_creditor(creditor_id: int) -> None:
    with get_conn() as conn:
        conn.execute("UPDATE creditors SET is_active = 1 WHERE creditor_id = ?", (creditor_id,))
        _audit(conn, "CREDITOR_SWITCHED_ON", creditor_id, _name(conn, creditor_id) + ": switched on")
        conn.commit()


def deactivate_creditor(creditor_id: int) -> None:
    """Plain, reversible is_active toggle — used when unchecking Active in the Creditor Master
    grid. Deliberately not delete_or_deactivate_creditor(): a checkbox uncheck should always just
    flip the flag (reversible by checking it again), never hard-delete."""
    with get_conn() as conn:
        conn.execute("UPDATE creditors SET is_active = 0 WHERE creditor_id = ?", (creditor_id,))
        _audit(conn, "CREDITOR_SWITCHED_OFF", creditor_id, _name(conn, creditor_id) + ": switched off")
        conn.commit()


def set_lead_days(creditor_id: int, days: int | None) -> tuple[bool, str]:
    """The supplier's usual delivery days (0-365), or None for 'not set' (a new purchase order then starts at a week). Audited."""
    if days is not None:
        try:
            days = int(days)
        except (TypeError, ValueError):
            return False, "Enter a whole number of days."
        if days < 0 or days > 365:
            return False, "Usual delivery days must be between 0 and 365."
    with get_conn() as conn:
        row = conn.execute("SELECT creditor_name, lead_days FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone()
        if not row:
            return False, "Supplier not found."
        conn.execute("UPDATE creditors SET lead_days = ? WHERE creditor_id = ?", (days, creditor_id))
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, old_values, new_values) VALUES (acting_user(), 'CREDITOR_LEAD_DAYS', 'creditors', ?, ?, ?)",
                     (creditor_id, str(row[1]), f"{row[0]}: {days if days is not None else 'not set'}"))
        conn.commit()
    return True, f"{row[0]}: usual delivery " + (f"{days} day(s)." if days is not None else "not set (a week is assumed).")


# --- one creditor at a time, safe when several people edit (the web screens, 2026-10-03) ---------------------------------------------------------

def _name(conn, creditor_id: int) -> str:
    row = conn.execute("SELECT creditor_name FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone()
    return row[0] if row else f"#{creditor_id}"


def _row(conn, creditor_id: int) -> dict | None:
    row = conn.execute(f"SELECT {', '.join(_FIELDS)} FROM creditors WHERE creditor_id = ?", (creditor_id,)).fetchone()
    return dict(zip(_FIELDS, row)) if row else None


def _audit_change(conn, creditor_id: int, before: dict | None) -> None:
    after = _row(conn, creditor_id)
    if not before or not after:
        return
    changed = {k: (before[k], after[k]) for k in _FIELDS if k != "is_active" and (before[k] or None) != (after[k] or None)}
    if changed:
        _audit(conn, "CREDITOR_EDITED", creditor_id, "; ".join(f"{k}: {b!r} -> {a!r}" for k, (b, a) in changed.items()), old=before["creditor_name"])


def _has_history(conn, creditor_id: int) -> bool:
    return any(conn.execute(f"SELECT 1 FROM {t} WHERE creditor_id = ? LIMIT 1", (creditor_id,)).fetchone() for t in CREDITOR_REFERENCE_TABLES)


def stamp(row: dict) -> str:
    """A fingerprint of the creditor as it was shown: saving with an old fingerprint means someone else changed it in between."""
    import hashlib
    return hashlib.sha256(repr([row.get(k) for k in _FIELDS]).encode()).hexdigest()[:16]


def get_creditor(creditor_id: int) -> dict | None:
    """One creditor for the edit form: its fields (type LESSOR for a lessor), its history counts and its fingerprint."""
    with get_conn() as conn:
        row = _row(conn, creditor_id)
    if row is None:
        return None
    row["creditor_id"] = creditor_id
    row["type"] = "LESSOR" if row["is_lessor"] else row["creditor_type"]
    row["history"] = creditor_reference_counts(creditor_id)
    row["stamp"] = stamp(row)
    return row


def last_change(creditor_id: int) -> tuple[str, str] | None:
    """(who, when in Manila) of the newest audit row for this creditor."""
    with get_conn() as conn:
        row = conn.execute("SELECT user_email, datetime(timestamp, '+8 hours') FROM audit_logs WHERE entity_name = 'creditors' AND entity_id = ? ORDER BY log_id DESC LIMIT 1",
                           (creditor_id,)).fetchone()
    return (row[0] or "", row[1] or "") if row else None


def save_creditor(creditor_id: int, expected_stamp: str, name: str, creditor_type: str, tin: str, contact_person: str, contact_number: str, email: str,
                  terms: str, lead_days) -> tuple[bool, str]:
    """Saves the edit form in ONE locked step, only if nobody changed the creditor since the form was opened (else nothing is saved: the message is 'CHANGED').
    The type cannot change once the creditor has history. Audited (old -> new)."""
    from engines.common import write_tx
    name = " ".join((name or "").split())
    if not name:
        return False, "Enter the name."
    if creditor_type not in EDITABLE_TYPES:
        return False, "Pick a type."
    if lead_days not in (None, ""):
        try:
            lead_days = int(lead_days)
        except (TypeError, ValueError):
            return False, "Usual delivery days: enter a whole number, or leave it empty."
        if not 0 <= lead_days <= 365:
            return False, "Usual delivery days must be between 0 and 365."
    else:
        lead_days = None
    ctype, is_lessor = storage_type(creditor_type)
    if ctype in ("BANK_LOAN", "CREDIT_CARD"):
        name = expand_bank_name(name)
    name = smart_proper_case(name)
    with write_tx() as conn:
        before = _row(conn, creditor_id)
        if before is None:
            return False, "This creditor no longer exists."
        if stamp(before) != expected_stamp:
            return False, "CHANGED"
        old_type = "LESSOR" if before["is_lessor"] else before["creditor_type"]
        if creditor_type != old_type and _has_history(conn, creditor_id):
            return False, f"The type can't change: {before['creditor_name']} already has transactions. It stays {TYPE_LABEL[old_type]}."
        conn.execute("""UPDATE creditors SET creditor_name = ?, creditor_type = ?, is_lessor = ?, tin = ?, contact_person = ?, contact_number = ?, email = ?, terms = ?,
                        lead_days = ? WHERE creditor_id = ?""",
                     (name, ctype, is_lessor, (tin or "").strip(), (contact_person or "").strip(), (contact_number or "").strip(), (email or "").strip(),
                      (terms or None) if ctype == "SUPPLIER" else None, lead_days if ctype == "SUPPLIER" else before["lead_days"], creditor_id))
        _audit_change(conn, creditor_id, before)
    return True, f"{name}: saved."


def set_creditor_active(creditor_id: int, expected_stamp: str, active: bool) -> tuple[bool, str]:
    """Switch a creditor off (kept with all its history, not pickable for new work) or back on, only if nobody changed it since it was shown."""
    from engines.common import write_tx
    with write_tx() as conn:
        before = _row(conn, creditor_id)
        if before is None:
            return False, "This creditor no longer exists."
        if stamp(before) != expected_stamp:
            return False, "CHANGED"
        conn.execute("UPDATE creditors SET is_active = ? WHERE creditor_id = ?", (1 if active else 0, creditor_id))
        _audit(conn, "CREDITOR_SWITCHED_ON" if active else "CREDITOR_SWITCHED_OFF", creditor_id, f"{before['creditor_name']}: switched {'on' if active else 'off'}")
    return True, f"{before['creditor_name']}: switched {'on' if active else 'off'}."


def delete_creditor(creditor_id: int, expected_stamp: str) -> tuple[bool, str]:
    """Deletes a creditor that has no history at all (one with history can only be switched off)."""
    from engines.common import write_tx
    with write_tx() as conn:
        before = _row(conn, creditor_id)
        if before is None:
            return False, "This creditor no longer exists."
        if stamp(before) != expected_stamp:
            return False, "CHANGED"
        if _has_history(conn, creditor_id):
            return False, f"{before['creditor_name']} has transactions, so it can't be deleted. Switch it off instead."
        conn.execute("DELETE FROM creditors WHERE creditor_id = ?", (creditor_id,))
        _audit(conn, "CREDITOR_DELETED", creditor_id, f"{before['creditor_name']}: deleted (no history)")
    return True, f"{before['creditor_name']}: deleted."


def list_creditors(search: str = "", ctype: str = "", show_off: bool = False) -> list[dict]:
    """The Creditors list: suppliers, lenders, lessors and bank loans (card issuers live on Credit cards), filtered."""
    cols = ("creditor_id", "name", "type", "tin", "contact_person", "contact_number", "email", "terms", "lead_days", "is_active")
    with get_conn() as conn:
        rows = [dict(zip(cols, r)) for r in conn.execute(
            """SELECT creditor_id, creditor_name, CASE WHEN is_lessor = 1 THEN 'LESSOR' ELSE creditor_type END, tin, contact_person, contact_number, email, terms,
                      lead_days, is_active FROM creditors WHERE creditor_type IN ('SUPPLIER', 'LENDER', 'BANK_LOAN') ORDER BY creditor_type, is_lessor, creditor_name""")]
    needle = (search or "").strip().lower()
    out = []
    for r in rows:
        if ctype and r["type"] != ctype:
            continue
        if not show_off and not r["is_active"]:
            continue
        if needle and not any(needle in str(r[k] or "").lower() for k in ("name", "tin", "contact_person", "contact_number", "email")):
            continue
        out.append(r)
    return out
