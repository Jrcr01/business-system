"""Sidebar quick-search (Phase 5 follow-up, scoped 2026-09-06).

The user's real usage is searching a check number or an invoice doc ref and jumping straight to
the batch/loan note it belongs to -- not a generic full-text search across the whole system.
Confirmed via AskUserQuestion: check number and invoice doc ref are the primary search targets
(checked first); Order Statement ref, Loan Note ref, Bank Loan ref, and supplier/lender name are
secondary, checked only if nothing matched as a check or invoice. A single match auto-jumps
straight to the record's specific sub-tab (Checks for a check number, Invoices for a doc ref);
multiple matches return a pick-list instead of guessing; zero matches say so plainly; a check
number that's actually logged as spoiled gets its own explanation rather than a generic
not-found, since that's a real, correct answer, not a miss.
"""
import sqlite3

import os
from engines.common import get_conn
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def search_records(term: str) -> dict:
    """Returns {"kind": ..., "matches": [...]}.

    kind is one of:
    - "check": matches from checks_issued (any status -- full history stays searchable, not just
      active checks, so a cancelled/replaced check is still found and still jumps to its batch).
    - "spoiled": the number belongs to the spoiled-check log instead of a real issued check.
    - "invoice": matches from invoices.doc_ref (not guaranteed unique in this system, hence a
      pick-list is a real possibility here even for one search term).
    - "ref": matches from Order Statement ref, Loan Note ref, Bank Loan ref, or supplier/lender
      name -- checked only once check/invoice/spoiled all come up empty.
    - "none": nothing matched anywhere.
    """
    term = (term or "").strip()
    if not term:
        return {"kind": "none", "matches": []}

    like = f"%{term}%"
    with get_conn(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row

        checks = conn.execute(
            """SELECT c.check_id, c.check_number, c.check_date, c.amount, c.status,
                      cr.creditor_name, c.statement_id, c.loan_note_id,
                      os.statement_ref, ln.note_ref
               FROM checks_issued c
               JOIN creditors cr ON c.creditor_id = cr.creditor_id
               LEFT JOIN order_statements os ON c.statement_id = os.statement_id
               LEFT JOIN loan_notes ln ON c.loan_note_id = ln.loan_note_id
               WHERE c.check_number LIKE ?
               ORDER BY c.check_date DESC""",
            (like,),
        ).fetchall()
        if checks:
            return {"kind": "check", "matches": [dict(r) for r in checks]}

        spoiled = conn.execute(
            "SELECT spoiled_id, check_number, reason, logged_at FROM spoiled_checks WHERE check_number LIKE ?",
            (like,),
        ).fetchall()
        if spoiled:
            return {"kind": "spoiled", "matches": [dict(r) for r in spoiled]}

        invoices = conn.execute(
            """SELECT i.invoice_id, i.doc_ref, i.amount, i.due_date, i.status,
                      cr.creditor_name, i.statement_id, os.statement_ref
               FROM invoices i
               JOIN order_statements os ON i.statement_id = os.statement_id
               JOIN creditors cr ON i.creditor_id = cr.creditor_id
               WHERE i.doc_ref LIKE ?
               ORDER BY i.invoice_date DESC""",
            (like,),
        ).fetchall()
        if invoices:
            return {"kind": "invoice", "matches": [dict(r) for r in invoices]}

        refs = []
        for row in conn.execute(
            """SELECT os.statement_id AS id, os.statement_ref AS ref, cr.creditor_name AS payee,
                      os.total_amount AS amount, os.status
               FROM order_statements os JOIN creditors cr ON os.creditor_id = cr.creditor_id
               WHERE os.statement_ref LIKE ? OR cr.creditor_name LIKE ?""",
            (like, like),
        ).fetchall():
            refs.append({"type": "Order Statement", **dict(row)})
        for row in conn.execute(
            """SELECT ln.loan_note_id AS id, ln.note_ref AS ref, cr.creditor_name AS payee,
                      ln.total_payable AS amount, ln.status
               FROM loan_notes ln JOIN creditors cr ON ln.creditor_id = cr.creditor_id
               WHERE ln.note_ref LIKE ? OR cr.creditor_name LIKE ?""",
            (like, like),
        ).fetchall():
            refs.append({"type": "Loan Note", **dict(row)})
        for row in conn.execute(
            """SELECT bl.bank_loan_id AS id, bl.loan_ref AS ref, cr.creditor_name AS payee,
                      bl.principal_amount AS amount, bl.status
               FROM bank_loans bl JOIN creditors cr ON bl.creditor_id = cr.creditor_id
               WHERE bl.loan_ref LIKE ? OR cr.creditor_name LIKE ?""",
            (like, like),
        ).fetchall():
            refs.append({"type": "Bank Loan", **dict(row)})
        if refs:
            return {"kind": "ref", "matches": refs}

    return {"kind": "none", "matches": []}
