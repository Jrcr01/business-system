"""Purchase orders in the cash plan (B3): open orders count in the forecast on their pay date, a new order is checked against the cash on that date, a later
order date is suggested when it does not fit, and an order placed over the plan is flagged for the owner (who is told, not asked)."""
import sqlite3
from datetime import date, timedelta

import pytest

from conftest import fetchone, make_creditor
from engines import settings_registry as sr
from engines.attention_service import attention_items
from engines.cash_insights_service import forecast, scheduled_outflows, set_buffer
from engines.check_date_planner import _baseline
from engines.po_rules import (
    acknowledge_over_plan, cancel_po, cash_check, open_commitments, order_po, orders_over_plan, save_po, suggest_order_date,
)
from engines.price_list_service import add_price
from test_cash_insights import statement  # noqa: F401  (fixture: a bank statement closing at 10,000 on Oct 16, 2026)

TODAY = date(2026, 10, 19)                       # a Monday; late check of 3,000 counts today, 4,000 on Oct 26


def _supplier(db, terms="30"):
    cid = make_creditor(db, name="PO Supplier", terms=terms)
    add_price(cid, "Tire", "pc", 100, date(2026, 9, 1))
    return cid


def _order(db, cid, qty, delivery=date(2026, 10, 20), ordered=True):
    ok, msg, po_id = save_po(None, cid, TODAY, delivery, [{"item_name": "Tire", "unit": "pc", "qty": qty}], "")
    assert ok, msg
    if ordered:
        assert order_po(po_id)[0]
    return po_id


def test_an_open_order_is_money_going_out_on_its_pay_date_and_nothing_else_is(statement, db):
    cid = _supplier(db)
    po = _order(db, cid, 50)                                              # 5,000, delivery Oct 20 + 30 days -> Nov 20 (Friday)
    draft = _order(db, cid, 10, ordered=False)
    cancelled = _order(db, cid, 10)
    cancel_po(cancelled)
    out = scheduled_outflows(TODAY, date(2026, 12, 31))
    pos = out[out["track"] == "Purchase order"]
    assert list(pos["day"]) == [date(2026, 11, 20)] and list(pos["amount"]) == [5000.0]
    assert pos.iloc[0]["payee"] == "PO Supplier" and "PO 0001" in pos.iloc[0]["reference"]
    assert open_commitments(TODAY, date(2026, 12, 31))["po_id"].tolist() == [po]


def test_the_forecast_balance_drops_by_the_order_from_its_pay_date(statement, db):
    cid = _supplier(db)
    before = forecast(60, TODAY)["daily"].set_index("day")["balance"]
    _order(db, cid, 50)
    after = forecast(60, TODAY)["daily"].set_index("day")["balance"]
    assert after[date(2026, 11, 19)] == before[date(2026, 11, 19)]
    assert after[date(2026, 11, 20)] == before[date(2026, 11, 20)] - 5000 and after[date(2026, 12, 15)] == before[date(2026, 12, 15)] - 5000


def test_the_setting_can_leave_orders_out_of_the_forecast(statement, db):
    cid = _supplier(db)
    _order(db, cid, 50)
    from engines.settings_store import set_setting
    set_setting("po_in_forecast", "0")
    assert scheduled_outflows(TODAY, date(2026, 12, 31))["track"].eq("Purchase order").sum() == 0


def test_the_planner_sees_the_orders_too(statement, db):
    cid = _supplier(db)
    _order(db, cid, 50)
    for start_cash in (None, 50_000.0):                                                             # the bank balance, or cash typed in on the page
        base = _baseline(TODAY, date(2026, 12, 1), start_cash, None, set())
        assert base["out"][base["days"].index(date(2026, 11, 20))] == 5000.0                        # part of "already going out" that day


def test_a_small_order_fits_and_a_big_one_does_not(statement, db):
    cid = _supplier(db)
    set_buffer(5000)
    small = cash_check(cid, 1000.0, date(2026, 10, 20), today=TODAY)
    assert small["known"] and small["ok"] and small["pay_date"] == date(2026, 11, 20) and small["shortfall"] == 0
    big = cash_check(cid, 10_000_000.0, date(2026, 10, 20), today=TODAY)
    assert big["known"] and not big["ok"] and big["shortfall"] > 0 and big["lowest"][1] < 5000


def test_an_order_already_counted_does_not_count_twice_when_it_is_checked_again(statement, db):
    cid = _supplier(db)
    set_buffer(0)
    po = _order(db, cid, 50)
    again = cash_check(cid, 5000.0, date(2026, 10, 20), today=TODAY, exclude_po_id=po)
    first = cash_check(cid, 5000.0, date(2026, 10, 20), today=TODAY)
    assert first["lowest"][1] == pytest.approx(again["lowest"][1] - 5000.0)       # without exclude, its own 5,000 is in the forecast already


def test_without_a_recent_bank_balance_the_answer_is_unknown_not_over(db):
    cid = _supplier(db)
    r = cash_check(cid, 1000.0, date(2026, 10, 20), today=TODAY)
    assert r["known"] is False and r["ok"] is None and r["message"]


def test_a_later_order_date_is_suggested_when_it_does_not_fit_yet(statement, db):
    cid = _supplier(db)
    set_buffer(5000)
    amount, delivery = 40_000.0, date(2026, 10, 20)
    now = cash_check(cid, amount, delivery, today=TODAY)
    assert not now["ok"]
    s = suggest_order_date(cid, amount, TODAY, delivery, today=TODAY, max_defer_days=90)
    assert s and s["days_later"] > 0 and s["order_date"] == TODAY + timedelta(days=s["days_later"]) and s["delivery_date"] == delivery + timedelta(days=s["days_later"])
    assert cash_check(cid, amount, s["delivery_date"], today=TODAY)["ok"]                          # the suggestion really fits
    assert not cash_check(cid, amount, s["delivery_date"] - timedelta(days=1), today=TODAY)["ok"]   # and it is the first date that does


def test_no_suggestion_when_nothing_in_reach_fits(statement, db):
    cid = _supplier(db)
    set_buffer(5000)
    assert suggest_order_date(cid, 10_000_000.0, TODAY, date(2026, 10, 20), today=TODAY, max_defer_days=30) is None
    assert suggest_order_date(cid, 1000.0, TODAY, date(2026, 10, 20), today=TODAY) is None          # it already fits: nothing to suggest


def test_ordering_over_the_plan_is_allowed_but_flagged_until_the_owner_acknowledges(statement, db):
    cid = _supplier(db)
    set_buffer(5000)
    small = _order(db, cid, 5)                                                                      # 500: fits
    big = _order(db, cid, 400_000)                                                                  # 40,000,000 estimate: does not
    row = fetchone(db, "SELECT over_cash_plan, cash_note, cash_ack_at FROM purchase_orders WHERE po_id = ?", (big,))
    assert row[0] == 1 and row[1] and row[2] is None
    assert fetchone(db, "SELECT over_cash_plan FROM purchase_orders WHERE po_id = ?", (small,))[0] == 0
    assert orders_over_plan()["po_id"].tolist() == [big]
    assert any("over the cash plan" in i["text"] for i in attention_items())
    assert acknowledge_over_plan(big)[0]
    assert orders_over_plan().empty and not any("over the cash plan" in i["text"] for i in attention_items())


def test_a_draft_is_never_flagged_and_changing_an_order_updates_its_flag(statement, db):
    cid = _supplier(db)
    set_buffer(5000)
    po = _order(db, cid, 400_000)
    assert fetchone(db, "SELECT over_cash_plan FROM purchase_orders WHERE po_id = ?", (po,))[0] == 1
    ok, _, _ = save_po(po, cid, TODAY, date(2026, 10, 20), [{"item_name": "Tire", "unit": "pc", "qty": 5}], "")
    assert ok and fetchone(db, "SELECT over_cash_plan, cash_ack_at FROM purchase_orders WHERE po_id = ?", (po,))[0] == 0
    draft = _order(db, cid, 400_000, ordered=False)
    assert fetchone(db, "SELECT over_cash_plan FROM purchase_orders WHERE po_id = ?", (draft,))[0] == 0


def test_the_settings_exist_with_their_defaults(db):
    assert sr.int_value("po_in_forecast") == 1 and sr.int_value("po_max_defer_days") == 30
