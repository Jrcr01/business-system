""""My reports" -- the owner's own reports with saved criteria (2026-09-28, plan phase 4; owner: "sometimes I need to extract a report or
data from the system", "create my own criteria and it can be saved").

A report is a small JSON spec, checked against this file's fixed list of DATASETS and their fields -- never free SQL:
    {"dataset": "checks",
     "date_field": "check_date", "preset": "This month", "n_days": 30, "date_from": "2026-09-01", "date_to": "2026-09-30",
     "filters": [{"field": "payee", "op": "is one of", "value": ["ACME"]}, {"field": "amount", "op": "between", "value": [1000, null]}],
     "columns": ["check_number", "check_date", "payee", "amount"],
     "group_by": [{"field": "payee"}, {"field": "check_date", "part": "month"}],          # up to 2
     "aggregates": [{"fn": "sum", "field": "amount"}, {"fn": "count", "field": "*"}],
     "sort": {"by": "agg:sum:amount", "desc": true}, "top_n": 10, "chart": "bar"}
Relative presets (This month, Last 30 days, Next 30 days ...) are worked out on the day the report runs, so a saved report rolls forward
by itself. run() gives a ReportResult: the table as shown (grouped with subtotals and a grand total, or plain rows), every matching row
for "Export all rows", the criteria in words, a headline figure and the chart data. Saved in `saved_reports` (migration 051): run, edit,
duplicate, delete, pin to the Dashboard, include in the overall report.
"""
from __future__ import annotations

import json
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import pandas as pd

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


@dataclass(frozen=True)
class Field:
    label: str
    kind: str = "text"          # text / choice / money / number / date / datetime


@dataclass(frozen=True)
class Dataset:
    label: str
    description: str
    fields: dict
    default_columns: tuple
    date_fields: tuple
    sql: str | None = None
    loader: object = None       # callable(conn) -> DataFrame, used instead of sql


def _payables(conn) -> pd.DataFrame:
    from engines.overview_report_service import _cash_rows

    df = _cash_rows(conn, date(1900, 1, 1), date(2999, 12, 31))
    df["amount"] = df["paid"] + df["scheduled"]
    df = df[df["amount"].abs() > 0.005].copy()
    df["status"] = ["Paid" if p > 0.005 else "Scheduled" for p in df["paid"]]
    return df[["date", "track", "payee", "status", "amount", "paid", "scheduled"]].sort_values("date")


BANK_KINDS = {"CHECK": "Our check", "DEPOSIT": "Money in", "RETURNED_CHECK": "Bounced deposit", "BILLS_PAYMENT": "Bills payment",
              "DEBIT_MEMO": "Debit memo", "OTHER": "Other"}
BANK_MATCH = {"matched": "Matched", "matched_manual": "Linked by hand", "matched_loan": "Bank-loan installment", "matched_card": "Card payment",
              "cleared_twice": "Cleared twice", "amount_mismatch": "Amount differs", "cancelled_check_cleared": "Cancelled check cleared", "returned_check": "Bounced deposit",
              "not_in_system": "Not in the system", "unmatched_debit": "Bank debit, unmatched"}


def _bank_lines(conn) -> pd.DataFrame:
    try:
        df = pd.read_sql_query("""SELECT posting_date, description, kind, check_number, debit, credit, balance, match_status AS match, resolution, note
                                  FROM bank_transactions ORDER BY posting_date, txn_id""", conn)
    except Exception:                                   # a database without the bank tables yet
        return pd.DataFrame(columns=["posting_date", "description", "kind", "check_number", "debit", "credit", "balance", "match", "resolution", "note"])
    df["kind"] = df["kind"].map(BANK_KINDS).fillna(df["kind"])
    df["match"] = df["match"].map(BANK_MATCH).fillna("")
    return df


DATASETS: dict[str, Dataset] = {
    "checks": Dataset(
        "Checks", "Every check issued, supplier and lender: number, date, payee, amount, status, printed, and when the bank cleared it.",
        {"check_number": Field("Check #"), "check_date": Field("Check date", "date"), "payee": Field("Payee", "choice"),
         "track": Field("Track", "choice"), "reference": Field("Batch / loan note"), "amount": Field("Amount", "money"),
         "status": Field("Status", "choice"), "printed": Field("Printed", "choice"), "bank_cleared_date": Field("Bank cleared", "date"),
         "days_to_clear": Field("Days to clear", "number")},
        ("check_number", "check_date", "payee", "reference", "amount", "status"), ("check_date", "bank_cleared_date"),
        sql="""SELECT c.check_number, c.check_date, cr.creditor_name AS payee,
                      CASE WHEN c.loan_note_id IS NOT NULL THEN 'Private Lender' ELSE 'Supplier' END AS track,
                      COALESCE(os.statement_ref, ln.note_ref) AS reference, c.amount, c.status,
                      CASE WHEN c.printed_at IS NOT NULL THEN 'Yes' ELSE 'No' END AS printed, c.bank_cleared_date,
                      CAST(JULIANDAY(c.bank_cleared_date) - JULIANDAY(c.check_date) AS INTEGER) AS days_to_clear
               FROM checks_issued c JOIN creditors cr ON cr.creditor_id = c.creditor_id
               LEFT JOIN order_statements os ON os.statement_id = c.statement_id LEFT JOIN loan_notes ln ON ln.loan_note_id = c.loan_note_id
               ORDER BY c.check_date, c.check_id"""),
    "invoices": Dataset(
        "Invoices", "Every supplier invoice: invoice number, supplier, received and due dates, gross and net amounts, status, batch.",
        {"doc_ref": Field("Invoice #"), "supplier": Field("Supplier", "choice"), "invoice_date": Field("Received", "date"),
         "due_date": Field("Due date", "date"), "gross_amount": Field("Gross", "money"), "amount": Field("Net", "money"),
         "status": Field("Status", "choice"), "batch": Field("Batch")},
        ("doc_ref", "supplier", "invoice_date", "due_date", "amount", "status"), ("invoice_date", "due_date"),
        sql="""SELECT i.doc_ref, cr.creditor_name AS supplier, i.invoice_date, i.due_date, i.gross_amount, i.amount, i.status, os.statement_ref AS batch
               FROM invoices i JOIN creditors cr ON cr.creditor_id = i.creditor_id LEFT JOIN order_statements os ON os.statement_id = i.statement_id
               ORDER BY i.invoice_date, i.invoice_id"""),
    "payables": Dataset(
        "Payables, all tracks", "Everything paid and scheduled across suppliers, lenders, bank loans and credit cards, past and future "
        "(the same figures as the overall report).",
        {"date": Field("Date", "date"), "track": Field("Track", "choice"), "payee": Field("Payee", "choice"),
         "status": Field("Paid / scheduled", "choice"), "amount": Field("Amount", "money"), "paid": Field("Paid", "money"),
         "scheduled": Field("Scheduled", "money")},
        ("date", "track", "payee", "status", "amount"), ("date",), loader=_payables),
    "credit_memos": Dataset(
        "Credit memos", "Supplier credit memos: memo number, date, supplier, batch, amount and reason.",
        {"memo_ref": Field("Memo #"), "memo_date": Field("Memo date", "date"), "supplier": Field("Supplier", "choice"), "batch": Field("Batch"),
         "amount": Field("Amount", "money"), "reason": Field("Reason")},
        ("memo_ref", "memo_date", "supplier", "batch", "amount", "reason"), ("memo_date",),
        sql="""SELECT m.memo_ref, m.memo_date, cr.creditor_name AS supplier, os.statement_ref AS batch, m.amount, m.reason
               FROM credit_memos m JOIN order_statements os ON os.statement_id = m.statement_id JOIN creditors cr ON cr.creditor_id = os.creditor_id
               ORDER BY m.memo_date, m.credit_memo_id"""),
    "loan_installments": Dataset(
        "Loan-note installments", "Private-lender loan notes, installment by installment: due date, principal, interest, balance, status.",
        {"note_ref": Field("Loan note"), "lender": Field("Lender", "choice"), "installment_no": Field("#", "number"),
         "due_date": Field("Due date", "date"), "principal": Field("Principal", "money"), "interest": Field("Interest", "money"),
         "amount": Field("Installment", "money"), "outstanding_balance": Field("Balance after", "money"), "status": Field("Status", "choice")},
        ("note_ref", "lender", "installment_no", "due_date", "amount", "status"), ("due_date",),
        sql="""SELECT ln.note_ref, cr.creditor_name AS lender, li.installment_no, li.due_date, li.principal, li.interest,
                      li.principal + li.interest AS amount, li.outstanding_balance, li.status
               FROM loan_installments li JOIN loan_notes ln ON ln.loan_note_id = li.loan_note_id JOIN creditors cr ON cr.creditor_id = ln.creditor_id
               ORDER BY li.due_date, li.installment_id"""),
    "bank_loan_installments": Dataset(
        "Bank-loan installments", "Bank-loan dues, installment by installment (paid once the due date has passed, as on the Bank loans page).",
        {"loan_ref": Field("Loan ref"), "bank": Field("Bank", "choice"), "installment_no": Field("#", "number"), "due_date": Field("Due date", "date"),
         "amount": Field("Amount", "money"), "interest_portion": Field("Interest part", "money"), "status": Field("Status", "choice")},
        ("loan_ref", "bank", "installment_no", "due_date", "amount", "status"), ("due_date",),
        sql="""SELECT bl.loan_ref, cr.creditor_name AS bank, bli.installment_no, bli.due_date, bli.amount, bli.interest_portion,
                      CASE WHEN bl.is_cancelled = 1 THEN 'Cancelled' WHEN bli.due_date < DATE('now', 'localtime') THEN 'Paid' ELSE 'Upcoming' END AS status
               FROM bank_loan_installments bli JOIN bank_loans bl ON bl.bank_loan_id = bli.bank_loan_id JOIN creditors cr ON cr.creditor_id = bl.creditor_id
               ORDER BY bli.due_date, bli.installment_id"""),
    "card_statements": Dataset(
        "Credit-card statements", "Card statements: card (last 4 digits only), issuer, cardholder, month, due date, amount due, paid, carried over.",
        {"card": Field("Card"), "issuer": Field("Issuer", "choice"), "cardholder": Field("Cardholder", "choice"), "statement_month": Field("Month"),
         "due_date": Field("Due date", "date"), "total_amount_due": Field("Amount due", "money"), "amount_paid": Field("Paid", "money"),
         "unpaid_balance_next": Field("Carried over", "money"), "status": Field("Status", "choice"), "payment_date": Field("Payment date", "date")},
        ("card", "cardholder", "due_date", "total_amount_due", "amount_paid", "status"), ("due_date", "payment_date"),
        sql="""SELECT cc.card_name || ' - ' || cc.card_number_last4 AS card, cr.creditor_name AS issuer, COALESCE(ch.name, '') AS cardholder,
                      COALESCE(cs.statement_month, SUBSTR(cs.due_date, 1, 7)) AS statement_month, cs.due_date, cs.total_amount_due, cs.amount_paid,
                      cs.unpaid_balance_next, cs.status, cs.payment_date
               FROM cc_statements cs JOIN credit_cards cc ON cc.card_id = cs.card_id JOIN creditors cr ON cr.creditor_id = cc.creditor_id
               LEFT JOIN cardholders ch ON ch.cardholder_id = cc.cardholder_id
               ORDER BY cs.due_date, cs.cc_statement_id"""),
    "bank_lines": Dataset(
        "Bank statement lines", "Every line of the imported Metrobank statements: date, description, type, check number, in, out, balance, "
        "how it was matched or resolved.",
        {"posting_date": Field("Bank date", "date"), "description": Field("Description"), "kind": Field("Type", "choice"),
         "check_number": Field("Check #"), "debit": Field("Out", "money"), "credit": Field("In", "money"), "balance": Field("Balance", "money"),
         "match": Field("Match", "choice"), "resolution": Field("How resolved", "choice"), "note": Field("Note")},
        ("posting_date", "description", "kind", "debit", "credit", "balance"), ("posting_date",), loader=_bank_lines),
    "audit_log": Dataset(
        "Audit log", "Every recorded change in the app: when, what action, which record, before and after.",
        {"timestamp": Field("When", "datetime"), "user_action": Field("Action", "choice"), "entity_name": Field("Record type", "choice"),
         "entity_id": Field("Record id", "number"), "old_values": Field("Before"), "new_values": Field("After")},
        ("timestamp", "user_action", "entity_name", "entity_id", "new_values"), ("timestamp",),
        sql="""SELECT datetime(timestamp, 'localtime') AS timestamp, user_action, entity_name, entity_id, old_values, new_values
               FROM audit_logs ORDER BY log_id"""),
}

PRESETS = ["All dates", "Today", "Yesterday", "This week", "Last week", "This month", "Last month", "This quarter", "Last quarter",
           "This year", "Last year", "Last N days", "Next N days", "Custom range"]
DATE_PARTS = {"day": "Day", "week": "Week starting", "month": "Month", "quarter": "Quarter", "year": "Year"}
AGG_FUNCS = {"sum": "Sum", "avg": "Average", "min": "Lowest", "max": "Highest", "count": "Count"}
OPS = {"text": ["contains", "is one of", "is not one of"], "choice": ["is one of", "is not one of", "contains"], "money": ["between"],
       "number": ["between"], "date": ["between"], "datetime": ["between"]}
CHARTS = [None, "bar", "line"]
MAX_GROUPS = 2


# ------------------------------------------------------------------ dates
def _quarter_start(d: date) -> date:
    return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)


def _month_end(d: date) -> date:
    nxt = date(d.year + (d.month // 12), d.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def preset_range(preset: str, today: date | None = None, n_days: int | None = None, date_from=None, date_to=None) -> tuple:
    """(from, to) for a preset, worked out from `today` -- so a saved "This month" report always shows the current month. (None, None) =
    all dates."""
    t = today or date.today()
    n = max(1, int(n_days or 30))
    if preset in (None, "", "All dates"):
        return None, None
    if preset == "Today":
        return t, t
    if preset == "Yesterday":
        return t - timedelta(days=1), t - timedelta(days=1)
    if preset in ("This week", "Last week"):
        start = t - timedelta(days=t.weekday()) - (timedelta(days=7) if preset == "Last week" else timedelta(0))
        return start, start + timedelta(days=6)
    if preset == "This month":
        return t.replace(day=1), _month_end(t)
    if preset == "Last month":
        last = t.replace(day=1) - timedelta(days=1)
        return last.replace(day=1), last
    if preset in ("This quarter", "Last quarter"):
        qs = _quarter_start(t)
        if preset == "Last quarter":
            qs = _quarter_start(qs - timedelta(days=1))
        return qs, _month_end(date(qs.year, qs.month + 2, 1))
    if preset == "This year":
        return date(t.year, 1, 1), date(t.year, 12, 31)
    if preset == "Last year":
        return date(t.year - 1, 1, 1), date(t.year - 1, 12, 31)
    if preset == "Last N days":
        return t - timedelta(days=n - 1), t
    if preset == "Next N days":
        return t, t + timedelta(days=n)
    if preset == "Custom range":
        f = date.fromisoformat(str(date_from)[:10]) if date_from else None
        e = date.fromisoformat(str(date_to)[:10]) if date_to else None
        return f, e
    raise ValueError(f"Unknown date preset: {preset}")


def _as_dates(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, errors="coerce").dt.date


def _date_key(s: pd.Series, part: str) -> pd.Series:
    """Sortable text key for a date part: day / week (the Monday) 'YYYY-MM-DD', month 'YYYY-MM', quarter 'YYYY Q3', year 'YYYY'."""
    d = pd.to_datetime(s, errors="coerce")
    if part == "day":
        out = d.dt.strftime("%Y-%m-%d")
    elif part == "week":
        out = (d - pd.to_timedelta(d.dt.weekday, unit="D")).dt.strftime("%Y-%m-%d")
    elif part == "month":
        out = d.dt.strftime("%Y-%m")
    elif part == "quarter":
        out = d.dt.year.astype("Int64").astype(str) + " Q" + d.dt.quarter.astype("Int64").astype(str)
    elif part == "year":
        out = d.dt.year.astype("Int64").astype(str)
    else:
        raise ValueError(part)
    return out.where(d.notna(), "(no date)")


def _show_key(v: str, part: str | None) -> str:
    """Friendly text for a date-part key, once the rows are in order: 'Sep 2026', 'Sep 21, 2026'."""
    try:
        if part == "month" and len(str(v)) == 7 and str(v)[4] == "-":
            return datetime.strptime(v + "-01", "%Y-%m-%d").strftime("%b %Y")
        if part in ("day", "week") and len(str(v)) == 10 and str(v)[4] == "-":
            return datetime.strptime(v, "%Y-%m-%d").strftime("%b %d, %Y")
    except ValueError:
        pass
    return v


# ------------------------------------------------------------------ spec checks
def blank_spec(dataset: str = "checks") -> dict:
    ds = DATASETS[dataset]
    return {"dataset": dataset, "date_field": ds.date_fields[0] if ds.date_fields else None, "preset": "All dates", "n_days": 30,
            "date_from": None, "date_to": None, "filters": [], "columns": list(ds.default_columns), "group_by": [], "aggregates": [],
            "sort": {"by": None, "desc": False}, "top_n": 0, "chart": None}


def agg_key(a: dict) -> str:
    return f"agg:{a['fn']}:{a.get('field') or '*'}"


def agg_label(ds: Dataset, a: dict) -> str:
    if a["fn"] == "count":
        return "Count of rows"
    return f"{AGG_FUNCS[a['fn']]} of {ds.fields[a['field']].label}"


def group_label(ds: Dataset, g: dict) -> str:
    f = ds.fields[g["field"]].label
    return f"{DATE_PARTS[g['part']]} ({f})" if g.get("part") else f


def validate(spec: dict) -> list[str]:
    """Everything wrong with a spec, in plain words (empty = fine). Only the listed datasets, fields and operations are allowed."""
    errs = []
    ds = DATASETS.get(spec.get("dataset"))
    if ds is None:
        return [f"Unknown data set: {spec.get('dataset')!r}."]
    fields = ds.fields
    df_ = spec.get("date_field")
    if df_ and df_ not in ds.date_fields:
        errs.append(f"{df_!r} is not a date of {ds.label}.")
    if spec.get("preset", "All dates") not in PRESETS:
        errs.append(f"Unknown date choice {spec.get('preset')!r}.")
    for f in spec.get("filters") or []:
        if f.get("field") not in fields:
            errs.append(f"Unknown filter field {f.get('field')!r}.")
        elif f.get("op") not in OPS[fields[f["field"]].kind]:
            errs.append(f"'{f.get('op')}' does not work on {fields[f['field']].label}.")
    for c in spec.get("columns") or []:
        if c not in fields:
            errs.append(f"Unknown column {c!r}.")
    groups = spec.get("group_by") or []
    if len(groups) > MAX_GROUPS:
        errs.append(f"Group by at most {MAX_GROUPS} fields.")
    for g in groups:
        if g.get("field") not in fields:
            errs.append(f"Unknown group field {g.get('field')!r}.")
        elif g.get("part") and (g["part"] not in DATE_PARTS or fields[g["field"]].kind not in ("date", "datetime")):
            errs.append(f"Cannot group {fields[g['field']].label} by {g['part']}.")
    for a in spec.get("aggregates") or []:
        if a.get("fn") not in AGG_FUNCS:
            errs.append(f"Unknown total {a.get('fn')!r}.")
        elif a["fn"] != "count" and (a.get("field") not in fields or fields[a["field"]].kind not in ("money", "number")):
            errs.append(f"{AGG_FUNCS[a['fn']]} needs an amount or number field.")
    if spec.get("chart") not in CHARTS:
        errs.append(f"Unknown chart {spec.get('chart')!r}.")
    try:
        if int(spec.get("top_n") or 0) < 0:
            errs.append("Top N cannot be negative.")
    except (TypeError, ValueError):
        errs.append("Top N must be a whole number.")
    return errs


# ------------------------------------------------------------------ running
def load(dataset: str) -> pd.DataFrame:
    ds = DATASETS[dataset]
    with sqlite3.connect(DB_PATH) as conn:
        df = ds.loader(conn) if ds.loader else pd.read_sql_query(ds.sql, conn)
    return df.reset_index(drop=True)


def _apply_filter(df: pd.DataFrame, ds: Dataset, f: dict) -> pd.DataFrame:
    col, op, val = f["field"], f["op"], f.get("value")
    kind = ds.fields[col].kind
    if op == "contains":
        return df[df[col].astype(str).str.contains(str(val or ""), case=False, regex=False, na=False)] if str(val or "").strip() else df
    if op in ("is one of", "is not one of"):
        vals = [str(v) for v in (val or [])]
        if not vals:
            return df
        hit = df[col].astype(str).isin(vals)
        return df[hit] if op == "is one of" else df[~hit]
    lo, hi = (list(val) + [None, None])[:2] if isinstance(val, (list, tuple)) else (None, None)
    if kind in ("date", "datetime"):
        d = _as_dates(df[col])
        keep = pd.Series(True, index=df.index)
        if lo:
            keep &= d >= date.fromisoformat(str(lo)[:10])
        if hi:
            keep &= d <= date.fromisoformat(str(hi)[:10])
        return df[keep & d.notna()] if (lo or hi) else df
    num = pd.to_numeric(df[col], errors="coerce")
    keep = pd.Series(True, index=df.index)
    if lo not in (None, ""):
        keep &= num >= float(lo)
    if hi not in (None, ""):
        keep &= num <= float(hi)
    return df[keep]


def _filter_text(ds: Dataset, f: dict) -> str:
    lab, op, val = ds.fields[f["field"]].label, f["op"], f.get("value")
    if op == "contains":
        return f'{lab} contains "{val}"'
    if op in ("is one of", "is not one of"):
        vals = list(val or [])
        shown = ", ".join(map(str, vals[:4])) + (f" and {len(vals) - 4} more" if len(vals) > 4 else "")
        return f"{lab} {op} {shown}"
    lo, hi = (list(val or []) + [None, None])[:2]
    money = ds.fields[f["field"]].kind in ("money", "number")
    fmt = (lambda v: f"{float(v):,.2f}") if money else (lambda v: str(v)[:10])
    if lo not in (None, "") and hi not in (None, ""):
        return f"{lab} from {fmt(lo)} to {fmt(hi)}"
    if lo not in (None, ""):
        return f"{lab} at least {fmt(lo)}"
    if hi not in (None, ""):
        return f"{lab} at most {fmt(hi)}"
    return f"{lab} (any)"


@dataclass
class ReportResult:
    title: str
    dataset_label: str
    table: pd.DataFrame                 # what is shown / exported, friendly column names
    kinds: dict                          # column -> money / number / count / date / datetime / text
    row_types: list                      # per table row: row / group / subtotal / total
    detail_all: pd.DataFrame            # every matching row, every field ("Export all rows")
    detail_kinds: dict
    criteria: list[str]
    date_range: tuple
    grouped: bool
    matching_rows: int
    headline: tuple                      # (label, value, kind)
    chart: dict | None = None            # {"type", "x", "y", "color", "data"}
    notes: list = field(default_factory=list)


def _agg_frame(work: pd.DataFrame, by: list[str], aggs: list[dict]) -> pd.DataFrame:
    named = {}
    for a in aggs:
        k = agg_key(a)
        named[k] = ("__one", "sum") if a["fn"] == "count" else (a["field"], {"sum": "sum", "avg": "mean", "min": "min", "max": "max"}[a["fn"]])
    if by:
        return work.groupby(by, dropna=False, sort=True).agg(**named).reset_index()
    return pd.DataFrame([{k: (work[c].sum() if fn == "sum" else work[c].mean() if fn == "mean" else work[c].min() if fn == "min" else work[c].max())
                          if len(work) else (0 if fn == "sum" else None) for k, (c, fn) in named.items()}])


def run(spec: dict, today: date | None = None, data: pd.DataFrame | None = None, override_range: tuple | None = None,
        title: str | None = None) -> ReportResult:
    """Run a spec. `data` = the dataset already loaded (the screen caches it); `override_range` = (from, to) to use instead of the spec's
    own dates (the overall report runs a saved report for its own period)."""
    errs = validate(spec)
    if errs:
        raise ValueError(" ".join(errs))
    ds = DATASETS[spec["dataset"]]
    df = (load(spec["dataset"]) if data is None else data).copy()
    criteria = [ds.label]
    date_field = spec.get("date_field")
    rng = (None, None)
    if date_field:
        rng = override_range if override_range is not None else preset_range(spec.get("preset", "All dates"), today, spec.get("n_days"),
                                                                              spec.get("date_from"), spec.get("date_to"))
        if rng[0] or rng[1]:
            d = _as_dates(df[date_field])
            keep = d.notna()
            if rng[0]:
                keep &= d >= rng[0]
            if rng[1]:
                keep &= d <= rng[1]
            df = df[keep]
            when = (spec.get("preset") if override_range is None and spec.get("preset") not in ("Custom range",) else "") or ""
            when = when.replace("N days", f"{int(spec.get('n_days') or 30)} days")
            span = f"{rng[0]:%b %d, %Y}" if rng[0] else "the start"
            span += f" to {rng[1]:%b %d, %Y}" if rng[1] else " onward"
            criteria.append(f"{ds.fields[date_field].label}: {when + ' — ' if when else ''}{span}")
        else:
            criteria.append(f"{ds.fields[date_field].label}: all dates")
    for f in spec.get("filters") or []:
        df = _apply_filter(df, ds, f)
        criteria.append(_filter_text(ds, f))
    matching = len(df)
    detail_all = df.rename(columns={k: v.label for k, v in ds.fields.items()})
    detail_kinds = {v.label: v.kind for v in ds.fields.values()}
    top_n = int(spec.get("top_n") or 0)
    sort = spec.get("sort") or {}
    groups = spec.get("group_by") or []
    chart_type = spec.get("chart")

    if not groups:
        cols = [c for c in (spec.get("columns") or ds.default_columns) if c in ds.fields]
        by = sort.get("by") if sort.get("by") in ds.fields else (date_field if date_field in cols else None)
        view = df.sort_values(by, ascending=not sort.get("desc"), kind="stable", na_position="last") if by else df
        if top_n:
            view = view.head(top_n)
            criteria.append(f"Top {top_n} by {ds.fields[by].label if by else 'order on file'}")
        table = view[cols].rename(columns={c: ds.fields[c].label for c in cols}).reset_index(drop=True)
        kinds = {ds.fields[c].label: ds.fields[c].kind for c in cols}
        money = [ds.fields[c].label for c in cols if ds.fields[c].kind == "money"]
        headline = (f"Total {money[0].lower()}", float(pd.to_numeric(table[money[0]], errors="coerce").sum()), "money") if money \
            else ("Rows", len(table), "count")
        return ReportResult(title or ds.label, ds.label, table, kinds, ["row"] * len(table), detail_all, detail_kinds, criteria, rng, False,
                            matching, headline)

    # ---- grouped
    aggs = list(spec.get("aggregates") or [])
    if not aggs:
        aggs = [{"fn": "count", "field": "*"}]
        first_money = next((k for k, v in ds.fields.items() if v.kind == "money"), None)
        if first_money:
            aggs.append({"fn": "sum", "field": first_money})
    work = df.copy()
    work["__one"] = 1
    for a in aggs:
        if a["fn"] != "count":
            work[a["field"]] = pd.to_numeric(work[a["field"]], errors="coerce")
    gcols = []
    for i, g in enumerate(groups):
        col = f"__g{i}"
        work[col] = _date_key(work[g["field"]], g["part"]) if g.get("part") else work[g["field"]].fillna("(blank)").astype(str).replace("", "(blank)")
        gcols.append(col)
    akeys = [agg_key(a) for a in aggs]
    sort_by = sort.get("by") if sort.get("by") in akeys else None
    desc = bool(sort.get("desc"))
    criteria.append("Grouped by " + " then ".join(group_label(ds, g) for g in groups))

    def ordered(frame: pd.DataFrame, key: str) -> pd.DataFrame:
        return frame.sort_values(sort_by, ascending=not desc, kind="stable") if sort_by else frame.sort_values(key, ascending=not desc, kind="stable")

    level1 = ordered(_agg_frame(work, [gcols[0]], aggs), gcols[0])
    if top_n:
        level1 = level1.head(top_n)
        criteria.append(f"Top {top_n} {group_label(ds, groups[0])}" + (f" by {agg_label(ds, aggs[akeys.index(sort_by)])}" if sort_by else ""))
    kept = work[work[gcols[0]].isin(level1[gcols[0]])]
    rows, types = [], []
    if len(groups) == 1:
        for r in level1.to_dict("records"):
            rows.append(r)
            types.append("group")
    else:
        for r1 in level1.to_dict("records"):
            sub = _agg_frame(kept[kept[gcols[0]] == r1[gcols[0]]], [gcols[1]], aggs)
            # dates inside a group run in date order; other second-level groups follow the chosen sort
            sub = sub.sort_values(gcols[1], kind="stable") if groups[1].get("part") else ordered(sub, gcols[1])
            for r2 in sub.to_dict("records"):
                rows.append({gcols[0]: r1[gcols[0]], **r2})
                types.append("group")
            rows.append({**r1, gcols[1]: "Subtotal"})
            types.append("subtotal")
    total = _agg_frame(kept, [], aggs).iloc[0].to_dict()
    rows.append({gcols[0]: "Grand total", **({gcols[1]: ""} if len(groups) > 1 else {}), **total})
    types.append("total")
    out = pd.DataFrame(rows, columns=gcols + akeys)
    for i, g in enumerate(groups):                   # friendly month names once the order is fixed
        out[gcols[i]] = [_show_key(v, g.get("part")) for v in out[gcols[i]]]
    labels = {**{gcols[i]: group_label(ds, g) for i, g in enumerate(groups)}, **{agg_key(a): agg_label(ds, a) for a in aggs}}
    table = out.rename(columns=labels)
    kinds = {}
    for i, g in enumerate(groups):
        kinds[labels[gcols[i]]] = "text"
    for a in aggs:
        kinds[agg_label(ds, a)] = "count" if a["fn"] == "count" else ds.fields[a["field"]].kind
    first = agg_label(ds, aggs[0])
    headline = (first, float(total[akeys[0]] or 0), kinds[first])
    chart = None
    if chart_type:
        cd = table[[t == "group" for t in types]]
        chart = {"type": chart_type, "x": labels[gcols[0]], "y": first, "color": labels[gcols[1]] if len(groups) > 1 else None, "data": cd,
                 "x_order": list(dict.fromkeys(cd[labels[gcols[0]]]))}
    return ReportResult(title or ds.label, ds.label, table, kinds, types, detail_all, detail_kinds, criteria, rng, True, matching, headline, chart)


# ------------------------------------------------------------------ PDF
def _cell(v, kind: str) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ""
    if kind == "money" or kind == "number":
        try:
            return f"{float(v):,.2f}"
        except (TypeError, ValueError):
            return str(v)
    if kind == "count":
        try:
            return f"{int(v):,}"
        except (TypeError, ValueError):
            return str(v)
    if kind == "date":
        d = pd.to_datetime(v, errors="coerce")
        return f"{d:%b %d, %Y}" if pd.notna(d) else str(v)
    if kind == "datetime":
        d = pd.to_datetime(v, errors="coerce")
        return f"{d:%b %d, %Y %I:%M %p}" if pd.notna(d) else str(v)
    return str(v)


def generate_report_pdf(r: ReportResult, max_rows: int = 2000) -> bytes:
    """The report as a landscape PDF: title band, the criteria in words, the chart (if chosen) and the table (subtotal and total rows
    labelled), via account_history_pdf_service.generate_rundown_pdf."""
    from engines.account_history_pdf_service import generate_rundown_pdf

    cols = list(r.table.columns)
    rows = [[_cell(v, r.kinds.get(c, "text")) for c, v in zip(cols, rec)] for rec in r.table.head(max_rows).itertuples(index=False)]
    right = [i for i, c in enumerate(cols) if r.kinds.get(c) in ("money", "number", "count")]
    extra = []
    if r.chart is not None and not r.chart["data"].empty:
        from engines.overview_pdf_service import column_chart

        cd = r.chart["data"]
        x, y, color = r.chart["x"], r.chart["y"], r.chart["color"]
        cats = r.chart["x_order"]
        if color:
            series = {str(s): [float(cd[(cd[x] == c) & (cd[color] == s)][y].sum()) for c in cats] for s in list(dict.fromkeys(cd[color]))[:6]}
        else:
            series = {y: [float(cd[cd[x] == c][y].sum()) for c in cats]}
        extra.append(column_chart(f"{y} by {x}", [str(c)[:14] for c in cats][:24], {k: v[:24] for k, v in series.items()}, height=180, labels=len(cats) <= 8))
    note = " · ".join(r.criteria[1:]) + (f" · {r.matching_rows:,} matching row(s)" if r.matching_rows else "")
    if len(r.table) > max_rows:
        note += f" · first {max_rows:,} rows shown (export to Excel for all)"
    return generate_rundown_pdf(r.title, r.dataset_label, cols, rows, [], right, [], note, before_table=extra)


# ------------------------------------------------------------------ saved reports
def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def list_saved() -> list[dict]:
    with _conn() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM saved_reports ORDER BY name COLLATE NOCASE").fetchall()]
    for r in rows:
        r["spec"] = json.loads(r["spec_json"])
    return rows


def get_saved(report_id: int) -> dict | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM saved_reports WHERE report_id = ?", (int(report_id),)).fetchone()
    if not r:
        return None
    d = dict(r)
    d["spec"] = json.loads(d["spec_json"])
    return d


def save_report(name: str, spec: dict, report_id: int | None = None) -> tuple[bool, str, int | None]:
    """Save a new report, or update `report_id`. The name must be unique; the spec must pass validate()."""
    name = (name or "").strip()
    if not name:
        return False, "Give the report a name.", None
    errs = validate(spec)
    if errs:
        return False, " ".join(errs), None
    js = json.dumps(spec, default=str)
    with _conn() as conn:
        clash = conn.execute("SELECT report_id FROM saved_reports WHERE name = ? COLLATE NOCASE AND report_id != ?", (name, int(report_id or 0))).fetchone()
        if clash:
            return False, f"A report named “{name}” already exists — pick another name.", None
        if report_id:
            conn.execute("UPDATE saved_reports SET name = ?, spec_json = ?, updated_at = CURRENT_TIMESTAMP WHERE report_id = ?", (name, js, int(report_id)))
            rid = int(report_id)
        else:
            rid = conn.execute("INSERT INTO saved_reports (name, spec_json) VALUES (?, ?)", (name, js)).lastrowid
        conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, new_values) VALUES ('REPORT_SAVED', 'saved_reports', ?, ?)", (rid, name))
    return True, f"Saved “{name}”.", rid


def duplicate_report(report_id: int) -> tuple[bool, str, int | None]:
    r = get_saved(report_id)
    if not r:
        return False, "Report not found.", None
    names = {x["name"].lower() for x in list_saved()}
    base, n, name = f"Copy of {r['name']}", 2, f"Copy of {r['name']}"
    while name.lower() in names:
        name, n = f"{base} ({n})", n + 1
    return save_report(name, r["spec"])


def delete_report(report_id: int) -> None:
    with _conn() as conn:
        r = conn.execute("SELECT name FROM saved_reports WHERE report_id = ?", (int(report_id),)).fetchone()
        conn.execute("DELETE FROM saved_reports WHERE report_id = ?", (int(report_id),))
        if r:
            conn.execute("INSERT INTO audit_logs (user_action, entity_name, entity_id, old_values) VALUES ('REPORT_DELETED', 'saved_reports', ?, ?)",
                         (int(report_id), r["name"]))


def set_flags(report_id: int, pin_dashboard: bool | None = None, include_in_overview: bool | None = None) -> None:
    with _conn() as conn:
        if pin_dashboard is not None:
            conn.execute("UPDATE saved_reports SET pin_dashboard = ? WHERE report_id = ?", (int(bool(pin_dashboard)), int(report_id)))
        if include_in_overview is not None:
            conn.execute("UPDATE saved_reports SET include_in_overview = ? WHERE report_id = ?", (int(bool(include_in_overview)), int(report_id)))


def mark_run(report_id: int) -> None:
    with _conn() as conn:
        conn.execute("UPDATE saved_reports SET last_run_at = CURRENT_TIMESTAMP WHERE report_id = ?", (int(report_id),))


def run_saved(report_id: int, today: date | None = None, override_range: tuple | None = None) -> ReportResult:
    r = get_saved(report_id)
    if r is None:
        raise ValueError("Report not found.")
    return run(r["spec"], today=today, override_range=override_range, title=r["name"])


def _flagged(flag: str) -> list[dict]:
    try:
        return [r for r in list_saved() if r[flag]]
    except sqlite3.OperationalError:                  # a database without saved_reports yet
        return []


def pinned_results(today: date | None = None) -> list[tuple[dict, ReportResult | str]]:
    """The Dashboard's pinned reports: (saved row, result) -- or (saved row, error text) when a report no longer runs."""
    out = []
    for r in _flagged("pin_dashboard"):
        try:
            out.append((r, run(r["spec"], today=today, title=r["name"])))
        except Exception as e:                        # a broken saved report must not break the Dashboard
            out.append((r, str(e)))
    return out


def overview_results(start: date, end: date) -> list[tuple[dict, ReportResult | str]]:
    """Reports included in the overall report, run for the report's period (a report with a date uses [start, end]; one without runs as
    saved)."""
    out = []
    for r in _flagged("include_in_overview"):
        try:
            spec = r["spec"]
            out.append((r, run(spec, override_range=(start, end) if spec.get("date_field") else None, title=r["name"])))
        except Exception as e:
            out.append((r, str(e)))
    return out
