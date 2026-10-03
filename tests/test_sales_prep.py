"""The places Sales and Stock will plug into (2026-10-03): customers apart from suppliers, selling prices apart from supplier cost lists, and bank deposits tagged as sales."""
import sqlite3
from datetime import date

from conftest import fetchone
from engines import product_catalog as pc
from engines import sales_prep as sp
from engines.acting_user import acting_as


def test_customers_are_kept_apart_from_suppliers_and_not_added_twice(db):
    with acting_as("sales@example.com", "sales"):
        ok, msg, cid = sp.add_customer("Santos Trucking", "FLEET", "30")
        assert ok and cid
        assert not sp.add_customer("santos trucking")[0] and not sp.add_customer("  ")[0] and not sp.add_customer("X", "VIP")[0]
    assert list(sp.list_customers()["customer_name"]) == ["Santos Trucking"]
    assert fetchone(db, "SELECT COUNT(*) FROM creditors WHERE creditor_name = 'Santos Trucking'")[0] == 0
    with acting_as("buyer@example.com", "purchasing"):
        assert not sp.add_customer("Another")[0]                                            # not the buyer's job


def test_a_selling_price_is_dated_and_never_mixes_with_the_cost_lists(db):
    pid, name, _ = pc.get_or_create_product("Tires", "Westlake", size="285/50R20", terrain="A/T", pattern="SL369")
    with acting_as("sales@example.com", "sales"):
        assert sp.set_selling_price(pid, 9500.0, date(2026, 10, 1))[0] and sp.set_selling_price(pid, 9900.0, date(2026, 11, 1))[0]
        assert not sp.set_selling_price(pid, -1)[0] and not sp.set_selling_price(99999, 10)[0]
    assert sp.selling_price_on(pid, date(2026, 10, 15)) == 9500.0 and sp.selling_price_on(pid, date(2026, 11, 2)) == 9900.0
    assert sp.selling_price_on(pid, date(2026, 9, 1)) is None
    assert fetchone(db, "SELECT COUNT(*) FROM supplier_price_list")[0] == 0


def test_only_money_in_can_be_tagged_as_a_deposit_and_the_totals_follow_the_bank(db):
    from engines.bank_statement_service import import_statement
    header = "Transaction Date,Posting Date,Time,Check Number,Transaction Description,Debit Amount,Credit Amount,Balance,Branch/Channel,Subscriber Number/TIN,Sequence Number"
    rows = ['10/05/2026,10/05/2026,23:59:59,0000000000,MO CSH DEP,"0.00","20,000.00","1,020,000.00",,,1',
            '10/05/2026,10/05/2026,23:59:59,0000000000,MO CSH DEP,"0.00","5,000.00","1,025,000.00",,,2',
            '10/06/2026,10/06/2026,23:59:59,0000000000,MO DM,"1,000.00","0.00","1,024,000.00",,,3']
    assert import_statement((header + "\n" + "\n".join(rows) + "\n").encode("utf-8"), "s.csv")["new_lines"] == 3
    ids = [r[0] for r in sqlite3.connect(db).execute("SELECT txn_id FROM bank_transactions ORDER BY txn_id")]
    with acting_as("clerk@example.com", "payables"):
        assert sp.tag_deposit(ids[0], "sales")[0] and sp.tag_deposit(ids[1], "other")[0]
        assert not sp.tag_deposit(ids[2], "sales")[0] and not sp.tag_deposit(ids[0], "gift")[0] and not sp.tag_deposit(99999, "sales")[0]
    assert sp.deposits_by_kind(date(2026, 10, 1), date(2026, 10, 31)) == {"sales": 20000.0, "other": 5000.0, "untagged": 0.0}
    with acting_as("clerk@example.com", "payables"):
        assert sp.tag_deposit(ids[0], None)[0]
    assert sp.deposits_by_kind(date(2026, 10, 1), date(2026, 10, 31))["untagged"] == 20000.0
