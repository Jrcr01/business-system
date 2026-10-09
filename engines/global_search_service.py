"""Search everything (2026-09-27): one box, every kind of record -- checks, invoices, order statements, loan notes, bank loans, creditors,
credit cards and spoiled checks -- by number, reference, name, TIN/contact, or an exact amount. Read-only. Each result carries where to go:
{'page': <page id of the sidebar menu>, 'state': {...session values that open that record}}."""
from __future__ import annotations

import os
import sqlite3
from engines.common import get_conn
from engines.common import us_date

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

# page ids of the sidebar menu (the url_path of each st.Page in app.py)
TAB_CREDITOR_MASTER, TAB_ORDER_STATEMENT, TAB_LENDERS, TAB_BANK_LOANS, TAB_CREDIT_CARDS, TAB_CHECKS = (
    "creditors", "order-statements", "lenders", "bank-loans", "credit-cards", "checks")
LIMIT = 8


def _amount(term: str):
    text = term.replace(",", "").replace("\u20b1", "").replace("PHP", "").strip()
    try:
        return float(text)
    except ValueError:
        return None


def _php(v) -> str:
    return f"\u20b1{float(v):,.2f}"


def search_everything(term: str, limit: int = LIMIT) -> list[dict]:
    """[{'title': 'Checks', 'total': 14, 'rows': [{'label': ..., 'page': str, 'state': {...}}]}] -- only groups that matched."""
    t = (term or "").strip()
    if len(t) < 2:
        return []
    like = f"%{t}%"
    amt = _amount(t)
    a = amt if amt is not None else -1.0           # -1 never matches an amount (all amounts are >= 0)
    groups: list[dict] = []

    with get_conn(DB_PATH) as conn:
        def run(title, sql, params, build):
            rows = conn.execute(sql, params).fetchall()
            total = conn.execute(f"SELECT COUNT(*) FROM ({sql.rsplit(' LIMIT ', 1)[0]})", params).fetchone()[0] if len(rows) >= limit else len(rows)
            if rows:
                groups.append({"title": title, "total": total, "rows": [build(r) for r in rows]})

        run("Checks",
            f"""SELECT c.check_id, c.check_number, c.amount, c.status, c.check_date, cr.creditor_name, c.statement_id, c.loan_note_id,
                       COALESCE(os.statement_ref, ln.note_ref)
                FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
                LEFT JOIN order_statements os ON os.statement_id = c.statement_id LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
                WHERE c.check_number LIKE ? OR cr.creditor_name LIKE ? OR os.statement_ref LIKE ? OR ln.note_ref LIKE ? OR ABS(c.amount - ?) < 0.005
                ORDER BY c.check_date DESC, c.check_id DESC LIMIT {limit}""",
            (like, like, like, like, a),
            lambda r: {"label": f"{r[1] or 'no number yet'} \u00b7 {r[5]} \u00b7 {_php(r[2])} \u00b7 {us_date(r[4])} \u00b7 {r[3]} \u00b7 {r[8] or ''}",
                       **({"page": TAB_ORDER_STATEMENT, "state": {"selected_statement_id": r[6], "os_mode": "Browse batches", "os_subtab": "Checks",
                                                                     f"os_checks_view__{r[6]}": "Checks in this batch"}} if r[6] is not None
                          else {"page": TAB_LENDERS, "state": {"selected_loan_note_id": r[7], "ln_mode": "Browse loan notes", "ln_subtab": "Checks"}})})

        run("Invoices",
            f"""SELECT i.invoice_id, i.doc_ref, cr.creditor_name, i.amount, i.status, os.statement_ref, i.statement_id
                FROM invoices i JOIN creditors cr ON cr.creditor_id = i.creditor_id LEFT JOIN order_statements os ON os.statement_id = i.statement_id
                WHERE i.doc_ref LIKE ? OR cr.creditor_name LIKE ? OR ABS(i.amount - ?) < 0.005
                ORDER BY i.invoice_id DESC LIMIT {limit}""",
            (like, like, a),
            lambda r: {"label": f"{r[1]} \u00b7 {r[2]} \u00b7 {_php(r[3])} \u00b7 {r[4]} \u00b7 {r[5] or 'no batch'}", "page": TAB_ORDER_STATEMENT,
                       "state": {"selected_statement_id": r[6], "os_mode": "Browse batches", "os_subtab": "Invoices"}} if r[6] is not None
            else {"label": f"{r[1]} \u00b7 {r[2]} \u00b7 {_php(r[3])} \u00b7 no batch", "page": TAB_ORDER_STATEMENT, "state": {}})

        run("Order statements",
            f"""SELECT os.statement_id, os.statement_ref, cr.creditor_name, os.total_amount, os.status
                FROM order_statements os JOIN creditors cr ON cr.creditor_id = os.creditor_id
                WHERE os.statement_ref LIKE ? OR cr.creditor_name LIKE ? OR ABS(os.total_amount - ?) < 0.005
                ORDER BY os.statement_id DESC LIMIT {limit}""",
            (like, like, a),
            lambda r: {"label": f"{r[1]} \u00b7 {r[2]} \u00b7 {_php(r[3])} \u00b7 {r[4]}", "page": TAB_ORDER_STATEMENT,
                       "state": {"selected_statement_id": r[0], "os_mode": "Browse batches"}})

        run("Loan notes",
            f"""SELECT ln.loan_note_id, ln.note_ref, cr.creditor_name, ln.principal_amount, ln.status
                FROM loan_notes ln JOIN creditors cr ON cr.creditor_id = ln.creditor_id
                WHERE ln.note_ref LIKE ? OR cr.creditor_name LIKE ? OR ABS(COALESCE(ln.principal_amount, -1) - ?) < 0.005 OR ABS(COALESCE(ln.total_payable, -1) - ?) < 0.005
                ORDER BY ln.loan_note_id DESC LIMIT {limit}""",
            (like, like, a, a),
            lambda r: {"label": f"{r[1]} \u00b7 {r[2]} \u00b7 principal {_php(r[3]) if r[3] is not None else 'not set'} \u00b7 {r[4]}", "page": TAB_LENDERS,
                       "state": {"selected_loan_note_id": r[0], "ln_mode": "Browse loan notes"}})

        run("Bank loans",
            f"""SELECT bl.bank_loan_id, bl.loan_ref, cr.creditor_name, bl.account_number, lb.name
                FROM bank_loans bl JOIN creditors cr ON cr.creditor_id = bl.creditor_id LEFT JOIN loan_beneficiaries lb ON lb.beneficiary_id = bl.beneficiary_id
                WHERE bl.loan_ref LIKE ? OR cr.creditor_name LIKE ? OR bl.account_number LIKE ? OR lb.name LIKE ?
                ORDER BY bl.bank_loan_id DESC LIMIT {limit}""",
            (like, like, like, like),
            lambda r: {"label": f"{r[1]} \u00b7 {r[2]}" + (f" \u00b7 acct {r[3]}" if r[3] else "") + (f" \u00b7 {r[4]}" if r[4] else ""), "page": TAB_BANK_LOANS,
                       "state": {"selected_bank_loan_id": r[0], "bl_mode": "Browse bank loans"}})

        run("Creditors",
            f"""SELECT creditor_id, creditor_name, creditor_type, is_active FROM creditors
                WHERE creditor_name LIKE ? OR tin LIKE ? OR contact_person LIKE ? OR contact_number LIKE ? OR email LIKE ?
                ORDER BY creditor_name LIMIT {limit}""",
            (like, like, like, like, like),
            lambda r: {"label": f"{r[1]} \u00b7 {str(r[2]).replace('_', ' ').title()}" + ("" if r[3] else " \u00b7 inactive"), "page": TAB_CREDITOR_MASTER,
                       "state": {"creditor_master_search": r[1]}, "url": f"/creditors/{r[0]}/"})

        run("Credit cards",
            f"""SELECT cc.card_id, cc.card_name, cc.card_number_last4, ch.name, iss.creditor_name
                FROM credit_cards cc JOIN creditors iss ON iss.creditor_id = cc.creditor_id LEFT JOIN cardholders ch ON ch.cardholder_id = cc.cardholder_id
                WHERE cc.card_name LIKE ? OR cc.card_number_last4 LIKE ? OR ch.name LIKE ? OR iss.creditor_name LIKE ?
                ORDER BY cc.card_id DESC LIMIT {limit}""",
            (like, like, like, like),
            lambda r: {"label": f"{r[1]} \u00b7 {r[2] or ''} \u00b7 {r[3] or 'no cardholder'} \u00b7 {r[4]}", "page": TAB_CREDIT_CARDS,
                       "state": {"stmt_search": r[2] or r[1]}, "url": f"/credit-cards/?card={r[0]}&show=all"})

        run("Spoiled checks (error checks)",
            f"""SELECT spoiled_id, check_number, reason, logged_at FROM spoiled_checks WHERE check_number LIKE ? OR reason LIKE ?
                ORDER BY spoiled_id DESC LIMIT {limit}""",
            (like, like),
            lambda r: {"label": f"{r[1]} \u00b7 logged {us_date(r[3])}" + (f" \u00b7 {r[2]}" if r[2] else ""), "page": TAB_CHECKS, "state": {}, "url": "/bank-cash/?tab=booklets"})
    return groups
