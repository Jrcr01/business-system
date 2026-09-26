"""restore_database_from_bytes() hardening (2026-09-17): this used to write whatever bytes it
was given straight over the live database with no validation at all -- confirmed to silently
destroy the live db when fed a non-database file. Now it validates the upload and always takes
a safety copy of the current file first."""
import glob
import os
import sqlite3

from engines.backup_service import export_database_backup, restore_database_from_bytes


def test_restore_accepts_a_real_backup_and_keeps_a_safety_copy(db):
    _, backup_bytes = export_database_backup()

    ok, msg = restore_database_from_bytes(backup_bytes)

    assert ok
    assert "restored" in msg.lower()
    safety_copies = glob.glob(f"{db}.bak_before_restore_*")
    assert len(safety_copies) == 1


def test_restore_rejects_non_database_bytes_and_leaves_live_db_untouched(db):
    garbage = b"this is not a sqlite database at all, just some random bytes 12345"

    ok, msg = restore_database_from_bytes(garbage)

    assert not ok
    assert "not restored" in msg.lower()
    # The live db must still be a valid, readable database afterwards.
    conn = sqlite3.connect(db)
    conn.execute("SELECT COUNT(*) FROM creditors")
    conn.close()


def test_restore_rejects_unrelated_sqlite_database(db, tmp_path):
    unrelated_path = str(tmp_path / "unrelated.db")
    conn = sqlite3.connect(unrelated_path)
    conn.execute("CREATE TABLE some_other_app_table (id INTEGER PRIMARY KEY)")
    conn.commit()
    conn.close()
    with open(unrelated_path, "rb") as f:
        unrelated_bytes = f.read()

    ok, msg = restore_database_from_bytes(unrelated_bytes)

    assert not ok
    assert "doesn't look like" in msg.lower()
    conn = sqlite3.connect(db)
    conn.execute("SELECT COUNT(*) FROM creditors")
    conn.close()
