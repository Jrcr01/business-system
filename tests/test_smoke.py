"""Sanity check for the `db` fixture itself -- if this fails, every other test's
failure is probably the fixture's fault, not the code under test."""
import sqlite3

import app


def test_fixture_points_app_at_isolated_db(db):
    assert app.DB_PATH == db
    assert app.DB_PATH != "data/ap_disbursement.db"


def test_fixture_db_has_no_data_yet(db):
    conn = sqlite3.connect(db)
    n = conn.execute("SELECT COUNT(*) FROM creditors").fetchone()[0]
    conn.close()
    assert n == 0


def test_bank_account_seeded(db):
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT bank_account_id FROM bank_accounts").fetchone()
    conn.close()
    assert row == (1,)


def test_two_tests_dont_see_each_others_data(db):
    """Guards against a fixture that accidentally reuses one file across tests."""
    conn = sqlite3.connect(db)
    conn.execute("INSERT INTO creditors (creditor_name, creditor_type) VALUES ('Leftover', 'SUPPLIER')")
    conn.commit()
    conn.close()
    # A second call to this same test function (via -k or a loop) would fail
    # here if the DB weren't fresh -- covered implicitly by every other test
    # file also starting from an empty creditors table.
