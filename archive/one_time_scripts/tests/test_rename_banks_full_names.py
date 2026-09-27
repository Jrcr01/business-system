"""Test of the one-time rename_banks_full_names.py script (archived 2026-09-27)."""


def test_rename_script_only_touches_bank_records(db):
    import sqlite3
    import rename_banks_full_names as rn
    from conftest import make_creditor
    bank = make_creditor(db, name="BDO", creditor_type="BANK_LOAN")
    supplier = make_creditor(db, name="BDO", creditor_type="SUPPLIER")   # a supplier that happens to share the letters
    assert rn.main.__module__ == "rename_banks_full_names"
    import sys
    sys.argv = ["x", "--db", db]
    assert rn.main() == 0
    names = dict(sqlite3.connect(db).execute("SELECT creditor_id, creditor_name FROM creditors WHERE creditor_id IN (?, ?)", (bank, supplier)).fetchall())
    assert names[bank] == "BDO Unibank, Inc." and names[supplier] == "BDO"
