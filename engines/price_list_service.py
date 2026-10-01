"""Supplier price lists (cash disbursement cycle, stage B1, 2026-10-01).

Each supplier sends its own price list, so an item is just free text per supplier ("Tire 175/70R13"). A price applies from its effective date until a later
row for the same item. A purchase order line takes its ESTIMATE from here (price_on); when the goods and the supplier's invoice arrive, the actual price
prevails and the list can be updated with a new dated row. Names are matched ignoring case and extra spaces, and keep the spelling they were first typed with.

  * add_price / set_price_active          -- typing one price in
  * parse_price_list_file / import_price_rows / template_csv -- a CSV or Excel file from the supplier
  * price_on / current_prices / price_history / item_names   -- looking prices up"""
from __future__ import annotations

import io
import re
from datetime import date, datetime

import pandas as pd

from engines.clock import manila_today
from engines.common import get_conn, write_tx

_HEADER_SYNONYMS = {
    "item_name": ["item", "item name", "items", "description", "particulars", "product", "product name", "name", "article", "size", "item description"],
    "item_code": ["item code", "code", "sku", "part no", "part number", "part #", "product code"],
    "unit": ["unit", "uom", "unit of measure", "measure"],
    "unit_price": ["unit price", "price", "cost", "unit cost", "net price", "amount", "srp", "dealer price", "list price", "net"],
    "effective_date": ["effective date", "effective", "as of", "date", "valid from", "price date"],
}


def _clean_name(name) -> str:
    return " ".join(str(name or "").split())


def _key(name) -> str:
    return _clean_name(name).lower()


def _to_price(value) -> float | None:
    """'1,850.00', '₱1850', 'PHP 1,850' -> 1850.0; anything unreadable or negative -> None."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        v = float(value)
    else:
        text = re.sub(r"(?i)php|₱|p(?=\d)|,|\s", "", str(value or ""))
        try:
            v = float(text)
        except ValueError:
            return None
    return v if v == v and v >= 0 else None          # v == v rules out NaN


def _to_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = pd.to_datetime(text)
    except (ValueError, TypeError):
        return None
    return None if pd.isna(parsed) else parsed.date()


def _audit(conn, action: str, ref, text: str) -> None:
    conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) VALUES (acting_user(), ?, 'supplier_price_list', ?, ?)",
                 (action, ref, text))


def _existing_spelling(conn, creditor_id: int, name: str) -> str | None:
    row = conn.execute("SELECT item_name FROM supplier_price_list WHERE creditor_id = ? AND lower(item_name) = ? ORDER BY price_id LIMIT 1", (creditor_id, _key(name))).fetchone()
    return row[0] if row else None


def _put(conn, creditor_id: int, name: str, unit: str, price: float, on: date, code: str, replace: bool) -> str:
    """Writes one price row. Returns 'added', 'replaced' or 'skipped' (the same item on the same date is already there)."""
    spelling = _existing_spelling(conn, creditor_id, name) or _clean_name(name)
    row = conn.execute("SELECT price_id FROM supplier_price_list WHERE creditor_id = ? AND item_name = ? AND effective_date = ?", (creditor_id, spelling, str(on))).fetchone()
    if row:
        if not replace:
            return "skipped"
        conn.execute("UPDATE supplier_price_list SET unit_price = ?, unit = COALESCE(NULLIF(?, ''), unit), item_code = COALESCE(NULLIF(?, ''), item_code), is_active = 1 "
                     "WHERE price_id = ?", (price, unit, code, row[0]))
        return "replaced"
    conn.execute("INSERT INTO supplier_price_list (creditor_id, item_code, item_name, unit, unit_price, effective_date) VALUES (?, ?, ?, ?, ?, ?)",
                 (creditor_id, code or None, spelling, unit or None, price, str(on)))
    return "added"


# --- typing prices in ---------------------------------------------------------------------------------------------------------------------------

def add_price(creditor_id: int, item_name: str, unit: str, unit_price, effective_date: date, item_code: str = "", replace: bool = False) -> tuple[bool, str]:
    name = _clean_name(item_name)
    if not name:
        return False, "Type the item name."
    price = _to_price(unit_price)
    if price is None:
        return False, "The price must be a number, zero or more."
    on = _to_date(effective_date)
    if on is None:
        return False, "Pick the date the price starts."
    with write_tx() as conn:
        result = _put(conn, creditor_id, name, _clean_name(unit), price, on, _clean_name(item_code), replace)
        if result == "skipped":
            return False, f"{name} already has a price starting {on:%b %d, %Y}. Use Replace to change it, or pick a later date for the new price."
        spelling = _existing_spelling(conn, creditor_id, name) or name
        _audit(conn, "PRICE_ADDED" if result == "added" else "PRICE_REPLACED", creditor_id, f"{spelling}: {price:,.2f} from {on} (supplier {creditor_id})")
    return True, f"{spelling}: {price:,.2f} from {on:%b %d, %Y} saved."


def set_price_active(price_id: int, active: bool) -> tuple[bool, str]:
    """Switches one price row off (a wrong or withdrawn price) or back on. Rows are never deleted: the history stays."""
    with write_tx() as conn:
        row = conn.execute("SELECT item_name, creditor_id FROM supplier_price_list WHERE price_id = ?", (price_id,)).fetchone()
        if not row:
            return False, "Price not found."
        conn.execute("UPDATE supplier_price_list SET is_active = ? WHERE price_id = ?", (1 if active else 0, price_id))
        _audit(conn, "PRICE_REACTIVATED" if active else "PRICE_DEACTIVATED", row[1], f"{row[0]} (price row {price_id})")
    return True, "Price switched on." if active else "Price switched off. It is no longer used for new orders."


# --- looking prices up --------------------------------------------------------------------------------------------------------------------------

def price_on(creditor_id: int, item_name: str, on: date | None = None) -> dict | None:
    """The price in force for this supplier's item on a date (default today): the newest active row starting on or before it."""
    on = _to_date(on) or manila_today()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT price_id, item_name, item_code, unit, unit_price, effective_date FROM supplier_price_list
               WHERE creditor_id = ? AND lower(item_name) = ? AND is_active = 1 AND effective_date <= ?
               ORDER BY effective_date DESC, price_id DESC LIMIT 1""", (creditor_id, _key(item_name), str(on))).fetchone()
    if not row:
        return None
    return {"price_id": row[0], "item_name": row[1], "item_code": row[2] or "", "unit": row[3] or "", "unit_price": float(row[4]), "effective_date": row[5]}


def item_names(creditor_id: int) -> list[str]:
    with get_conn() as conn:
        rows = conn.execute("SELECT item_name FROM supplier_price_list WHERE creditor_id = ? GROUP BY lower(item_name) ORDER BY lower(item_name)", (creditor_id,)).fetchall()
    return [r[0] for r in rows]


def current_prices(creditor_id: int, on: date | None = None) -> pd.DataFrame:
    """One row per item: the price in force on `on`, since when, and the price before it (to see what changed)."""
    on = _to_date(on) or manila_today()
    cols = ["price_id", "item_name", "item_code", "unit", "unit_price", "effective_date", "previous_price"]
    with get_conn() as conn:
        df = pd.read_sql_query(
            """SELECT price_id, item_name, item_code, unit, unit_price, effective_date FROM supplier_price_list
               WHERE creditor_id = ? AND is_active = 1 AND effective_date <= ?
               ORDER BY lower(item_name), effective_date DESC, price_id DESC""", conn, params=(creditor_id, str(on)))
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["_k"] = df["item_name"].str.lower()
    df["previous_price"] = df.groupby("_k")["unit_price"].shift(-1)
    out = df.groupby("_k", sort=False).head(1).drop(columns="_k").reset_index(drop=True)
    out["item_code"] = out["item_code"].fillna("")
    out["unit"] = out["unit"].fillna("")
    return out[cols]


def price_history(creditor_id: int, item_name: str) -> pd.DataFrame:
    with get_conn() as conn:
        return pd.read_sql_query(
            """SELECT price_id, item_name, unit, unit_price, effective_date, is_active FROM supplier_price_list
               WHERE creditor_id = ? AND lower(item_name) = ? ORDER BY effective_date DESC, price_id DESC""", conn, params=(creditor_id, _key(item_name)))


# --- a file from the supplier -------------------------------------------------------------------------------------------------------------------

def template_csv() -> bytes:
    """A starter file with the recognised headings and one obviously fake row."""
    return ("Item,Unit,Price,Effective Date,Item Code\n"
            "SAMPLE TIRE 175/70R13,pc,1000.00,2026-01-01,\n").encode("utf-8")


def _map_columns(columns) -> dict[str, str]:
    normalized = {" ".join(str(c).strip().lower().replace("_", " ").split()): c for c in columns}
    mapping = {}
    for canonical, synonyms in _HEADER_SYNONYMS.items():
        for syn in synonyms:
            if syn in normalized and normalized[syn] not in mapping.values():
                mapping[canonical] = normalized[syn]
                break
    return mapping


def parse_price_list_file(file_bytes: bytes, filename: str) -> tuple[list[dict], list[str]]:
    """Reads a supplier's .csv or .xlsx. Returns (rows, messages): a row for every line with an item and a readable price; a line that cannot be read
    is reported by its row number instead of silently dropped; a completely blank line is skipped. Never raises."""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    try:
        if ext == "csv":
            df = pd.read_csv(io.BytesIO(file_bytes), dtype=str, keep_default_na=False)
        elif ext == "xlsx":
            df = pd.read_excel(io.BytesIO(file_bytes), dtype=str, engine="openpyxl").fillna("")
        else:
            return [], ["Unsupported file type (use .csv or .xlsx)."]
    except Exception as e:                                           # a damaged or odd file is reported, not raised
        return [], [f"Could not read the file: {e}"]
    if df.empty:
        return [], ["The file has no rows."]
    cols = _map_columns(df.columns)
    if "item_name" not in cols or "unit_price" not in cols:
        return [], ["Could not find the item and price columns. Expected headings like 'Item' (or Description / Particulars) and 'Price' (or Unit Price)."]

    rows, messages = [], []
    for i, r in df.iterrows():
        n = i + 2                                                    # +1 for the heading row, +1 because spreadsheets count from 1

        def cell(key: str) -> str:
            return str(r[cols[key]]).strip() if key in cols else ""

        if not any(str(v).strip() for v in r.values):
            continue
        name = _clean_name(cell("item_name"))
        if not name:
            messages.append(f"Row {n}: no item name, skipped.")
            continue
        price = _to_price(cell("unit_price"))
        if price is None:
            messages.append(f"Row {n} ({name}): the price '{cell('unit_price')}' could not be read, skipped.")
            continue
        raw_date = cell("effective_date")
        on = _to_date(raw_date)
        if raw_date and on is None:
            messages.append(f"Row {n} ({name}): the date '{raw_date}' could not be read, so the date you pick below is used.")
        rows.append({"item_code": _clean_name(cell("item_code")), "item_name": name, "unit": _clean_name(cell("unit")), "unit_price": price, "effective_date": on})
    return rows, messages


def import_price_rows(creditor_id: int, rows: list[dict], default_date: date, on_duplicate: str = "skip") -> dict[str, int]:
    """Saves parsed rows in one step. A row without its own date uses default_date. on_duplicate ('skip' or 'replace') decides what happens when the
    same item already has a price for that date. Returns the counts and writes one audit line."""
    counts = {"added": 0, "replaced": 0, "skipped": 0}
    with write_tx() as conn:
        for r in rows:
            on = _to_date(r.get("effective_date")) or _to_date(default_date)
            counts[_put(conn, creditor_id, r["item_name"], r.get("unit") or "", float(r["unit_price"]), on, r.get("item_code") or "", on_duplicate == "replace")] += 1
        if counts["added"] or counts["replaced"]:
            _audit(conn, "PRICE_LIST_IMPORTED", creditor_id, f"supplier {creditor_id}: {counts['added']} added, {counts['replaced']} replaced, {counts['skipped']} skipped")
    return counts
