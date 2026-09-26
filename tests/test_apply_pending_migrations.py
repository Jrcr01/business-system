"""apply_pending_migrations.py's schema-diff-and-sync tool -- 2026-09-17 added index diffing
(it previously only synced tables/columns/views, so a new CREATE UNIQUE INDEX in schema.sql
would silently never reach live even after running this)."""
import sqlite3

from apply_pending_migrations import diff_and_sync, SCHEMA_PATH


def _build_target_without_the_new_index(db_path):
    """A target db that already matches schema.sql on every table/column/view (so those diff
    sections have nothing to do) but predates the new checks_issued unique index -- the
    realistic case this tool actually runs against (TEST/live, not a from-scratch database)."""
    conn = sqlite3.connect(db_path)
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.execute("DROP INDEX IF EXISTS idx_checks_issued_unique_number")
    conn.commit()
    conn.close()


def test_diff_and_sync_adds_a_missing_unique_index(tmp_path):
    db_path = str(tmp_path / "target.db")
    _build_target_without_the_new_index(db_path)

    report = diff_and_sync(db_path)

    assert report["indexes_added"] == ["idx_checks_issued_unique_number"]
    assert report["tables_added"] == []
    assert report["columns_added"] == []
    conn = sqlite3.connect(db_path)
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_checks_issued_unique_number" in indexes


def test_diff_and_sync_skips_index_when_data_already_violates_it(tmp_path):
    db_path = str(tmp_path / "target.db")
    _build_target_without_the_new_index(db_path)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO creditors (creditor_id, creditor_name, creditor_type) VALUES (1, 'Test Co', 'SUPPLIER')"
    )
    conn.execute(
        "INSERT INTO order_statements (statement_id, creditor_id, statement_ref, total_amount) VALUES (1, 1, 'REF-1', 0)"
    )
    conn.execute(
        "INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_number, check_date, amount) VALUES (1, 1, 1, '1000', '2026-01-01', 500)"
    )
    conn.execute(
        "INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_number, check_date, amount) VALUES (1, 1, 1, '1000', '2026-01-02', 700)"
    )
    conn.commit()
    conn.close()

    report = diff_and_sync(db_path)

    assert any("needs manual cleanup" in s for s in report["skipped"])
    assert report["indexes_added"] == []


def test_diff_and_sync_dry_run_does_not_touch_the_database(tmp_path):
    db_path = str(tmp_path / "target.db")
    _build_target_without_the_new_index(db_path)

    report = diff_and_sync(db_path, dry_run=True)

    assert report["indexes_added"] == ["idx_checks_issued_unique_number"]  # reports what WOULD happen
    conn = sqlite3.connect(db_path)
    indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    conn.close()
    assert "idx_checks_issued_unique_number" not in indexes  # but nothing was actually created
