"""The cash forecast and its dependants are hidden by default (owner, 2026-10-02: no deposits are known until the Sales / AR cycle exists) and come back with one
setting. While hidden, purchase orders show the load already committed that week instead of a cash verdict."""
from datetime import date, datetime

from conftest import make_creditor
from engines import settings_registry as sr
from engines.cash_insights_service import forecast_if_shown
from engines.clock import set_fake_now
from engines.home_tiles import TILES
from engines.po_rules import cash_check, committed_load, orders_over_plan, order_po, save_po
from engines.price_list_service import add_price
from test_cash_insights import statement  # noqa: F401

TODAY = date(2026, 10, 19)


def test_forecast_if_shown_follows_the_setting(statement, db):
    assert forecast_if_shown(7, TODAY) == {}
    sr.save("show_cash_forecast", 1)
    assert forecast_if_shown(7, TODAY)["daily"] is not None


def test_while_hidden_the_po_cash_check_says_so_and_nothing_is_over_the_plan(statement, db):
    cid = make_creditor(db, name="S", terms="30")
    r = cash_check(cid, 1000.0, date(2026, 10, 20), today=TODAY)
    assert r["known"] is False and "switched off" in r["message"]
    assert orders_over_plan().empty


def test_the_cash_forecast_actions_leave_with_the_forecast(db):
    bank = next(t for t in TILES if t.page_id == "bank-cash")
    assert "Cash forecast" not in [a.label for a in bank.visible_actions]
    sr.save("show_cash_forecast", 1)
    assert "Cash forecast" in [a.label for a in bank.visible_actions]


def test_the_committed_load_of_the_week_counts_checks_and_other_orders(statement, db):
    set_fake_now(datetime(2026, 10, 19, 9, 0))                      # the page's own "today"
    try:
        _committed_load_body(db)
    finally:
        set_fake_now(None)


def _committed_load_body(db):
    cid = make_creditor(db, name="PO Supplier", terms="30")
    add_price(cid, "Tire", "pc", 100, date(2026, 9, 1))
    ok, _, po = save_po(None, cid, TODAY, date(2026, 10, 20), [{"item_name": "Tire", "unit": "pc", "qty": 50}], "")
    assert ok and order_po(po)[0]                                   # 5,000, falls due Fri Nov 20
    load = committed_load(date(2026, 11, 18), 2000.0)                # same week
    assert load["week_start"] == date(2026, 11, 16) and load["orders"] == 5000.0 and load["total"] == 5000.0 and load["adds"] == 2000.0
    assert committed_load(date(2026, 11, 18), 2000.0, exclude_po_id=po)["orders"] == 0.0     # a change to that order does not count it twice
    first = committed_load(date(2026, 10, 21), 0.0)
    assert first["checks"] == 3000.0                                # the late check counts in the first week
    assert committed_load(None) is None
