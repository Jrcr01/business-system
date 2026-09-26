import sqlite3
import io
import os
import shutil
import tempfile
from datetime import datetime

DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

# A handful of core tables that only exist in this app's schema -- enough to reject an
# unrelated SQLite file (or a random file with a valid SQLite header but garbage content)
# without needing a full schema diff.
_EXPECTED_TABLES = ("creditors", "checks_issued", "order_statements", "credit_cards")

def export_database_backup() -> tuple[str, bytes]:
    """Creates a live in-memory hot backup of ap_disbursement.db."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_filename = f"ap_disbursement_backup_{timestamp}.db"
    
    src_conn = sqlite3.connect(DB_PATH)
    dest_conn = sqlite3.connect(":memory:")
    src_conn.backup(dest_conn)
    
    try:
        backup_bytes = dest_conn.serialize()
    except AttributeError:
        buffer = io.BytesIO()
        for line in dest_conn.iterdump():
            buffer.write(f"{line}\n".encode('utf-8'))
        backup_bytes = buffer.getvalue()
        backup_filename = f"ap_disbursement_backup_{timestamp}.sql"

    src_conn.close()
    dest_conn.close()
    return backup_filename, backup_bytes

def restore_database_from_bytes(uploaded_bytes: bytes) -> tuple[bool, str]:
    """Restores the database from an uploaded backup file.

    2026-09-17 hardening: this used to write the uploaded bytes straight over the live
    database file with no check that they were even a valid SQLite database -- an empty file,
    a corrupted download, or the wrong file entirely would silently destroy the live database
    (confirmed: feeding it 60 bytes of plain text made the live db unreadable while this
    function still reported success). Now it validates the upload against a temp copy first,
    and always takes a timestamped safety copy of the CURRENT live file before overwriting it,
    so a bad restore can still be undone even after the fact.
    """
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            f.write(uploaded_bytes)

        try:
            check_conn = sqlite3.connect(tmp_path)
            integrity = check_conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                return False, f"Upload failed SQLite's integrity check ({integrity}) -- not restored."
            tables = {row[0] for row in check_conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            missing = [t for t in _EXPECTED_TABLES if t not in tables]
            if missing:
                return False, f"This doesn't look like an AP Disbursement backup (missing table(s): {', '.join(missing)}) -- not restored."
        except sqlite3.DatabaseError:
            return False, "Uploaded file isn't a valid SQLite database -- not restored."
        finally:
            check_conn.close()

        if os.path.exists(DB_PATH):
            safety_copy = f"{DB_PATH}.bak_before_restore_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            shutil.copy2(DB_PATH, safety_copy)
        shutil.copy2(tmp_path, DB_PATH)
        return True, "Database restored. The version just replaced was saved alongside it as a .bak_before_restore file, in case this needs undoing."
    finally:
        os.remove(tmp_path)