"""Lessors and leases (plan D5, 2026-10-01): rent we pay. "Checks determine when it is due (we just issue checks to cover the monthly rents)" -- so a LEASE is a loan note with no
interest whose repayment schedule IS the rent checks, flagged is_lease = 1 (loan_notes) and its lessor flagged is_lessor = 1 (creditors, stored as a LENDER). Everything a loan note
already does -- schedule rows, parked checks, Chrysanth, numbering, handwritten checks, bank matching, voiding -- therefore works for rent unchanged; this module adds only what is
specific: creating a lease, adding rent checks, converting the two lenders that are really lessors, and the lists the Lessors page shows."""
from __future__ import annotations

from engines.clock import manila_today
from engines.common import get_conn
import os
import sqlite3
from datetime import date

import pandas as pd

from engines.common import php

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def _audit(conn, action: str, table: str, rec_id, text: str) -> None:
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, ?, ?, ?)", (action, table, rec_id, text))


def _next_lease_ref(conn) -> str:
    prefix = f"{manila_today().year} - LS "
    seqs = [int(r[0][len(prefix):]) for r in conn.execute("SELECT note_ref FROM loan_notes WHERE note_ref LIKE ?", (prefix + "%",)) if r[0][len(prefix):].strip().isdigit()]
    return f"{prefix}{(max(seqs) + 1 if seqs else 1):04d}"


# ------------------------------------------------------------------------------------------------------------ reading
def leases(include_cancelled: bool = False) -> pd.DataFrame:
    """One row per lease: lease_id (= its loan note id), ref, lessor, description, status, rent checks (active), total of those checks, last check amount, next check date."""
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(
            """SELECT n.loan_note_id AS lease_id, n.note_ref AS ref, n.creditor_id, c.creditor_name AS lessor, COALESCE(n.lease_description, '') AS description, n.status,
                      (SELECT COUNT(*) FROM checks_issued k WHERE k.loan_note_id = n.loan_note_id AND k.status = 'Active') AS checks,
                      COALESCE((SELECT SUM(k.amount) FROM checks_issued k WHERE k.loan_note_id = n.loan_note_id AND k.status = 'Active'), 0) AS total,
                      (SELECT k.amount FROM checks_issued k WHERE k.loan_note_id = n.loan_note_id AND k.status = 'Active' ORDER BY k.check_date DESC, k.check_id DESC LIMIT 1) AS last_rent,
                      (SELECT MIN(k.check_date) FROM checks_issued k WHERE k.loan_note_id = n.loan_note_id AND k.status = 'Active' AND k.check_date >= manila_today()) AS next_check
               FROM loan_notes n JOIN creditors c ON c.creditor_id = n.creditor_id
               WHERE n.is_lease = 1""" + ("" if include_cancelled else " AND n.status != 'Cancelled'") + " ORDER BY c.creditor_name, n.loan_note_id", conn)


def lease_checks(lease_id: int) -> pd.DataFrame:
    with get_conn(DB_PATH) as conn:
        return pd.read_sql_query(
            """SELECT k.check_id, k.check_number, k.check_date, k.amount, k.status, k.bank_cleared_date,
                      CASE WHEN k.is_handwritten = 1 THEN 'handwritten' WHEN k.printed_at IS NOT NULL THEN 'printed' WHEN k.check_number IS NULL THEN 'parked' ELSE 'to print' END AS how
               FROM checks_issued k WHERE k.loan_note_id = ? ORDER BY k.check_date, k.check_id""", conn, params=(int(lease_id),))


# ------------------------------------------------------------------------------------------------------------ writing
def create_lease(lessor_id: int, description: str, rent_checks: list[tuple[date, float]], issue_date: date | None = None) -> tuple[bool, str, int | None]:
    """A new lease for a lessor with its first rent checks (parked, no number yet). rent_checks: [(check date, amount), ...] -- at least one."""
    rows = [(d, round(float(a), 2)) for d, a in rent_checks if a and float(a) > 0]
    if not rows:
        return False, "Enter at least one rent check (date and amount).", None
    with get_conn(DB_PATH) as conn:
        row = conn.execute("SELECT creditor_name, is_lessor FROM creditors WHERE creditor_id = ?", (lessor_id,)).fetchone()
        if not row:
            return False, "Lessor not found.", None
        if not row[1]:
            return False, f"{row[0]} is not a lessor. Add it on the Creditors page with Type = Lessor.", None
        ref = _next_lease_ref(conn)
        total = round(sum(a for _, a in rows), 2)
        cur = conn.execute("""INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, monthly_interest_rate, total_interest, total_payable, status, is_lease, lease_description)
                              VALUES (?, ?, ?, ?, 0, 0, ?, 'Active', 1, ?)""", (lessor_id, ref, str(issue_date or rows[0][0]), total, total, (description or "").strip() or None))
        lease_id = cur.lastrowid
        _audit(conn, "LEASE_CREATED", "loan_notes", lease_id, f"{ref}: lease for {row[0]}" + (f" ({description.strip()})" if (description or "").strip() else ""))
    ok, msg, ids = _issue_rows(lease_id, lessor_id, rows, raise_principal=False)
    if not ok:
        return False, f"Lease {ref} was created, but: {msg}", lease_id
    return True, f"Lease {ref} created with {len(ids)} rent check(s), {php(total)}.", lease_id


def add_rent_checks(lease_id: int, rent_checks: list[tuple[date, float]]) -> tuple[bool, str, list[int]]:
    """More rent checks on an existing lease (parked): the lease total and schedule grow with them."""
    rows = [(d, round(float(a), 2)) for d, a in rent_checks if a and float(a) > 0]
    if not rows:
        return False, "Enter at least one rent check (date and amount).", []
    with get_conn(DB_PATH) as conn:
        row = conn.execute("SELECT creditor_id, status, is_lease FROM loan_notes WHERE loan_note_id = ?", (lease_id,)).fetchone()
    if not row or not row[2]:
        return False, "Lease not found.", []
    if row[1] not in ("Active", "Pending Setup"):
        return False, f"A {row[1]} lease cannot take more rent checks.", []
    return _issue_rows(lease_id, row[0], rows, raise_principal=True)


def _issue_rows(lease_id: int, lessor_id: int, rows: list[tuple[date, float]], raise_principal: bool) -> tuple[bool, str, list[int]]:
    from engines.check_rules import issue_check
    from engines.loan_schedule_service import add_loan_installment, edit_loan_note_terms

    with get_conn(DB_PATH) as conn:
        principal = conn.execute("SELECT COALESCE(principal_amount, 0) FROM loan_notes WHERE loan_note_id = ?", (lease_id,)).fetchone()[0]
    if raise_principal:
        ok, msg = edit_loan_note_terms(lease_id, round(float(principal) + sum(a for _, a in rows), 2), 0.0)
        if not ok:
            return False, msg, []
    ids: list[int] = []
    for d, amount in rows:
        ok, msg = add_loan_installment(lease_id, d, amount, 0.0)
        if not ok:
            return False, f"{msg} ({len(ids)} of {len(rows)} saved)", ids
        with get_conn(DB_PATH) as conn:
            inst = conn.execute("SELECT installment_id FROM loan_installments WHERE loan_note_id = ? AND due_date = ? ORDER BY installment_id DESC LIMIT 1", (lease_id, str(d))).fetchone()[0]
        ok, msg, cid = issue_check(lessor_id, amount, d, loan_note_id=lease_id, installment_id=inst)
        if not ok:
            return False, f"{msg} ({len(ids)} of {len(rows)} saved)", ids
        ids.append(cid)
    return True, f"{len(ids)} rent check(s) added.", ids


# ------------------------------------------------------------------------------------------------------------ converting lenders that are really lessors
def conversion_preview(creditor_ids: list[int]) -> pd.DataFrame:
    """What converting these lenders to lessors would change: each loan note that becomes a lease, with its checks. Writes nothing."""
    out = []
    with get_conn(DB_PATH) as conn:
        for cid in creditor_ids:
            for lid, ref, status, name, ctype, is_lessor in conn.execute(
                    """SELECT n.loan_note_id, n.note_ref, n.status, c.creditor_name, c.creditor_type, c.is_lessor FROM loan_notes n JOIN creditors c ON c.creditor_id = n.creditor_id
                       WHERE n.creditor_id = ? AND n.status != 'Cancelled' ORDER BY n.loan_note_id""", (cid,)):
                n, total = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM checks_issued WHERE loan_note_id = ? AND status = 'Active'", (lid,)).fetchone()
                interest = conn.execute("SELECT COALESCE(SUM(interest), 0) FROM loan_installments WHERE loan_note_id = ?", (lid,)).fetchone()[0]
                out.append({"creditor_id": cid, "lessor": name, "loan_note_id": lid, "note": ref, "status": status, "active_checks": n, "checks_total": float(total),
                            "interest_in_schedule": float(interest), "already_lessor": bool(is_lessor), "type": ctype})
    return pd.DataFrame(out, columns=["creditor_id", "lessor", "loan_note_id", "note", "status", "active_checks", "checks_total", "interest_in_schedule", "already_lessor", "type"])


def convert_to_lessors(creditor_ids: list[int], description: str = "") -> tuple[bool, str]:
    """Turn lenders into lessors: the creditor is flagged, every one of its loan notes becomes a lease. Nothing about a check changes (number, date, amount, state, link).
    Refused for a creditor that is not a lender, or whose loan note has interest in its schedule (that is a real loan, not rent)."""
    prev = conversion_preview(creditor_ids)
    if prev.empty:
        return False, "Nothing to convert: no loan notes found for those lenders."
    if (prev["type"] != "LENDER").any():
        return False, "Only lenders can be converted to lessors."
    bad = prev[prev["interest_in_schedule"] > 0.005]
    if not bad.empty:
        return False, f"{bad.iloc[0]['note']} ({bad.iloc[0]['lessor']}) has interest in its schedule, so it is a real loan, not rent."
    with get_conn(DB_PATH) as conn:
        for cid in sorted(set(creditor_ids)):
            conn.execute("UPDATE creditors SET is_lessor = 1 WHERE creditor_id = ?", (cid,))
        for r in prev.itertuples():
            conn.execute("UPDATE loan_notes SET is_lease = 1, lease_description = COALESCE(NULLIF(lease_description, ''), ?) WHERE loan_note_id = ?", (description.strip() or None, r.loan_note_id))
            _audit(conn, "LEASE_CONVERTED", "loan_notes", int(r.loan_note_id),
                   f"{r.note} ({r.lessor}) is now a lease: {int(r.active_checks)} active check(s), {php(r.checks_total)}; the lender became a lessor; no check changed")
    names = ", ".join(sorted(set(prev["lessor"])))
    return True, f"{len(prev)} loan note(s) of {names} are now leases; {int(prev['active_checks'].sum())} checks kept unchanged."
