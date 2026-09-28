"""Audit log protection (core step C2, 2026-09-28; owner: "lock + tamper check", corrections only by owner-run masking).

Two layers:
  1. LOCK -- triggers in schema.sql make the database itself refuse any UPDATE or DELETE on audit_logs and audit_seals (the one
     exception: filling user_email on a row that has none, which update_database does once for rows made before F1.5).
  2. TAMPER CHECK -- someone with an outside SQLite tool could drop the triggers and edit the file. So every audit row gets a SEAL:
     an HMAC-SHA256 fingerprint of the row made with a secret key that is NOT in the database (`audit_seal.key` beside the database
     file; on the server: its data folder). A second file beside it, `audit_seal.state`, remembers the highest sealed row and how
     many rows were sealed, so cutting rows off the end is noticed too. seal_and_verify() -- run by the daily backup -- checks every
     sealed row, then seals the new ones:
       * a sealed row that was changed          -> "changed"
       * a sealed row that is gone              -> "deleted"
       * a row without a seal older than the newest sealed one (slipped into the past, or its seal was removed) -> "unsealed"
       * fewer sealed rows / a lower top row than the state file remembers -> "cut off"
       * a protection trigger missing           -> "lock removed"
     When anything is wrong, NEW rows are not sealed (the evidence is kept as it is) until the owner has looked.
  The result is kept in settings ('audit_check') for Data health and the ERP home page. Rows are never "fixed" by this module.
Masking (mask_audit_text.py) is the only way a sealed row may change; it re-seals just that row and logs AUDIT_MASKED."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3

from engines.clock import manila_now

KEY_FILE = "audit_seal.key"
STATE_FILE = "audit_seal.state"
SETTING_KEY = "audit_check"
PROTECT_TRIGGERS = ("audit_logs_no_update", "audit_logs_no_delete", "audit_seals_no_update", "audit_seals_no_delete")
_ROW_SQL = "SELECT log_id, user_action, entity_name, entity_id, old_values, new_values, timestamp, user_email FROM audit_logs"


def _side_file(db_path: str, name: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), name)


def load_key(db_path: str, create: bool = False) -> bytes | None:
    path = _side_file(db_path, KEY_FILE)
    if not os.path.exists(path):
        if not create:
            return None
        with open(path, "w", encoding="utf-8") as f:
            f.write(secrets.token_hex(32))
    with open(path, encoding="utf-8") as f:
        return bytes.fromhex(f.read().strip())


def _load_state(db_path: str) -> dict:
    path = _side_file(db_path, STATE_FILE)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_state(db_path: str, state: dict) -> None:
    with open(_side_file(db_path, STATE_FILE), "w", encoding="utf-8") as f:
        json.dump(state, f)


def fingerprint(key: bytes, row) -> str:
    """HMAC of every column of one audit row (NULL and '' are kept apart)."""
    text = "\x1f".join("\x00" if v is None else str(v) for v in row)
    return hmac.new(key, text.encode("utf-8"), hashlib.sha256).hexdigest()


def _missing_triggers(conn) -> list[str]:
    have = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}
    return [t for t in PROTECT_TRIGGERS if t not in have]


def verify(conn: sqlite3.Connection, key: bytes, state: dict | None = None) -> list[str]:
    """Plain-language problems; empty = the log is exactly as it was sealed. Read-only."""
    problems = [f"Lock removed: protection {t} is missing from the database." for t in _missing_triggers(conn)]
    rows = {r[0]: r for r in conn.execute(_ROW_SQL)}
    seals = dict(conn.execute("SELECT log_id, fingerprint FROM audit_seals"))
    changed = [i for i, fp in seals.items() if i in rows and not hmac.compare_digest(fingerprint(key, rows[i]), fp)]
    deleted = [i for i in seals if i not in rows]
    top = max(seals, default=0)
    unsealed = [i for i in rows if i not in seals and i < top]
    if changed:
        problems.append(f"Changed: {len(changed)} sealed audit entr{'y' if len(changed) == 1 else 'ies'} no longer match "
                        f"(entry {', '.join(map(str, sorted(changed)[:10]))}{' ...' if len(changed) > 10 else ''}).")
    if deleted:
        problems.append(f"Deleted: {len(deleted)} sealed audit entr{'y is' if len(deleted) == 1 else 'ies are'} gone "
                        f"(entry {', '.join(map(str, sorted(deleted)[:10]))}{' ...' if len(deleted) > 10 else ''}).")
    if unsealed:
        problems.append(f"Unsealed: {len(unsealed)} older entr{'y has' if len(unsealed) == 1 else 'ies have'} no seal "
                        f"(entry {', '.join(map(str, sorted(unsealed)[:10]))}{' ...' if len(unsealed) > 10 else ''}).")
    if state:
        if top < state.get("top", 0) or len(seals) < state.get("count", 0):
            problems.append(f"Cut off: the log should have {state.get('count')} sealed entries up to entry {state.get('top')}, "
                            f"but has {len(seals)} up to entry {top}.")
    return problems


def seal_and_verify(db_path: str) -> dict:
    """Check every sealed row, then seal the new ones (only when nothing is wrong). The first run creates the key and seals the whole
    log as it is (the starting point). Returns {'ok', 'problems', 'newly_sealed', 'sealed_total', 'at'} and saves it for Data health."""
    from engines.common import write_tx

    at = manila_now().strftime("%Y-%m-%d %H:%M:%S")
    with write_tx(db_path) as conn:
        first_time = conn.execute("SELECT COUNT(*) FROM audit_seals").fetchone()[0] == 0
        key = load_key(db_path, create=first_time)
        state = _load_state(db_path)
        if key is None:
            problems = [f"Key missing: {KEY_FILE} is not beside the database, so the seals can't be checked. Restore it from where it was kept."]
        else:
            problems = verify(conn, key, state)
        newly = 0
        if not problems:
            top = conn.execute("SELECT COALESCE(MAX(log_id), 0) FROM audit_seals").fetchone()[0]
            new_rows = conn.execute(_ROW_SQL + " WHERE log_id > ? ORDER BY log_id", (top,)).fetchall()
            conn.executemany("INSERT INTO audit_seals (log_id, fingerprint) VALUES (?, ?)", [(r[0], fingerprint(key, r)) for r in new_rows])
            newly = len(new_rows)
        total, top = conn.execute("SELECT COUNT(*), COALESCE(MAX(log_id), 0) FROM audit_seals").fetchone()
        result = {"ok": not problems, "problems": problems, "newly_sealed": newly, "sealed_total": total, "at": at}
        conn.execute("""INSERT INTO settings (key, value, category, description) VALUES (?, ?, 'system', 'Last audit log tamper check')
                        ON CONFLICT (key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP""",
                     (SETTING_KEY, json.dumps(result)))
    if not problems:
        _save_state(db_path, {"top": top, "count": total, "at": at})
    return result


def last_result(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (SETTING_KEY,)).fetchone()
    return json.loads(row[0]) if row and row[0] else None
