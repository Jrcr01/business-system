"""The Dashboard's one combined "needs attention" list (plan H): every source in one place, most urgent first, info shown but not counted."""
import sqlite3
from datetime import date, timedelta

from conftest import make_creditor, make_statement
from engines import settings_registry as sr
from engines.attention_service import attention_count, attention_items
from engines.statement_rules import add_invoice, create_order_statement


def _items_for(group):
    return [i for i in attention_items() if i["group"] == group]


def test_a_clean_database_has_nothing_to_report(db):
    assert attention_items() == [] and attention_count() == 0


def test_a_stale_uncleared_check_is_a_warning_and_follows_the_setting(db):
    sup = make_creditor(db, name="Sup")
    sid = make_statement(db, sup)
    old = date.today() - timedelta(days=200)
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO checks_issued (bank_account_id, creditor_id, statement_id, check_number, check_date, amount, status)
                        VALUES (1, ?, ?, '3992000001', ?, 1000, 'Active')""", (sup, sid, str(old)))

    stale = [i for i in attention_items() if "still not cleared" in i["text"]]
    assert len(stale) == 1 and stale[0]["level"] == "warning" and stale[0]["page"] == "bank-cash"

    sr.save("stale_check_days", 365)                                    # 200 days old is no longer stale
    assert not [i for i in attention_items() if "still not cleared" in i["text"]]


def test_info_items_are_listed_but_not_counted_and_errors_come_first(db):
    sup = make_creditor(db, name="Sup")
    sid = make_statement(db, sup)
    soon = date.today() + timedelta(days=2)
    old = date.today() - timedelta(days=200)
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO checks_issued (bank_account_id, creditor_id, statement_id, check_number, check_date, amount, status)
                        VALUES (1, ?, ?, NULL, ?, 500, 'Active')""", (sup, sid, str(soon)))            # parked, due within 7 days -> info
        conn.execute("""INSERT INTO checks_issued (bank_account_id, creditor_id, statement_id, check_number, check_date, amount, status)
                        VALUES (1, ?, ?, '3992000002', ?, 900, 'Active')""", (sup, sid, str(old)))     # stale -> warning
    items = attention_items()

    assert [i["level"] for i in items] == sorted((i["level"] for i in items), key=lambda l: {"error": 0, "warning": 1, "info": 2}[l])
    assert any(i["level"] == "info" for i in items)
    assert attention_count(items) == sum(1 for i in items if i["level"] != "info")


def test_supplier_concentration_uses_the_settings_percentage(db):
    future = date.today() + timedelta(days=30)
    rows = []
    for name, amount, number in (("Big Supplier", 600.0, "3992000011"), ("Small Supplier", 400.0, "3992000012")):
        who = make_creditor(db, name=name)
        rows.append((who, create_order_statement(who), number, str(future), amount))
    with sqlite3.connect(db) as conn:
        conn.executemany("""INSERT INTO checks_issued (bank_account_id, creditor_id, statement_id, check_number, check_date, amount, status)
                            VALUES (1, ?, ?, ?, ?, ?, 'Active')""", rows)

    hit = [i for i in attention_items() if "concentration" in i["text"].lower()]
    assert len(hit) == 1 and "Big Supplier" in hit[0]["text"] and "60%" in hit[0]["text"] and hit[0]["page"] == "analytics"

    sr.save("supplier_concentration_pct", 70)                                                    # 60% is now under the alert level
    assert not [i for i in attention_items() if "concentration" in i["text"].lower()]


def test_missing_tables_skip_the_source_instead_of_failing(db):
    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE guarantee_checks")
    attention_items()                                                                           # no exception
