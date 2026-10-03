"""Small get / set helpers for the `settings` key-value table (2026-09-28). Used for the owner's adjustable numbers -- e.g. the stale-deposit
alert days (default 7, owner: "I plan to upload the SOA weekly"), the cash buffer, the large-debit alert factor."""
from __future__ import annotations

import os
import sqlite3
from engines.common import get_conn

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")


_CACHE: dict = {}          # db path -> (file stamp, {key: value}); one read of the whole (small) settings table serves every get_setting until the file changes


def _stamp(path: str) -> tuple:
    """Changes whenever anything is saved to the database (the file or its write-ahead log): then the settings are read again."""
    out = []
    for p in (path, path + "-wal"):
        try:
            st_ = os.stat(p)
            out.append((st_.st_mtime_ns, st_.st_size))
        except OSError:
            out.append(None)
    return tuple(out)


def _all_settings() -> dict:
    stamp = _stamp(DB_PATH)
    hit = _CACHE.get(DB_PATH)
    if hit and hit[0] == stamp:
        return hit[1]
    with get_conn(DB_PATH) as conn:
        values = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    _CACHE[DB_PATH] = (stamp, values)
    return values


def get_setting(key: str, default=None):
    """A setting's value. Reads the settings table once and keeps it until the database file changes (a page asks for settings hundreds of times;
    each separate read used to open its own connection)."""
    value = _all_settings().get(key)
    return value if value not in (None, "") else default


def get_number(key: str, default: float) -> float:
    try:
        return float(get_setting(key, default))
    except (TypeError, ValueError):
        return float(default)


def set_setting(key: str, value, category: str = "general", description: str | None = None) -> None:
    _CACHE.pop(DB_PATH, None)
    with get_conn(DB_PATH) as conn:
        if conn.execute("SELECT 1 FROM settings WHERE key = ?", (key,)).fetchone():
            conn.execute("UPDATE settings SET value = ?, updated_at = CURRENT_TIMESTAMP WHERE key = ?", (str(value), key))
        else:
            conn.execute("INSERT INTO settings (key, value, category, description) VALUES (?, ?, ?, ?)", (key, str(value), category, description))
    _CACHE.pop(DB_PATH, None)                         # and again once saved, in case another page read it meanwhile
