"""Hide private data inside ONE audit log entry (core step C2, 2026-09-28; owner: "owner-run masking only").

The audit log can't be changed (the database refuses it). The one exception is hiding private data that should never have been written
there -- e.g. a full card number -- by replacing it with a mask such as ****1234. This script is the only way to do that:
  * preview by default; --apply to do it (the owner runs it; nobody else, nothing automatic);
  * the mask may only be asterisks plus at most 4 characters kept from the hidden text (e.g. the last 4 digits);
  * a backup of the database is made first; only that one entry changes; its seal is renewed so the tamper check stays clean;
  * an AUDIT_MASKED entry records which entry, what kind of data and why (never the hidden text itself).

    python mask_audit_text.py --log-id 123 --find 4111111111111111 --mask ************1111 --reason "full card number"
    python mask_audit_text.py ... --apply
    --db <file>   another database (default: the live database from live_db_path.txt)"""
import argparse
import os
import sqlite3
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))   # the project root (this tool moved to tools/ on 2026-10-17)
sys.path.insert(0, HERE)
from db_location import live_db_path, safe_copy  # noqa: E402
from engines.audit_seal import PROTECT_TRIGGERS, fingerprint, load_key  # noqa: E402
from engines.clock import manila_now  # noqa: E402
from engines.common import write_tx  # noqa: E402

MAX_VISIBLE = 4
_ROW_SQL = "SELECT log_id, user_action, entity_name, entity_id, old_values, new_values, timestamp, user_email FROM audit_logs WHERE log_id = ?"


def mask_is_allowed(find: str, mask: str) -> str | None:
    """None when the mask is allowed, else why not."""
    kept = mask.replace("*", "")
    if "*" not in mask:
        return "The mask must hide something: use asterisks (*)."
    if len(kept) > MAX_VISIBLE:
        return f"The mask may keep at most {MAX_VISIBLE} characters of the hidden text."
    if kept and kept not in find:
        return "The characters kept in the mask must come from the hidden text (e.g. its last 4 digits)."
    return None


def _trigger_sql() -> dict[str, str]:
    ref = sqlite3.connect(":memory:")
    with open(os.path.join(HERE, "schema.sql"), encoding="utf-8") as f:
        ref.executescript(f.read())
    sql = {r[0]: r[1] for r in ref.execute("SELECT name, sql FROM sqlite_master WHERE type = 'trigger'") if r[0] in PROTECT_TRIGGERS}
    ref.close()
    return sql


def mask(db_path: str, log_id: int, find: str, mask_text: str, reason: str, apply: bool) -> tuple[bool, str]:
    why_not = mask_is_allowed(find, mask_text)
    if why_not:
        return False, why_not
    if not reason.strip():
        return False, "Say why (--reason), e.g. \"full card number\"."
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        row = conn.execute(_ROW_SQL, (log_id,)).fetchone()
        sealed = conn.execute("SELECT 1 FROM audit_seals WHERE log_id = ?", (log_id,)).fetchone() is not None
    finally:
        conn.close()
    if not row:
        return False, f"There is no audit entry {log_id}."
    old_values, new_values = row[4] or "", row[5] or ""
    hits = old_values.count(find) + new_values.count(find)
    if not hits:
        return False, f"Entry {log_id} doesn't contain that text."
    key = load_key(db_path) if sealed else None
    if sealed and key is None:
        return False, "This entry is sealed but the seal key file is missing, so its seal can't be renewed. Nothing was changed."
    preview = (f"Entry {log_id} ({row[1]}, {row[6]}): {hits} place(s) would be hidden as {mask_text}; "
               f"reason: {reason}.{' Its seal would be renewed.' if sealed else ''}")
    if not apply:
        return True, "PREVIEW -- nothing changed. " + preview + " Run again with --apply to do it."

    backup = f"{db_path}.bak_before_audit_mask_{manila_now():%Y%m%d_%H%M%S}"
    safe_copy(db_path, backup)
    triggers = _trigger_sql()
    with write_tx(db_path) as conn:        # all or nothing: the lock is lifted and put back inside this one step
        conn.execute("DROP TRIGGER IF EXISTS audit_logs_no_update")
        conn.execute("DROP TRIGGER IF EXISTS audit_seals_no_update")
        conn.execute("UPDATE audit_logs SET old_values = ?, new_values = ? WHERE log_id = ?",
                     (row[4].replace(find, mask_text) if row[4] is not None else None,
                      row[5].replace(find, mask_text) if row[5] is not None else None, log_id))
        if sealed:
            conn.execute("UPDATE audit_seals SET fingerprint = ? WHERE log_id = ?",
                         (fingerprint(key, conn.execute(_ROW_SQL, (log_id,)).fetchone()), log_id))
        conn.execute(triggers["audit_logs_no_update"])
        conn.execute(triggers["audit_seals_no_update"])
        conn.execute("INSERT INTO audit_logs (user_email, user_action, entity_name, entity_id, new_values) "
                     "VALUES (acting_user(), 'AUDIT_MASKED', 'audit_logs', ?, ?)",
                     (log_id, f"{hits} place(s) hidden as {mask_text} ({reason}); backup {os.path.basename(backup)}"))
    return True, "DONE. " + preview + f" Backup: {backup}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None)
    ap.add_argument("--log-id", type=int, required=True)
    ap.add_argument("--find", required=True, help="the private text to hide")
    ap.add_argument("--mask", required=True, help="what to show instead, e.g. ************1111")
    ap.add_argument("--reason", required=True)
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    db_path = a.db or live_db_path()
    ok, msg = mask(db_path, a.log_id, a.find, a.mask, a.reason, a.apply)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
