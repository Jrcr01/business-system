"""Logging a spoiled check (2026-09-27): the same number can't be logged twice, can't be the number of a check on file, and a number outside
every registered booklet is flagged for confirmation."""
from datetime import date

import app
from conftest import make_creditor, make_statement, fetchone
from engines.check_engine import format_metrobank_check_number

BANK = 1


def _booklet(db, start=3992554301, end=3992554400):
    ok, msg = app.create_booklet(BANK, "B1", start, end, date(2026, 1, 1))
    assert ok, msg


def test_a_number_can_only_be_logged_as_spoiled_once_however_it_is_typed(db):
    _booklet(db)
    assert app.log_spoiled_check("3992554303", "jam")[0]

    ok, msg = app.log_spoiled_check("3992554303", "again")
    assert not ok and "already logged as spoiled" in msg
    ok, msg = app.log_spoiled_check("2554303", "typed short")            # same leaf, 7-digit form
    assert not ok and "already logged as spoiled" in msg
    assert fetchone(db, "SELECT COUNT(*) FROM spoiled_checks")[0] == 1
    assert fetchone(db, "SELECT check_number FROM spoiled_checks")[0] == "3992554303"     # stored in the full 10-digit form


def test_a_number_that_belongs_to_a_check_on_file_cannot_be_spoiled(db):
    import sqlite3
    _booklet(db)
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) VALUES (1, ?, ?, '3992554310', 500, '2026-10-01', 'Active')",
                 (statement_id, creditor_id))
    conn.commit()
    conn.close()

    ok, msg = app.log_spoiled_check("3992554310", "oops")

    assert not ok and "already the number of a Active check to ACME Tires" in msg
    assert fetchone(db, "SELECT COUNT(*) FROM spoiled_checks")[0] == 0


def test_a_number_outside_every_booklet_is_flagged_but_one_inside_is_not(db):
    _booklet(db)
    _, blocking, warnings = app.spoiled_check_problems("3992554350")
    assert blocking == [] and warnings == []

    _, blocking, warnings = app.spoiled_check_problems("3992599999")
    assert blocking == [] and len(warnings) == 1 and "not inside any registered booklet" in warnings[0]


def test_blank_or_non_numeric_numbers_are_refused(db):
    assert app.spoiled_check_problems("")[1] == ["Check number is required."]
    assert "digits only" in app.spoiled_check_problems("39925ABC")[1][0]
    assert not app.log_spoiled_check("abc", "x")[0]


# ---- the hard rule lives in the database itself, so no code path can get round it (2026-09-27) ----

def _raw(db):
    import sqlite3
    return sqlite3.connect(db)


def _insert_check(conn, number, statement_id, creditor_id):
    conn.execute("INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) VALUES (1, ?, ?, ?, 100, '2026-10-01', 'Active')",
                 (statement_id, creditor_id, number))


def test_database_refuses_a_second_check_with_the_same_number_however_typed(db):
    import sqlite3
    import pytest
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    conn = _raw(db)
    _insert_check(conn, "3992554310", statement_id, creditor_id)
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        _insert_check(conn, "3992554310", statement_id, creditor_id)          # identical
    with pytest.raises(sqlite3.IntegrityError, match="Duplicate check number"):
        _insert_check(conn, "2554310", statement_id, creditor_id)             # the same leaf typed in 7 digits
    conn.close()


def test_database_refuses_a_spoiled_number_that_is_a_check_and_a_check_number_that_is_spoiled(db):
    import sqlite3
    import pytest
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    conn = _raw(db)
    _insert_check(conn, "3992554310", statement_id, creditor_id)
    conn.execute("INSERT INTO spoiled_checks (bank_account_id, check_number) VALUES (1, '3992554320')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="already the number of a check"):
        conn.execute("INSERT INTO spoiled_checks (bank_account_id, check_number) VALUES (1, '3992554310')")
    with pytest.raises(sqlite3.IntegrityError, match="already logged as spoiled"):
        _insert_check(conn, "3992554320", statement_id, creditor_id)
    with pytest.raises(sqlite3.IntegrityError, match="Duplicate check number"):
        conn.execute("INSERT INTO spoiled_checks (bank_account_id, check_number) VALUES (1, '2554320')")     # same leaf, 7 digits
    conn.close()


def test_database_refuses_renumbering_a_check_onto_a_used_number(db):
    import sqlite3
    import pytest
    creditor_id = make_creditor(db, name="ACME Tires")
    statement_id = make_statement(db, creditor_id)
    conn = _raw(db)
    _insert_check(conn, "3992554310", statement_id, creditor_id)
    _insert_check(conn, None, statement_id, creditor_id)
    conn.execute("INSERT INTO spoiled_checks (bank_account_id, check_number) VALUES (1, '3992554320')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError, match="another check"):
        conn.execute("UPDATE checks_issued SET check_number = '3992554310' WHERE check_number IS NULL")
    with pytest.raises(sqlite3.IntegrityError, match="spoiled"):
        conn.execute("UPDATE checks_issued SET check_number = '3992554320' WHERE check_number IS NULL")
    conn.execute("UPDATE checks_issued SET check_number = '3992554311' WHERE check_number IS NULL")     # a free number is fine
    conn.close()


def test_schema_sync_adds_the_triggers_to_an_older_database(tmp_path):
    import sqlite3
    from apply_pending_migrations import diff_and_sync
    path = str(tmp_path / "old.db")
    conn = sqlite3.connect(path)
    with open("schema.sql", encoding="utf-8") as f:
        conn.executescript(f.read())
    conn.execute("DROP TRIGGER trg_checks_issued_no_duplicate_bi")
    conn.commit()
    conn.close()

    report = diff_and_sync(path)

    assert report["triggers_synced"] == ["trg_checks_issued_no_duplicate_bi"]
    assert diff_and_sync(path, dry_run=True)["triggers_synced"] == []
