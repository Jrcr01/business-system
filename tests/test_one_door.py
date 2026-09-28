"""Phase F1.2 (2026-09-28): one door to the database. Every connection has the same safety settings (foreign keys, busy timeout, WAL);
nothing in engines / views / app opens the database any other way; copies and restores are WAL-safe (backup API)."""
import pathlib
import re
import sqlite3

from db_location import safe_copy
from engines.backup_service import restore_database_from_bytes
from engines.common import get_conn

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_every_connection_has_the_safety_settings(db):
    conn = get_conn()
    try:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    finally:
        conn.close()


def test_nothing_opens_the_database_except_the_door():
    """Only engines/db_engine.open_db() calls sqlite3.connect; backup_service reads UPLOADED files (not our database yet) read-only."""
    allowed = {("engines/db_engine.py", 1), ("engines/backup_service.py", 2)}
    found = set()
    for p in list((ROOT / "engines").glob("*.py")) + list((ROOT / "views").glob("*.py")) + [ROOT / "app.py"]:
        n = len(re.findall(r"sqlite3\.connect\(", p.read_text(encoding="utf-8")))
        if n:
            found.add((p.relative_to(ROOT).as_posix(), n))
    assert found == allowed, found


def test_links_are_enforced_on_every_connection(db):
    conn = get_conn()
    try:
        try:
            conn.execute("INSERT INTO invoices (creditor_id, doc_ref, invoice_date, due_date, gross_amount, amount) "
                         "VALUES (999999, 'X', '2026-09-01', '2026-10-01', 1, 1)")
            conn.commit()
            raised = False
        except sqlite3.IntegrityError:
            raised = True
        assert raised, "an invoice for a creditor that does not exist must be refused"
    finally:
        conn.close()


def test_safe_copy_includes_saves_still_in_the_wal_file(tmp_path):
    src, dst = tmp_path / "live.db", tmp_path / "copy.db"
    keep = sqlite3.connect(src)
    keep.execute("PRAGMA journal_mode = WAL")
    keep.execute("PRAGMA wal_autocheckpoint = 0")                      # keep the save in the -wal file
    keep.execute("CREATE TABLE t (v TEXT)")
    keep.execute("INSERT INTO t VALUES ('newest save')")
    keep.commit()
    assert (tmp_path / "live.db-wal").stat().st_size > 0
    safe_copy(str(src), str(dst))
    keep.close()
    c = sqlite3.connect(dst)
    try:
        assert c.execute("SELECT v FROM t").fetchall() == [("newest save",)]
        assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"   # a copy is one plain file
    finally:
        c.close()
    assert not (tmp_path / "copy.db-wal").exists()


def test_restore_is_wal_safe(db, tmp_path):
    """Restore copies the upload INTO the live database through SQLite (never over the file), even with a -wal file beside it."""
    live = get_conn(db)
    live.execute("INSERT INTO creditors (creditor_name, creditor_type, terms) VALUES ('Before restore', 'SUPPLIER', '30')")
    live.commit()
    live.close()
    other = tmp_path / "upload.db"
    safe_copy(db, str(other))
    with sqlite3.connect(other) as c:
        c.execute("UPDATE creditors SET creditor_name = 'From the upload' WHERE creditor_name = 'Before restore'")
    ok, msg = restore_database_from_bytes(other.read_bytes())
    assert ok, msg
    check = get_conn(db)
    try:
        assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        names = {r[0] for r in check.execute("SELECT creditor_name FROM creditors")}
        assert "From the upload" in names and "Before restore" not in names
    finally:
        check.close()
