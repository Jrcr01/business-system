"""Bulk repayment-schedule import from a spreadsheet (CSV or Excel) — for lenders whose
schedule already lives in Excel (the same shape as the two real reference sheets this phase
was designed against: Date / Principal / Interest / Outstanding Balance columns).

Same safety principle as invoice import: parsed rows land in a staging grid for review,
nothing is saved until confirmed via the existing add_loan_installment() path — which already
enforces the hard "can't over-commit principal beyond the loan" rule per row, so an imported
sheet gets the same protection a manually-typed row would.
"""
from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import date

import pandas as pd

_HEADER_SYNONYMS = {
    "due_date": ["due date", "date", "payment date"],
    "principal": ["principal", "principal amount"],
    "interest": ["interest", "interest amount"],
    "outstanding_balance": ["outstanding balance", "balance", "outstanding"],
}


def get_template_csv() -> bytes:
    """A downloadable starter CSV matching the real reference sheets' own column names."""
    return (
        "Date,Principal,Interest,Outstanding Balance\n"
        "2026-01-01,10000.00,2000.00,90000.00\n"
    ).encode("utf-8")


@dataclass
class ImportedInstallmentRow:
    due_date: date
    principal: float
    interest: float
    outstanding_balance: float | None  # sanity-check only, never stored — the system always recomputes its own


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


def parse_loan_schedule_spreadsheet(file_bytes: bytes, filename: str) -> tuple[list[ImportedInstallmentRow], list[str]]:
    """Returns (rows, messages). A row missing Principal or Interest still gets a ₱0 default
    so it's visible/fixable in the staging grid rather than silently dropped — except a fully
    blank row, which is skipped. Never raises."""
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
            "Could not find a Date column. Expected headers like "
            "'Date', 'Principal', 'Interest', 'Outstanding Balance' (any reasonable variant works)."
        ]

    def _clean_amount(s: str) -> str:
        return s.replace(",", "").replace("₱", "").replace("PHP", "").strip()

    rows: list[ImportedInstallmentRow] = []
    messages: list[str] = []
    for i, row in df.iterrows():
        excel_row_num = i + 2

        def cell(canonical: str) -> str:
            col = col_map.get(canonical)
            return str(row[col]).strip() if col else ""

        date_str = cell("due_date")
        principal_str = _clean_amount(cell("principal"))
        interest_str = _clean_amount(cell("interest"))
        balance_str = _clean_amount(cell("outstanding_balance"))

        if not date_str and not principal_str and not interest_str:
            continue  # fully blank row

        row_issues = []
        due_date = date.today()
        if date_str:
            try:
                due_date = pd.to_datetime(date_str).date()
            except (ValueError, TypeError):
                row_issues.append("Date")
        else:
            row_issues.append("Date")

        principal = 0.0
        if principal_str:
            try:
                principal = float(principal_str)
            except ValueError:
                row_issues.append("Principal")

        interest = 0.0
        if interest_str:
            try:
                interest = float(interest_str)
            except ValueError:
                row_issues.append("Interest")

        outstanding_balance = None
        if balance_str:
            try:
                outstanding_balance = float(balance_str)
            except ValueError:
                pass  # sanity-check only — not worth a warning if unparseable

        if row_issues:
            messages.append(f"Row {excel_row_num}: could not read {', '.join(row_issues)} — check/fill in manually.")

        rows.append(ImportedInstallmentRow(due_date=due_date, principal=principal, interest=interest, outstanding_balance=outstanding_balance))

    if not rows:
        messages.append("No usable rows found in the spreadsheet.")
    return rows, messages
