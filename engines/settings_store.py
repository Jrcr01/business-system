"""Small get / set helpers for the `settings` key-value table (2026-09-28). Used for the owner's adjustable numbers -- e.g. the stale-deposit
alert days (default 7, owner: "I plan to upload the SOA weekly"), the cash buffer, the large-debit alert factor."""
from __future__ import annotations

import os
import sqlite3

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


def get_setting(key: str, default=None):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row[0] if row and row[0] not in (None, "") else default


def get_number(key: str, default: float) -> float:
    try:
        return float(get_setting(key, default))
    except (TypeError, ValueError):
        return float(default)


def set_setting(key: str, value, category: str = "general", description: str | None = None) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        if conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone():
            conn.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?", (str(value), key))
        else:
            conn.execute("INSERT INTO settings (key, value, category, description) VALUES (?, ?, ?, ?)", (key, str(value), category, description))
