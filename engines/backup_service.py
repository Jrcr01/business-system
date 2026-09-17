import sqlite3
import io
from datetime import datetime

import os
DB_PATH = os.environ.get("AP_DB_PATH", "data/ap_disbursement.db")

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

def restore_database_from_bytes(uploaded_bytes: bytes) -> bool:
    """Restores the database from an uploaded backup file."""
    try:
        with open(DB_PATH, "wb") as f:
            f.write(uploaded_bytes)
        return True
    except Exception:
        return False