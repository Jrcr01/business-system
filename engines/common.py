"""Shared helpers for the business rules: the database connection, uncached reads, money/text/date formatting, calendar marks.

Moved out of app.py unchanged (2026-09-27) so the rules can be read and tested without the screens; app.py imports
every name back, so app.<name> keeps working."""
from __future__ import annotations

import os
import pandas as pd
import sqlite3

from datetime import date
from datetime import datetime
from engines.db_engine import df_query


# AP_DB_PATH override lets a separate process point at a different database (e.g. the Phase 8a
# TEST db) without touching the live path every other launch uses by default.
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


PRIMARY_BANK_ACCOUNT_ID = 1


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def q_live(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Uncached read — use for anything that enforces uniqueness (e.g. check number
    sequencing), since the 15s cache on q() can otherwise hand out the same check
    number twice across back-to-back mutations."""
    return df_query(sql, params)


def php(amount) -> str:
    try:
        if pd.isna(amount):
            # float('nan') doesn't raise -- without this, a genuine NaN (pandas' own null for a
            # nullable numeric column read via raw pd.read_sql_query, as opposed to q()'s ''
            # convention) would silently format as the literal string "₱nan" instead of ₱0.00.
            return "₱0.00"
        return f"₱{float(amount):,.2f}"
    except (TypeError, ValueError):
        return "₱0.00"


def has_value(v) -> bool:
    """True if v is a real, non-blank value. q()/q_live() (engines/db_engine.py's df_query)
    coerce every SQL NULL to '' app-wide, which breaks a plain pd.isna()/pd.notna() check on a
    nullable column read through them (an empty string is not NaN) -- found live via a genuinely
    unnumbered/unprinted check that both "already has a number" and "already printed" checks
    misreported as true. Use this instead of pd.isna()/pd.notna() on any column that came from q()
    or q_live() and can legitimately be NULL."""
    return not (pd.isna(v) or v == "")


def cell_text(v) -> str:
    """Safe blank-if-missing text read from a data_editor cell (None/NaN -> '')."""
    return "" if pd.isna(v) else str(v)


def _as_date(v):
    if isinstance(v, str):
        return datetime.strptime(v, "%Y-%m-%d").date()
    if isinstance(v, datetime):
        return v.date()
    return v


def fix_nullable_int(df: pd.DataFrame, col: str) -> pd.DataFrame:
    df = df.copy()
    df[col] = pd.to_numeric(df[col], errors="coerce").astype("Int64")
    return df


def filter_by_date(df: pd.DataFrame, col: str, date_from, date_to) -> pd.DataFrame:
    if df.empty or date_from is None or date_to is None:
        return df
    parsed = pd.to_datetime(df[col], errors="coerce").dt.date
    return df[(parsed >= date_from) & (parsed <= date_to)]


def _pdf_money(v) -> str:
    return f"{float(v):,.2f}"


def prospective_year(month: int, today: date | None = None) -> int:
    """The year a picked month is assumed to belong to, looking forward from today (2026-09-26, per user): from September,
    picking January means January of NEXT year, picking November means this year. A month that is still ahead (or the
    current one) stays in this year; one already behind us rolls to the next. The year picker can still override it."""
    today = today or date.today()
    return today.year if month >= today.month else today.year + 1


def set_blocked_date(d: date, label: str, kind: str = "custom") -> None:
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO calendar_blocked_dates (blocked_date, label, kind) VALUES (?, ?, ?) "
            "ON CONFLICT(blocked_date) DO UPDATE SET label = excluded.label, kind = excluded.kind",
            (str(d), label or None, kind),
        )
        conn.commit()


def remove_blocked_date(d: date) -> None:
    with get_conn() as conn:
        conn.execute("DELETE FROM calendar_blocked_dates WHERE blocked_date = ?", (str(d),))
        conn.commit()
