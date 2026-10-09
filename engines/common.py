"""Shared helpers for the business rules: the database connection, uncached reads, money/text/date formatting, calendar marks.

Moved out of app.py unchanged (2026-09-27) so the rules can be read and tested without the screens; app.py imports
every name back, so app.<name> keeps working."""
from __future__ import annotations

import os
import pandas as pd
import sqlite3

from datetime import date
from datetime import datetime
from engines.db_engine import df_query, next_doc_number, open_db
from engines.db_engine import write_tx as _write_tx
from engines.clock import manila_today


# AP_DB_PATH override lets a separate process point at a different database (e.g. the Phase 8a
# TEST db) without touching the live path every other launch uses by default.
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


PRIMARY_BANK_ACCOUNT_ID = 1


def get_conn(db_path: str | None = None) -> sqlite3.Connection:
    """A connection through the one door (engines/db_engine.open_db: foreign keys, busy timeout, WAL). `db_path` defaults to this
    module's DB_PATH; other modules pass their own DB_PATH."""
    return open_db(db_path or DB_PATH)


def write_tx(db_path: str | None = None):
    """One locked step (engines/db_engine.write_tx) on this module's database -- use for every check-then-save."""
    return _write_tx(db_path or DB_PATH)


def q_live(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Uncached read — use for anything that enforces uniqueness (e.g. check number
    sequencing), since the 15s cache on q() can otherwise hand out the same check
    number twice across back-to-back mutations."""
    return df_query(sql, params)


def us_date(value, empty: str = "") -> str:
    """A date as people read it in this system: mm/dd/yyyy (owner, 2026-10-09), with HH:MM when it has a time. Takes a date, a datetime or ISO text;
    anything else is returned as it is."""
    from datetime import date as _date, datetime as _dt
    if value in (None, "") or str(value).lower() in ("nan", "nat", "none"):
        return empty
    if isinstance(value, _dt):
        return value.strftime("%m/%d/%Y %H:%M")
    if isinstance(value, _date):
        return value.strftime("%m/%d/%Y")
    text = str(value).strip()
    try:
        d = _date.fromisoformat(text[:10])
    except ValueError:
        return text
    rest = text[10:].strip().lstrip("T")
    return d.strftime("%m/%d/%Y") + (" " + rest[:5] if len(rest) >= 5 and rest[2] == ":" else "")


def us_dates_in(text) -> str:
    """Dates written inside free text (notes, resolutions, stored messages) as mm/dd/yyyy: '2026-10-09' and 'Oct 09, 2026' -> '10/09/2026'."""
    import re
    if text in (None, ""):
        return ""
    months = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), start=1)}
    s = re.sub(r"\b(\d{4})-(\d{2})-(\d{2})\b", lambda m: f"{m.group(2)}/{m.group(3)}/{m.group(1)}", str(text))
    return re.sub(r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? (\d{1,2}), (\d{4})\b",
                  lambda m: f"{months[m.group(1).lower()]:02d}/{int(m.group(2)):02d}/{m.group(3)}", s)


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
    today = today or manila_today()
    return today.year if month >= today.month else today.year + 1


def add_months(d: date, months: int = 1) -> date:
    """The same day `months` later; a day that does not exist there (Jan 31 + 1) becomes the month's last day."""
    import calendar
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


def booklet_defaults(bank_account_id: int = 1) -> dict:
    """What a new booklet usually is: the next code, the number after the last booklet's end, and the same size as that booklet. Empty/1000-1050 when none is registered."""
    with get_conn() as conn:
        row = conn.execute("SELECT booklet_code, start_number, end_number FROM check_booklets WHERE bank_account_id = ? ORDER BY end_number DESC LIMIT 1", (bank_account_id,)).fetchone()
    if not row:
        return {"code": "Booklet A", "start": 1000, "end": 1050}
    size = int(row[2]) - int(row[1])
    code = str(row[0] or "")
    tail = code.rstrip()[-1:] if code else ""
    nxt = (code[:-1] + chr(ord(tail) + 1)) if tail.isalpha() and tail.upper() != "Z" else (code + " 2" if code else "")
    if code and code.split()[-1].isdigit():
        nxt = " ".join(code.split()[:-1] + [str(int(code.split()[-1]) + 1)])
    return {"code": nxt or "Booklet", "start": int(row[2]) + 1, "end": int(row[2]) + 1 + size}


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
