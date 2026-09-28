"""Bulk bank-loan schedule import from a spreadsheet (CSV or Excel) — for typing dozens of Due
Date/Amount rows off a real bank amortization document faster than one at a time.

Same safety principle as invoice/loan-schedule import: parsed rows land in a staging grid for
review, nothing is saved until confirmed via the existing add_bank_loan_installment() path.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

import pandas as pd
from engines.clock import manila_today

_HEADER_SYNONYMS = {
    "due_date": ["due date", "date", "payment date"],
    "amount": ["amount", "total amount due per month", "total amount due", "total due", "total payment", "amount due"],
}


def get_template_csv() -> bytes:
    """A downloadable starter CSV — just Due Date and Amount."""
    return (
        "Due Date,Amount\n"
        "22-JUN-26,71407.21\n"
    ).encode("utf-8")


@dataclass
class ImportedBankLoanRow:
    due_date: date
    amount: float


def _normalize_header(h: str) -> str:
    return " ".join(str(h).strip().lower().replace("_", " ").split())


def _map_columns(columns) -> dict[str, str]:
    normalized = {_normalize_header(c): c for c in columns}
    mapping = {}
    for canonical, synonyms in _HEADER_SYNONYMS.items():
        for syn in synonyms:
            if syn in normalized:
                mapping[canonical] = normalized[syn]
                break
    return mapping


def _clean_amount(s: str) -> str:
    return s.replace(",", "").replace("₱", "").replace("PHP", "").replace("%", "").strip()


def parse_bank_loan_spreadsheet(file_bytes: bytes, filename: str) -> tuple[list[ImportedBankLoanRow], list[str]]:
    """Returns (rows, messages). A row missing Amount still gets a ₱0 default so it's visible/
    fixable in the staging grid rather than silently dropped — except a fully blank row, which
    is skipped. Never raises."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if ext == "csv":
            df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)
        elif ext == "xlsx":
            df = pd.read_excel(io.BytesIO(file_bytes), dtype=str, engine="openpyxl").fillna("")
        else:
            return [], ["Unsupported file type (use .csv or .xlsx)."]
    except Exception as e:
        return [], [f"Could not read spreadsheet: {e}"]

    if df.empty:
        return [], ["Spreadsheet has no rows."]

    col_map = _map_columns(df.columns)
    if "due_date" not in col_map:
        return [], [
            "Could not find a Due Date column. Expected headers like "
            "'Due Date' and 'Amount' (any reasonable variant works)."
        ]

    rows: list[ImportedBankLoanRow] = []
    messages: list[str] = []
    for i, row in df.iterrows():
        excel_row_num = i + 2

        def cell(canonical: str) -> str:
            col = col_map.get(canonical)
            return str(row[col]).strip() if col else ""

        date_str = cell("due_date")
        amount_str = _clean_amount(cell("amount"))

        if not date_str and not amount_str:
            continue  # fully blank row

        row_issues = []
        due_date = manila_today()
        if date_str:
            try:
                due_date = pd.to_datetime(date_str).date()
            except (ValueError, TypeError):
                row_issues.append("Due Date")
        else:
            row_issues.append("Due Date")

        amount = 0.0
        if amount_str:
            try:
                amount = float(amount_str)
            except ValueError:
                row_issues.append("Amount")

        if row_issues:
            messages.append(f"Row {excel_row_num}: could not read {', '.join(row_issues)} — check/fill in manually.")

        rows.append(ImportedBankLoanRow(due_date=due_date, amount=amount))

    if not rows:
        messages.append("No usable rows found in the spreadsheet.")
    return rows, messages
