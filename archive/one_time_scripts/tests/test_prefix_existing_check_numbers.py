"""prefix_existing_check_numbers.py (2026-09-20): 7-digit legacy check numbers become full 10-digit numbers."""
import sqlite3
import sys

import prefix_existing_check_numbers as m
from conftest import make_creditor, make_statement


def _insert(db, statement_id, creditor_id, number):
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO checks_issued (bank_account_id, statement_id, creditor_id, check_number, amount, check_date, status) VALUES (1, ?, ?, ?, 100, '2026-10-01', 'Active')",
        (statement_id, creditor_id, number),
    )
    conn.commit(); conn.close()


def test_new_number_only_touches_short_numeric_values():
    assert m.new_number("2554250") == "3992554250"
    assert m.new_number("12345") == "3990012345"
    assert m.new_number("3992508213") is None
    assert m.new_number("ABC-1") is None
    assert m.new_number(None) is None


def test_renumbering_updates_short_numbers_and_leaves_full_ones(db, monkeypatch):
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    _insert(db, statement_id, creditor_id, "2554250")
    _insert(db, statement_id, creditor_id, "3992508213")
    monkeypatch.setattr(sys, "argv", ["x", "--db", db])

    assert m.main() == 0

    conn = sqlite3.connect(db)
    numbers = sorted(r[0] for r in conn.execute("SELECT check_number FROM checks_issued"))
    assert numbers == ["3992508213", "3992554250"]


def test_a_collision_stops_everything_without_changes(db, monkeypatch):
    creditor_id = make_creditor(db, creditor_type="SUPPLIER")
    statement_id = make_statement(db, creditor_id)
    _conn = sqlite3.connect(db)
    for _t in ("trg_checks_issued_no_duplicate_bi", "trg_checks_issued_no_duplicate_bu"):
        _conn.execute(f"DROP TRIGGER IF EXISTS {_t}")      # simulate legacy data written before the database-level rule existed
    _conn.commit()
    _conn.close()
    _insert(db, statement_id, creditor_id, "2554250")
    _insert(db, statement_id, creditor_id, "3992554250")   # already the full form of the same leaf
    monkeypatch.setattr(sys, "argv", ["x", "--db", db])

    assert m.main() == 1

    conn = sqlite3.connect(db)
    assert sorted(r[0] for r in conn.execute("SELECT check_number FROM checks_issued")) == ["2554250", "3992554250"]
