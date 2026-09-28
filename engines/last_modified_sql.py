"""'Last modified' for the browsers (2026-09-27): the most recent moment anything happened to a batch / loan note / bank loan -- the
record itself being created, its invoices, checks, credit memos or repayment rows being added, or any logged change (audit trail) to
them. Derived from those existing timestamps, so it needs no schema change and works for records that already exist.
Each function returns a SQL expression (local time) for the row aliased `alias` in the caller's query."""
from __future__ import annotations


def _latest(*parts: str) -> str:
    return "datetime(MAX(" + ", ".join(parts) + "), '+8 hours')"


def _max_or_blank(sql: str) -> str:
    return f"COALESCE(({sql}), '')"


def statement_modified_sql(alias: str = "os") -> str:
    sid = f"{alias}.statement_id"
    return _latest(
        f"{alias}.created_at",
        _max_or_blank(f"SELECT MAX(created_at) FROM invoices WHERE statement_id = {sid}"),
        _max_or_blank(f"SELECT MAX(created_at) FROM checks_issued WHERE statement_id = {sid}"),
        _max_or_blank(f"SELECT MAX(created_at) FROM credit_memos WHERE statement_id = {sid}"),
        _max_or_blank(
            "SELECT MAX(timestamp) FROM audit_logs WHERE "
            f"(entity_name = 'order_statements' AND entity_id = {sid}) "
            f"OR (entity_name = 'invoices' AND entity_id IN (SELECT invoice_id FROM invoices WHERE statement_id = {sid})) "
            f"OR (entity_name = 'checks_issued' AND entity_id IN (SELECT check_id FROM checks_issued WHERE statement_id = {sid}))"
        ),
    )


def loan_note_modified_sql(alias: str = "ln") -> str:
    lid = f"{alias}.loan_note_id"
    return _latest(
        f"{alias}.created_at",
        _max_or_blank(f"SELECT MAX(created_at) FROM loan_installments WHERE loan_note_id = {lid}"),
        _max_or_blank(f"SELECT MAX(created_at) FROM checks_issued WHERE loan_note_id = {lid}"),
        _max_or_blank(
            "SELECT MAX(timestamp) FROM audit_logs WHERE "
            f"(entity_name = 'loan_notes' AND entity_id = {lid}) "
            f"OR (entity_name = 'checks_issued' AND entity_id IN (SELECT check_id FROM checks_issued WHERE loan_note_id = {lid}))"
        ),
    )


def bank_loan_modified_sql(alias: str = "bl") -> str:
    bid = f"{alias}.bank_loan_id"
    return _latest(
        f"{alias}.created_at",
        _max_or_blank(f"SELECT MAX(created_at) FROM bank_loan_installments WHERE bank_loan_id = {bid}"),
        _max_or_blank(f"SELECT MAX(timestamp) FROM audit_logs WHERE entity_name = 'bank_loans' AND entity_id = {bid}"),
    )
