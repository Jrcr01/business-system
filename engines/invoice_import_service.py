"""Bulk invoice import from a spreadsheet (CSV or Excel).

For anyone who already keeps invoices in a spreadsheet, this is far more
reliable than OCR — it's parsing real structured data, not guessing from a
photo. Same safety principle as every other invoice-entry path in this app:
parsed rows land in a staging grid for review, nothing is saved until
confirmed via the existing add_invoice() path.
"""
from __future__ import annotations

import difflib
import io
from dataclasses import dataclass
from datetime import date

import pandas as pd
from engines.clock import manila_today

# Column-header synonyms, matched case/whitespace-insensitively. First match
# in each list wins if a spreadsheet somehow has more than one candidate
# column (unusual, but resolved deterministically rather than picking either
# at random).
_HEADER_SYNONYMS = {
    "doc_ref": ["doc ref", "docref", "doc_ref", "invoice no", "invoice number", "invoice #",
                "reference", "reference no", "ref", "dr no", "si no", "or no", "ci no"],
    "invoice_date": ["invoice date", "date received", "date", "doc date"],
    "amount": ["amount", "gross amount", "gross", "total", "total amount", "net amount"],
    "less_pct": ["less %", "less pct", "less", "discount %", "discount", "discount pct"],
}


def get_template_csv() -> bytes:
    """A downloadable starter CSV with the recognized headers and one example
    row — the example uses obviously-fake values so it's not mistaken for a
    real invoice if uploaded as-is."""
    return (
        "Doc Ref,Date,Amount,Less %\n"
        "SAMPLE-0001,2026-01-01,1000.00,0\n"
    ).encode("utf-8")


@dataclass
class ImportedInvoiceRow:
    doc_ref: str
    invoice_date: date
    amount: float
    less_pct: float


def _normalize_header(h: str) -> str:
    return " ".join(str(h).strip().lower().replace("_", " ").split())


def _map_columns(columns) -> dict[str, str]:
    """Returns {canonical_name: actual_column_name} for whichever canonical
    fields were found. A field simply won't be in the result if no header
    matched — caller treats that as 'missing for every row', same as an OCR
    field that couldn't be read."""
    normalized = {_normalize_header(c): c for c in columns}
    mapping = {}
    for canonical, synonyms in _HEADER_SYNONYMS.items():
        for syn in synonyms:
            if syn in normalized:
                mapping[canonical] = normalized[syn]
                break
    return mapping


def parse_invoice_spreadsheet(file_bytes: bytes, filename: str) -> tuple[list[ImportedInvoiceRow], list[str]]:
    """Returns (rows, messages). rows is best-effort per spreadsheet row (a
    missing Doc Ref or unparseable Amount still produces a row with a
    default/blank so it's visible and fixable in the staging grid, not
    silently dropped) — except a row that's entirely blank, which is skipped.
    messages carries file-level problems (bad file, no recognizable columns)
    and per-row warnings (row number + what couldn't be read). Never raises."""
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

    return _rows_from_df(df)


def _rows_from_df(df: pd.DataFrame) -> tuple[list[ImportedInvoiceRow], list[str]]:
    if df.empty:
        return [], ["Spreadsheet has no rows."]

    col_map = _map_columns(df.columns)
    if "doc_ref" not in col_map and "amount" not in col_map:
        return [], [
            "Could not find a Doc Ref or Amount column. Expected headers like "
            "'Doc Ref'/'Invoice No', 'Date', 'Amount', 'Less %' (any reasonable variant works)."
        ]

    rows: list[ImportedInvoiceRow] = []
    messages: list[str] = []
    for i, row in df.iterrows():
        excel_row_num = i + 2  # +1 for header, +1 for 0-index -> 1-index

        def cell(canonical: str) -> str:
            col = col_map.get(canonical)
            return str(row[col]).strip() if col else ""

        doc_ref = cell("doc_ref")
        date_str = cell("invoice_date")
        amount_str = cell("amount").replace(",", "").replace("₱", "").replace("PHP", "").strip()
        less_pct_str = cell("less_pct").replace("%", "").strip()

        if not doc_ref and not date_str and not amount_str:
            continue  # fully blank row (common trailing blank rows in exported sheets)

        row_issues = []
        if not doc_ref:
            row_issues.append("Doc Ref")

        invoice_date = manila_today()
        if date_str:
            try:
                invoice_date = pd.to_datetime(date_str).date()
            except (ValueError, TypeError):
                row_issues.append("Date")
        else:
            row_issues.append("Date")

        amount = 0.0
        if amount_str:
            try:
                amount = float(amount_str)
            except ValueError:
                row_issues.append("Amount")
        else:
            row_issues.append("Amount")

        less_pct = 0.0
        if less_pct_str:
            try:
                less_pct = float(less_pct_str)
            except ValueError:
                pass  # optional field — silently default rather than flag a warning

        if row_issues:
            messages.append(f"Row {excel_row_num}: could not read {', '.join(row_issues)} — check/fill in manually.")

        rows.append(ImportedInvoiceRow(doc_ref=doc_ref, invoice_date=invoice_date, amount=amount, less_pct=less_pct))

    if not rows:
        messages.append("No usable rows found in the spreadsheet.")
    return rows, messages


@dataclass
class ImportedSheet:
    sheet_name: str
    rows: list
    messages: list


def parse_invoice_workbook(file_bytes: bytes) -> tuple[list[ImportedSheet], list[str]]:
    """Reads EVERY sheet of an .xlsx workbook with the same column rules as a single-sheet import.
    One sheet = one new batch (tabs starting with "_" are helper tabs and are ignored). Sheets with nothing usable are still returned (with a message) so
    they show up in the review instead of silently vanishing. Never raises."""
    try:
        sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, dtype=str, engine="openpyxl")
    except Exception as e:
        return [], [f"Could not read the workbook: {e}"]
    out = []
    for name, df in sheets.items():
        if str(name).startswith("_"):
            continue  # helper tabs (instructions, supplier list) are named with a leading underscore
        rows, messages = _rows_from_df(df.fillna(""))
        out.append(ImportedSheet(sheet_name=str(name), rows=rows, messages=messages))
    if not out:
        return [], ["The workbook has no sheets."]
    return out, []


def _norm_name(text: str) -> str:
    return " ".join("".join(ch if ch.isalnum() else " " for ch in str(text).lower()).split())


def match_supplier(sheet_name: str, suppliers: dict) -> int | None:
    """Best guess at which supplier a sheet belongs to, from the sheet's tab name. suppliers is
    {creditor_id: name}. Exact name first, then a name contained in the tab (or the tab contained in
    a name, so "Acom - Sept" and "Acom" both find Acom Trading), then a close spelling. None if
    nothing is convincing -- the review screen then makes the user pick."""
    target = _norm_name(sheet_name)
    if not target:
        return None
    names = {cid: _norm_name(n) for cid, n in suppliers.items()}
    for cid, n in names.items():
        if n == target:
            return cid
    contained = [(len(n), cid) for cid, n in names.items() if n and (n in target or (len(target) >= 4 and target in n))]
    if contained:
        return max(contained)[1]
    # Leading words: "Acom - Sept" starts with the same word as "Acom Trading". Only trusted when the
    # match is unique, otherwise the user picks.
    def lead(a: str, b: str) -> int:
        n = 0
        for x, y in zip(a.split(), b.split()):
            if x != y:
                break
            n += 1
        return n
    scored = [(lead(target, n), cid) for cid, n in names.items()]
    scored = [(k, cid) for k, cid in scored if k >= 1 and len(target.split()[0]) >= 4]
    if scored:
        top = max(k for k, _ in scored)
        winners = [cid for k, cid in scored if k == top]
        if len(winners) == 1:
            return winners[0]
    best = max(((difflib.SequenceMatcher(None, target, n).ratio(), cid) for cid, n in names.items()), default=(0, None))
    return best[1] if best[0] >= 0.8 else None
