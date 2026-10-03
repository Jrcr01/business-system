"""Supplier price lists (B1): typed prices and a CSV/Excel import. Free-text item name per supplier; a price applies from its effective date until a later one."""
from datetime import date

import pandas as pd

from conftest import fetchone, make_creditor
from engines.price_list_service import (
    add_price, current_prices, import_price_rows, item_names, price_history, price_on, set_price_active,
)


def test_a_typed_price_is_found_on_and_after_its_date_until_a_later_one(db):
    cid = make_creditor(db)
    assert add_price(cid, "Tire 175/70R13", "pc", 1850, date(2026, 10, 1))[0]
    assert add_price(cid, "Tire 175/70R13", "pc", 1900, date(2026, 11, 1))[0]
    assert price_on(cid, "Tire 175/70R13", date(2026, 9, 30)) is None             # before the first price
    assert price_on(cid, "Tire 175/70R13", date(2026, 10, 15))["unit_price"] == 1850
    assert price_on(cid, "Tire 175/70R13", date(2026, 11, 1))["unit_price"] == 1900
    assert price_on(cid, "Tire 175/70R13", date(2027, 1, 1))["unit_price"] == 1900


def test_item_names_match_ignoring_case_and_spaces_and_keep_the_first_spelling(db):
    cid = make_creditor(db)
    add_price(cid, "Tire  175/70R13", "pc", 1850, date(2026, 10, 1))
    ok, _ = add_price(cid, "tire 175/70r13 ", "pc", 1900, date(2026, 11, 1))
    assert ok
    assert item_names(cid) == ["Tire 175/70R13"]                                    # one item, not two
    assert price_on(cid, "TIRE 175/70R13", date(2026, 12, 1))["unit_price"] == 1900


def test_prices_are_per_supplier(db):
    a, b = make_creditor(db, name="A"), make_creditor(db, name="B")
    add_price(a, "Tube", "pc", 100, date(2026, 10, 1))
    assert price_on(b, "Tube", date(2026, 10, 2)) is None


def test_bad_prices_are_refused(db):
    cid = make_creditor(db)
    assert not add_price(cid, "  ", "pc", 10, date(2026, 10, 1))[0]
    assert not add_price(cid, "Tire", "pc", -1, date(2026, 10, 1))[0]
    assert not add_price(cid, "Tire", "pc", "abc", date(2026, 10, 1))[0]
    assert fetchone(db, "SELECT COUNT(*) FROM supplier_price_list")[0] == 0


def test_the_same_item_and_date_is_refused_unless_replacing(db):
    cid = make_creditor(db)
    add_price(cid, "Tire", "pc", 1850, date(2026, 10, 1))
    ok, msg = add_price(cid, "Tire", "pc", 1999, date(2026, 10, 1))
    assert not ok and "already" in msg.lower()
    ok, _ = add_price(cid, "Tire", "pc", 1999, date(2026, 10, 1), replace=True)
    assert ok and price_on(cid, "Tire", date(2026, 10, 1))["unit_price"] == 1999


def test_current_prices_show_the_latest_per_item_with_the_change(db):
    cid = make_creditor(db)
    add_price(cid, "Tire", "pc", 1850, date(2026, 10, 1))
    add_price(cid, "Tire", "pc", 1900, date(2026, 11, 1))
    add_price(cid, "Tube", "pc", 100, date(2026, 10, 1))
    df = current_prices(cid, on=date(2026, 11, 5)).set_index("item_name")
    assert df.loc["Tire", "unit_price"] == 1900 and df.loc["Tire", "previous_price"] == 1850
    assert df.loc["Tube", "unit_price"] == 100 and pd.isna(df.loc["Tube", "previous_price"])
    assert list(price_history(cid, "tire")["unit_price"]) == [1900, 1850]            # newest first


def test_a_deactivated_price_is_not_used(db):
    cid = make_creditor(db)
    add_price(cid, "Tire", "pc", 1850, date(2026, 10, 1))
    pid = fetchone(db, "SELECT price_id FROM supplier_price_list")[0]
    assert set_price_active(pid, False)[0]
    assert price_on(cid, "Tire", date(2026, 10, 5)) is None


def test_import_adds_new_prices_and_skips_or_replaces_ones_already_there(db):
    cid = make_creditor(db)
    add_price(cid, "Tire", "pc", 1850, date(2026, 10, 1))
    rows = [{"item_code": "", "item_name": "Tire", "unit": "pc", "unit_price": 1900.0, "effective_date": None},
            {"item_code": "", "item_name": "Tube", "unit": "pc", "unit_price": 100.0, "effective_date": None}]
    r1 = import_price_rows(cid, rows, default_date=date(2026, 10, 1), on_duplicate="skip")
    assert r1 == {"added": 1, "replaced": 0, "skipped": 1}
    assert price_on(cid, "Tire", date(2026, 10, 1))["unit_price"] == 1850
    r2 = import_price_rows(cid, rows, default_date=date(2026, 10, 1), on_duplicate="replace")
    assert r2 == {"added": 0, "replaced": 2, "skipped": 0}
    assert price_on(cid, "Tire", date(2026, 10, 1))["unit_price"] == 1900
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'PRICE_LIST_IMPORTED'")[0] == 2


def test_adding_a_price_is_audited_with_who(db):
    cid = make_creditor(db)
    add_price(cid, "Tire", "pc", 1850, date(2026, 10, 1))
    row = fetchone(db, "SELECT user_email, new_values FROM audit_logs WHERE user_action = 'PRICE_ADDED'")
    assert row and row[0] and "Tire" in row[1]
