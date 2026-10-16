"""The engine functions the tests reach as `app.<name>` (2026-10-17: the Streamlit prototype moved to archive/streamlit/; this file only
re-exports the same names from the engines, so the ~70 test files written against it keep testing the rules). Nothing runs on import.
New tests import from the engines directly."""
from __future__ import annotations

from engines.card_rules import add_cardholder, add_cc_statement, add_credit_card, confirm_cc_payment_from_next_statement, deactivate_cardholder, deactivate_credit_card, delete_cc_statement, edit_cardholder, edit_cc_statement, edit_credit_card, get_default_statement_month, get_last_cc_statement_unpaid_balance, get_unconfirmed_cc_statement, reactivate_cardholder, reactivate_credit_card  # noqa: F401
from engines.check_rules import _is_check_number_available, allocate_check_to_invoices, apply_check_correction, assign_check_number, assign_check_number_from_booklet, assign_check_numbers_from_booklet, assign_check_numbers_manual, cancel_check, create_booklet, edit_parked_check, get_available_booklet_numbers, get_existing_check_dates_for_creditor, get_latest_check_for_creditor, issue_check, issue_checks_grid, log_spoiled_check, pick_number_source, plan_sequential_numbers, replace_check, retract_check_number, spoiled_check_problems, unpark_checks  # noqa: F401
from engines.check_run_service import mark_checks_printed  # noqa: F401
from engines.common import DB_PATH, PRIMARY_BANK_ACCOUNT_ID, get_conn, prospective_year, q_live  # noqa: F401
from engines.creditor_rules import add_creditor, creditor_reference_counts, deactivate_creditor, delete_or_deactivate_creditor, get_or_create_card_issuer, reactivate_creditor, update_creditor  # noqa: F401
from engines.loan_note_rules import create_loan_note, update_loan_note_status  # noqa: F401
from engines.statement_rules import add_credit_memo, add_invoice, add_terms_bank_rule, confirm_credit_memos, create_order_statement, delete_credit_memo, delete_invoice, edit_invoice, get_batch_balance, get_batch_memo_status, get_invoices_with_remaining, lock_statement, parse_terms_days, resolve_due_date, sync_invoice_payment_status, unlock_statement, waterfall_allocate_preview  # noqa: F401
