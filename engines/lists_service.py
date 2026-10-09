"""Standard LISTS of the records themselves (plan F, 2026-10-01: "when I am asked who are our suppliers"). Reports so far listed transactions; these list the suppliers, lenders,
banks, batches, loan notes, bank loans, cards, booklets ... each with plain column names, an Active-only switch where a list has one, and one workbook with a sheet per list.
Left out on purpose: settings, drafts, internal ids, code. Card numbers are last four digits only."""
from __future__ import annotations

from engines.clock import manila_today
from engines.common import get_conn
import os
import sqlite3

import pandas as pd

from engines.cc_rules import cc_owed_case

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")
TODAY = "manila_today()"
YES = "CASE WHEN {c} = 1 THEN 'Yes' ELSE 'No' END"

# key -> (title, one-line help, SQL, {column: plain label}, has an 'active' column to filter on)
LISTS: dict[str, dict] = {}


def _add(key: str, title: str, help_text: str, sql: str, labels: dict, active_col: str | None = None) -> None:
    LISTS[key] = {"title": title, "help": help_text, "sql": sql, "labels": labels, "active_col": active_col}


_add("suppliers", "Suppliers", "Everyone we buy from, with what they are owed and when we last dealt with them.",
     f"""SELECT c.creditor_name AS name, c.tin, c.contact_person, c.contact_number, c.email, c.terms, {YES.format(c='c.is_active')} AS active,
         (SELECT COUNT(*) FROM order_statements o WHERE o.creditor_id = c.creditor_id AND o.status != 'Cancelled') AS batches,
         (SELECT COUNT(*) FROM invoices i WHERE i.creditor_id = c.creditor_id AND i.status != 'Cancelled' AND i.is_opening_balance = 0) AS invoices,
         COALESCE((SELECT SUM(k.amount) FROM checks_issued k WHERE k.creditor_id = c.creditor_id AND k.statement_id IS NOT NULL AND k.status = 'Active'
                   AND k.check_date >= {TODAY} AND k.settled_outside_at IS NULL), 0) AS open_balance,
         (SELECT MAX(i.invoice_date) FROM invoices i WHERE i.creditor_id = c.creditor_id AND i.status != 'Cancelled' AND i.is_opening_balance = 0) AS last_invoice,
         (SELECT MAX(k.check_date) FROM checks_issued k WHERE k.creditor_id = c.creditor_id AND k.status = 'Active') AS last_check
         FROM creditors c WHERE c.creditor_type = 'SUPPLIER' ORDER BY c.creditor_name""",
     {"name": "Supplier", "tin": "TIN", "contact_person": "Contact person", "contact_number": "Number", "email": "Email", "terms": "Terms (days)", "active": "Active",
      "batches": "Batches", "invoices": "Invoices", "open_balance": "Still to pay", "last_invoice": "Last invoice", "last_check": "Last check date"}, "active")

_add("lenders", "Private lenders", "Everyone we borrow from, with their loan notes and what is still to pay.",
     f"""SELECT c.creditor_name AS name, c.tin, c.contact_person, c.contact_number, c.email, {YES.format(c='c.is_active')} AS active,
         (SELECT COUNT(*) FROM loan_notes n WHERE n.creditor_id = c.creditor_id AND n.status != 'Cancelled') AS notes,
         COALESCE((SELECT SUM(n.principal_amount) FROM loan_notes n WHERE n.creditor_id = c.creditor_id AND n.status != 'Cancelled'), 0) AS principal,
         COALESCE((SELECT SUM(k.amount) FROM checks_issued k WHERE k.creditor_id = c.creditor_id AND k.loan_note_id IS NOT NULL AND k.status = 'Active'
                   AND k.check_date >= {TODAY} AND k.settled_outside_at IS NULL), 0) AS open_balance,
         (SELECT MAX(k.check_date) FROM checks_issued k WHERE k.creditor_id = c.creditor_id AND k.status = 'Active') AS last_check
         FROM creditors c WHERE c.creditor_type = 'LENDER' AND c.is_lessor = 0 ORDER BY c.creditor_name""",
     {"name": "Lender", "tin": "TIN", "contact_person": "Contact person", "contact_number": "Number", "email": "Email", "active": "Active", "notes": "Loan notes",
      "principal": "Total principal", "open_balance": "Still to pay (checks)", "last_check": "Last check date"}, "active")

_add("lessors", "Lessors (rent)", "Everyone we pay rent to, with their rent checks (paid like loans, no contract).",
     f"""SELECT c.creditor_name AS name, c.tin, c.contact_person, c.contact_number, c.email, {YES.format(c='c.is_active')} AS active,
         COALESCE((SELECT GROUP_CONCAT(n.note_ref, ', ') FROM loan_notes n WHERE n.creditor_id = c.creditor_id AND n.is_lease = 1 AND n.status != 'Cancelled'), '') AS leases,
         COALESCE((SELECT GROUP_CONCAT(DISTINCT n.lease_description) FROM loan_notes n WHERE n.creditor_id = c.creditor_id AND n.is_lease = 1 AND n.lease_description IS NOT NULL), '') AS what_is_rented,
         (SELECT COUNT(*) FROM checks_issued k WHERE k.creditor_id = c.creditor_id AND k.loan_note_id IS NOT NULL AND k.status = 'Active') AS rent_checks,
         COALESCE((SELECT SUM(k.amount) FROM checks_issued k WHERE k.creditor_id = c.creditor_id AND k.loan_note_id IS NOT NULL AND k.status = 'Active'
                   AND k.check_date >= {TODAY} AND k.settled_outside_at IS NULL), 0) AS open_balance,
         (SELECT MIN(k.check_date) FROM checks_issued k WHERE k.creditor_id = c.creditor_id AND k.loan_note_id IS NOT NULL AND k.status = 'Active' AND k.check_date >= {TODAY}) AS next_check,
         (SELECT k.amount FROM checks_issued k WHERE k.creditor_id = c.creditor_id AND k.loan_note_id IS NOT NULL AND k.status = 'Active' ORDER BY k.check_date DESC, k.check_id DESC LIMIT 1) AS last_rent
         FROM creditors c WHERE c.is_lessor = 1 ORDER BY c.creditor_name""",
     {"name": "Lessor", "tin": "TIN", "contact_person": "Contact person", "contact_number": "Number", "email": "Email", "active": "Active", "leases": "Lease folders",
      "what_is_rented": "What is rented", "rent_checks": "Rent checks", "open_balance": "Still to pay", "next_check": "Next check date", "last_rent": "Latest rent check"}, "active")

_add("banks", "Banks", "Banks we have loans or cards with (a bank used for both appears once).",
     f"""SELECT c.creditor_name AS name, MAX(c.contact_person) AS contact_person, MAX(c.contact_number) AS contact_number, MAX(c.email) AS email,
         CASE WHEN MAX(c.is_active) = 1 THEN 'Yes' ELSE 'No' END AS active,
         COALESCE((SELECT COUNT(*) FROM bank_loans b JOIN creditors cb ON cb.creditor_id = b.creditor_id WHERE cb.creditor_name = c.creditor_name AND b.is_cancelled = 0), 0) AS bank_loans,
         COALESCE((SELECT COUNT(*) FROM credit_cards cc JOIN creditors cx ON cx.creditor_id = cc.creditor_id WHERE cx.creditor_name = c.creditor_name AND cc.is_active = 1), 0) AS cards,
         COALESCE((SELECT SUM(i.amount) FROM bank_loan_installments i JOIN bank_loans b ON b.bank_loan_id = i.bank_loan_id JOIN creditors cb ON cb.creditor_id = b.creditor_id
                   WHERE cb.creditor_name = c.creditor_name AND b.is_cancelled = 0 AND NOT (i.cleared_at IS NOT NULL OR i.installment_id IN (SELECT matched_installment_id FROM bank_transactions WHERE matched_installment_id IS NOT NULL))), 0)
         + COALESCE((SELECT SUM({cc_owed_case('cs')}) FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id JOIN creditors cx ON cx.creditor_id = cc.creditor_id
                     WHERE cx.creditor_name = c.creditor_name AND cs.status NOT IN ('Paid', 'Cancelled')), 0) AS owed
         FROM creditors c WHERE c.creditor_type IN ('BANK_LOAN', 'CREDIT_CARD') GROUP BY c.creditor_name ORDER BY c.creditor_name""",
     {"name": "Bank", "contact_person": "Contact person", "contact_number": "Number", "email": "Email", "active": "Active", "bank_loans": "Bank loans", "cards": "Cards", "owed": "Still to pay"}, "active")

_add("batches", "Order statements (batches)", "Every supplier batch: what it holds, what was paid by checks, what is left.",
     """SELECT o.statement_ref AS batch, c.creditor_name AS supplier, o.status, DATE(o.created_at, '+8 hours') AS created,
         (SELECT COUNT(*) FROM invoices i WHERE i.statement_id = o.statement_id AND i.status != 'Cancelled') AS invoices,
         (SELECT COALESCE(SUM(i.amount), 0) FROM invoices i WHERE i.statement_id = o.statement_id AND i.status != 'Cancelled') AS gross,
         (SELECT COALESCE(SUM(m.amount), 0) FROM credit_memos m WHERE m.statement_id = o.statement_id) AS credit_memos,
         COALESCE(v.net_total, 0) AS net,
         (SELECT COALESCE(SUM(k.amount), 0) FROM checks_issued k WHERE k.statement_id = o.statement_id AND k.status = 'Active') AS paid_by_checks,
         COALESCE(v.net_total, 0) - (SELECT COALESCE(SUM(k.amount), 0) FROM checks_issued k WHERE k.statement_id = o.statement_id AND k.status = 'Active') AS balance,
         CASE WHEN o.is_locked = 1 THEN 'Yes' ELSE 'No' END AS locked
         FROM order_statements o JOIN creditors c ON c.creditor_id = o.creditor_id LEFT JOIN v_batch_net_totals v ON v.statement_id = o.statement_id
         ORDER BY o.statement_id DESC""",
     {"batch": "Batch", "supplier": "Supplier", "status": "Status", "created": "Created", "invoices": "Invoices", "gross": "Invoices total", "credit_memos": "Credit memos",
      "net": "Net total", "paid_by_checks": "Paid by checks", "balance": "Balance", "locked": "Locked"})

_add("loan_notes", "Loan notes", "Every private loan note with its terms and what is outstanding.",
     f"""SELECT n.note_ref AS note, c.creditor_name AS lender, n.issue_date, n.principal_amount AS principal, n.monthly_interest_rate AS rate, n.total_interest AS interest,
         n.total_payable, (SELECT COUNT(*) FROM loan_installments i WHERE i.loan_note_id = n.loan_note_id) AS installments,
         n.total_payable - COALESCE((SELECT SUM(k.amount) FROM checks_issued k WHERE k.loan_note_id = n.loan_note_id AND k.status = 'Active' AND k.check_date < {TODAY}), 0) AS outstanding,
         n.status, CASE WHEN n.is_beginning_balance = 1 THEN 'Beginning balance (from MS Access)' ELSE '' END AS source
         FROM loan_notes n JOIN creditors c ON c.creditor_id = n.creditor_id ORDER BY n.loan_note_id DESC""",
     {"note": "Loan note", "lender": "Lender", "issue_date": "Issue date", "principal": "Principal", "rate": "Monthly rate %", "interest": "Interest", "total_payable": "Total payable",
      "installments": "Installments", "outstanding": "Outstanding", "status": "Status", "source": "Source"})

_add("bank_loans", "Bank loans", "Every bank loan with its dates, who it is for and what is still to pay.",
     f"""SELECT c.creditor_name AS bank, b.loan_ref AS reference, b.account_number AS account, b.start_date, b.maturity_date, b.principal_amount AS principal, b.mode_of_payment AS mode,
         lb.name AS for_whom, COALESCE((SELECT SUM(i.amount) FROM bank_loan_installments i WHERE i.bank_loan_id = b.bank_loan_id AND i.due_date >= {TODAY}), 0) AS outstanding,
         CASE WHEN b.is_cancelled = 1 THEN 'Cancelled' ELSE b.status END AS status, CASE WHEN b.is_beginning_balance = 1 THEN 'Beginning balance (from MS Access)' ELSE '' END AS source
         FROM bank_loans b JOIN creditors c ON c.creditor_id = b.creditor_id LEFT JOIN loan_beneficiaries lb ON lb.beneficiary_id = b.beneficiary_id ORDER BY b.bank_loan_id DESC""",
     {"bank": "Bank", "reference": "Loan reference", "account": "Account no.", "start_date": "Start", "maturity_date": "Maturity", "principal": "Principal", "mode": "Mode of payment",
      "for_whom": "For", "outstanding": "Still to pay", "status": "Status", "source": "Source"})

_add("cards", "Credit cards", "Every card (last four digits only) with its limit, statement day and cardholder.",
     f"""SELECT c.creditor_name AS issuer, cc.card_name, cc.card_number_last4 AS last4, ch.name AS cardholder, cc.network, cc.credit_limit, cc.statement_day, cc.annual_fee_due_month AS fee_month,
         {YES.format(c='cc.is_active')} AS active FROM credit_cards cc JOIN creditors c ON c.creditor_id = cc.creditor_id LEFT JOIN cardholders ch ON ch.cardholder_id = cc.cardholder_id
         ORDER BY c.creditor_name, cc.card_name""",
     {"issuer": "Issuer", "card_name": "Card", "last4": "Last 4", "cardholder": "Cardholder", "network": "Network", "credit_limit": "Credit limit", "statement_day": "Statement day",
      "fee_month": "Annual fee month", "active": "Active"}, "active")

_add("cardholders", "Cardholders", "People who hold the company cards.",
     f"""SELECT h.name, h.department, h.contact_number, (SELECT COUNT(*) FROM credit_cards cc WHERE cc.cardholder_id = h.cardholder_id AND cc.is_active = 1) AS cards,
         {YES.format(c='h.is_active')} AS active FROM cardholders h ORDER BY h.name""",
     {"name": "Cardholder", "department": "Department", "contact_number": "Number", "cards": "Active cards", "active": "Active"}, "active")

_add("beneficiaries", "Loan beneficiaries", "Whom the bank loans are for (a person or an affiliate).",
     """SELECT lb.name, lb.beneficiary_type AS type, lb.contact_number, (SELECT COUNT(*) FROM bank_loans b WHERE b.beneficiary_id = lb.beneficiary_id AND b.is_cancelled = 0) AS bank_loans
         FROM loan_beneficiaries lb ORDER BY lb.name""",
     {"name": "Name", "type": "Type", "contact_number": "Number", "bank_loans": "Bank loans"})

_add("booklets", "Check booklets", "Every checkbook: its numbers, how many leaves are used, spoiled, held as guarantee and left.",
     """SELECT b.booklet_code AS code, b.start_number AS first_no, b.end_number AS last_no, b.date_received, b.status,
         (b.end_number - b.start_number + 1) AS leaves,
         (SELECT COUNT(*) FROM checks_issued k WHERE k.bank_account_id = b.bank_account_id AND k.check_number GLOB '[0-9]*' AND CAST(k.check_number AS INTEGER) BETWEEN b.start_number AND b.end_number) AS used,
         (SELECT COUNT(*) FROM spoiled_checks s WHERE s.bank_account_id = b.bank_account_id AND s.check_number GLOB '[0-9]*' AND CAST(s.check_number AS INTEGER) BETWEEN b.start_number AND b.end_number) AS spoiled,
         (SELECT COUNT(*) FROM guarantee_checks g WHERE g.bank_account_id = b.bank_account_id AND CAST(g.check_number AS INTEGER) BETWEEN b.start_number AND b.end_number) AS guarantee
         FROM check_booklets b ORDER BY b.start_number""",
     {"code": "Booklet", "first_no": "First no.", "last_no": "Last no.", "date_received": "Received", "status": "Status", "leaves": "Leaves", "used": "Used", "spoiled": "Spoiled",
      "guarantee": "Guarantee", "left": "Left"})

_add("spoiled", "Spoiled checks", "Leaves that cannot be used (misprints, jams, torn).",
     """SELECT s.check_number, s.reason, DATE(s.logged_at, '+8 hours') AS logged,
         (SELECT b.booklet_code FROM check_booklets b WHERE b.bank_account_id = s.bank_account_id AND s.check_number GLOB '[0-9]*'
            AND CAST(s.check_number AS INTEGER) BETWEEN b.start_number AND b.end_number LIMIT 1) AS booklet
         FROM spoiled_checks s ORDER BY s.check_number""",
     {"check_number": "Check no.", "reason": "Reason", "logged": "Logged", "booklet": "Booklet"})

_add("adjustments", "Adjustments to the bank", "Where the bank differed from what we recorded and how it was settled (the bank is the truth).",
     """SELECT DATE(a.created_at, '+8 hours') AS date, a.kind, k.check_number, cr.creditor_name AS payee, a.recorded_value, a.bank_value, a.amount, a.reason,
         CASE WHEN a.reversed_at IS NOT NULL THEN 'Reversed' ELSE '' END AS reversed
         FROM bank_adjustments a LEFT JOIN checks_issued k ON k.check_id = a.check_id LEFT JOIN creditors cr ON cr.creditor_id = k.creditor_id ORDER BY a.adjustment_id DESC""",
     {"date": "Date", "kind": "What", "check_number": "Check no.", "payee": "Payee", "recorded_value": "We recorded", "bank_value": "Bank shows", "amount": "Difference", "reason": "Reason",
      "reversed": "Reversed"})

_add("corrections", "Corrections to checks", "Every correction made to a check's number, date or amount, with the reason.",
     """SELECT DATE(c.corrected_at, '+8 hours') AS date, k.check_number, cr.creditor_name AS payee, c.correction_type AS what, c.old_value, c.new_value, c.reason
         FROM check_corrections c JOIN checks_issued k ON k.check_id = c.check_id LEFT JOIN creditors cr ON cr.creditor_id = k.creditor_id ORDER BY c.correction_id DESC""",
     {"date": "Date", "check_number": "Check no.", "payee": "Payee", "what": "What changed", "old_value": "Was", "new_value": "Now", "reason": "Reason"})

_add("print_runs", "Chrysanth print runs", "Each file sent to Chrysanth and whether it was marked printed.",
     """SELECT p.print_batch_id AS run, DATE(p.exported_at, '+8 hours') AS exported, p.check_count AS checks, p.total_amount AS total, DATE(p.printed_confirmed_at, '+8 hours') AS printed
         FROM check_print_batches p ORDER BY p.print_batch_id DESC""",
     {"run": "Run", "exported": "Exported", "checks": "Checks", "total": "Total", "printed": "Marked printed"})

_add("bank_imports", "Bank statements imported", "Each bank statement file brought in, its period and closing balance.",
     """SELECT i.file_name AS file, i.period_from, i.period_to, i.line_count AS lines, i.new_lines, i.closing_balance, CASE WHEN i.balance_ok = 1 THEN 'Yes' ELSE 'No' END AS balance_ok,
         DATE(i.imported_at, '+8 hours') AS imported, DATE(i.reconciled_at, '+8 hours') AS reconciled FROM bank_statement_imports i ORDER BY i.import_id DESC""",
     {"file": "File", "period_from": "From", "period_to": "To", "lines": "Lines", "new_lines": "New lines", "closing_balance": "Closing balance", "balance_ok": "Balance checks out",
      "imported": "Imported", "reconciled": "Reconciled"})


def list_keys() -> list[str]:
    return list(LISTS)


def get_list(key: str, active_only: bool = False) -> pd.DataFrame:
    """One list as a DataFrame with its raw column names; `active_only` drops inactive rows on lists that have an Active column."""
    spec = LISTS[key]
    with get_conn(DB_PATH) as conn:
        df = pd.read_sql_query(spec["sql"], conn)
    if key == "booklets" and not df.empty:
        df["left"] = df["leaves"] - df["used"] - df["spoiled"] - df["guarantee"]
    if active_only and spec["active_col"] and not df.empty:
        df = df[df[spec["active_col"]] == "Yes"].reset_index(drop=True)
    return df


def labels(key: str) -> dict:
    return LISTS[key]["labels"]


def all_lists(active_only: bool = False) -> dict[str, pd.DataFrame]:
    """Every list keyed by its title -- the 'Download all lists' workbook has one sheet each."""
    return {LISTS[k]["title"]: get_list(k, active_only) for k in LISTS}


_MONEY = {"last_rent", "open_balance", "principal", "interest", "total_payable", "outstanding", "owed", "gross", "credit_memos", "net", "paid_by_checks", "balance", "credit_limit", "amount",
          "total", "closing_balance"}
_DATES = {"next_check", "last_invoice", "last_check", "created", "issue_date", "start_date", "maturity_date", "date_received", "logged", "date", "exported", "printed", "period_from", "period_to",
          "imported", "reconciled"}


def kinds(key: str) -> dict:
    """{raw column: 'money' | 'date'} for the Excel / CSV formats of one list (keyed by the raw column names)."""
    return {c: ("money" if c in _MONEY else "date") for c in LISTS[key]["labels"] if c in _MONEY or c in _DATES}


def friendly_list(key: str, active_only: bool = False) -> pd.DataFrame:
    """One list with its plain column names (what is shown and exported)."""
    return get_list(key, active_only).rename(columns=LISTS[key]["labels"])


def export_workbook(active_only: bool = False) -> tuple[dict[str, pd.DataFrame], dict]:
    """(sheets, kinds) for 'Download all lists': one sheet per list with plain column names, and the money / date formats keyed by those names."""
    sheets, fmt = {}, {}
    for key, spec in LISTS.items():
        sheets[spec["title"]] = friendly_list(key, active_only)
        fmt.update({spec["labels"][c]: k for c, k in kinds(key).items()})
    return sheets, fmt
