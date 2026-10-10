"""The guard rail on the order form (2026-10-03): a day whose payables would pass the daily limit shows a warning and a lighter day; the user decides; ordering anyway is noted on the
Review list. Off when the limit is 0."""
from datetime import date

import pandas as pd
from streamlit.testing.v1 import AppTest

import app
from conftest import make_creditor
from engines import review_flags as rf
from engines.acting_user import acting_as
from engines.check_rules import issue_check
from engines.po_rules import day_guard, expected_pay_date, order_po, save_po
from engines.settings_store import set_setting
from engines.statement_rules import add_invoice

DELIVERY = date(2027, 3, 3)                                  # a Wednesday far ahead; terms 30 days: pays on Apr 3 (a Saturday) -> Monday Apr 5


def _supplier_with_a_heavy_pay_day(db, heavy=8000.0):
    cid = make_creditor(db, name="Guard Supplier", terms="30")
    pay = expected_pay_date(cid, DELIVERY)
    other = make_creditor(db, name="Heavy Day Supplier")
    sid = app.create_order_statement(other)
    add_invoice(other, "H-1", date(2027, 1, 1), date(2027, 2, 1), heavy, [], sid)
    with acting_as("owner@example.com", "owner"):
        assert issue_check(other, heavy, pay, statement_id=sid)[0]                         # checks already due on the day this order would fall due
    return cid, pay


def test_with_the_limit_switched_off_there_is_no_guard(db):
    cid, pay = _supplier_with_a_heavy_pay_day(db)
    set_setting("po_daily_payables_limit", 0, "Purchasing")          # the default is now ₱400,000 (owner, 2026-10-09); 0 switches it off
    assert day_guard(cid, DELIVERY, 4000.0)["on"] is False


def test_a_day_over_the_limit_warns_and_suggests_the_nearest_lighter_delivery(db):
    cid, pay = _supplier_with_a_heavy_pay_day(db)
    set_setting("po_daily_payables_limit", 10000, "Purchasing")
    g = day_guard(cid, DELIVERY, 4000.0)
    assert g["on"] and g["over"] and g["already"] == 8000.0 and g["with_order"] == 12000.0 and g["pay_date"] == pay
    sug = g["suggestion"]
    assert sug and sug["delivery_date"] > DELIVERY and sug["pay_date"] > pay and sug["with_order"] == 4000.0
    assert not day_guard(cid, sug["delivery_date"], 4000.0)["over"]                          # taking the suggestion clears the warning
    assert not day_guard(cid, DELIVERY, 1500.0)["over"]                                       # a small order fits the same day
    assert day_guard(cid, DELIVERY, 4000.0, exclude_po_id=None)["limit"] == 10000.0


def test_an_order_bigger_than_the_limit_alone_gets_a_warning_and_no_day_suggested(db):
    cid, pay = _supplier_with_a_heavy_pay_day(db, heavy=100.0)
    set_setting("po_daily_payables_limit", 5000, "Purchasing")
    g = day_guard(cid, DELIVERY, 9000.0)
    assert g["over"] and g["suggestion"] is None


def test_ordering_anyway_goes_through_and_is_noted_on_the_review_list(db):
    cid, pay = _supplier_with_a_heavy_pay_day(db)
    set_setting("po_daily_payables_limit", 10000, "Purchasing")
    with acting_as("brother@example.com", "purchasing"):
        ok, msg, po = save_po(None, cid, date(2027, 2, 1), DELIVERY, [{"item_name": "Tires", "qty": 2, "est_unit_price": 2000}])
        assert ok, msg
        assert order_po(po)[0]                                                                # never blocked
    flags = rf.open_flags()
    assert list(flags["kind"]) == ["PO_HIGH_DAY"] and "12,000.00" in flags.iloc[0]["detail"] and "10,000.00" in flags.iloc[0]["detail"]


def test_an_order_within_the_limit_leaves_the_review_list_alone(db):
    cid, pay = _supplier_with_a_heavy_pay_day(db, heavy=100.0)
    set_setting("po_daily_payables_limit", 10000, "Purchasing")
    with acting_as("brother@example.com", "purchasing"):
        ok, msg, po = save_po(None, cid, date(2027, 2, 1), DELIVERY, [{"item_name": "Tires", "qty": 2, "est_unit_price": 2000}])
        assert ok and order_po(po)[0]
    assert rf.open_count() == 0


def _form_app():
    import streamlit as st

    from views import purchase_orders

    st.session_state.setdefault("auth_user", {"user_id": 1, "email": "b@x.c", "name": "B", "role": "purchasing"})
    purchase_orders.render()


def test_the_order_form_shows_the_warning_and_the_lighter_day_button(db):
    cid, pay = _supplier_with_a_heavy_pay_day(db)
    set_setting("po_daily_payables_limit", 10000, "Purchasing")
    lines = pd.DataFrame([{"item_name": "Tires", "unit": "pc", "qty": 2, "list_price": 2000.0, "discount_chain": "0"}])
    at = AppTest.from_function(_form_app, default_timeout=60)
    for k, v in {"po_mode": "New order", "po_supplier": cid, "po_lines": lines, "po_order_date": date(2027, 2, 1), "po_delivery": DELIVERY, "_po_sig": (cid, date(2027, 2, 1))}.items():       # _po_sig: the form already started from this supplier and date
        at.session_state[k] = v
    at = at.run()
    assert not at.exception, at.exception
    assert any("High payables" in w.value for w in at.warning)
    btn = next(b for b in at.button if b.key == "po_use_light_day")
    at = btn.click().run()
    assert not at.exception and not any("High payables" in w.value for w in at.warning)      # the suggested day clears the warning
    assert at.session_state["po_delivery"] > DELIVERY
