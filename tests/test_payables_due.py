"""Payables due (2026-10-02): what is COMMITTED to go out, with no guess about deposits. Checks not yet cleared, loan and card dues from entered statements,
and ordered purchase orders -- by week and by supplier. The cash forecast is hidden (setting show_cash_forecast, off) until Sales / AR brings real deposits."""
from datetime import date

import pytest

from conftest import make_creditor
from engines import settings_registry as sr
from engines.cash_insights_service import scheduled_outflows
from engines.payables_due_service import by_supplier, by_week, next_days
from engines.po_rules import order_po, save_po
from engines.price_list_service import add_price
from test_cash_insights import statement  # noqa: F401  (a bank statement closing at 10,000 on Oct 16 2026; checks of 3,000 late and 4,000 on Oct 26)

TODAY = date(2026, 10, 19)                            # a Monday


def _order(db, qty, delivery=date(2026, 10, 20)):
    cid = make_creditor(db, name="PO Supplier", terms="30")
    add_price(cid, "Tire", "pc", 100, date(2026, 9, 1))
    ok, _, po = save_po(None, cid, TODAY, delivery, [{"item_name": "Tire", "unit": "pc", "qty": qty}], "")
    assert ok and order_po(po)[0]
    return po


def test_the_forecast_is_hidden_by_default_and_the_switch_brings_it_back(db):
    assert sr.forecast_shown() is False and sr.int_value("show_cash_forecast") == 0
    sr.save("show_cash_forecast", 1)
    assert sr.forecast_shown() is True


def test_next_days_adds_up_what_can_hit_the_bank_and_shows_the_balance_beside_it(statement, db):
    r = next_days(7, today=TODAY)
    assert r["checks"] == 1 and r["amount"] == 3000.0               # the late check counts today; the 4,000 one is dated Oct 26 (day 7)
    assert r["balance"] == 10000.0 and r["balance_as_of"] == date(2026, 10, 16)
    wider = next_days(14, today=TODAY)
    assert wider["checks"] == 2 and wider["amount"] == 7000.0


def test_weekly_totals_equal_the_forecasts_outflows_and_split_by_kind(statement, db):
    _order(db, 50)                                                  # 5,000 owed on Nov 2 (Oct 20 + 30 days, moved to a bank day)
    weeks = by_week(12, today=TODAY)
    out = scheduled_outflows(TODAY, date(2027, 1, 11))
    assert weeks["Total"].sum() == pytest.approx(out["amount"].sum())
    assert set(weeks.columns) >= {"week", "Supplier checks", "Lender checks", "Bank loans", "Credit cards", "Purchase orders", "Total"}
    row = weeks[weeks["week"] == date(2026, 11, 16)].iloc[0]
    assert row["Purchase orders"] == 5000.0 and row["Supplier checks"] == 0.0
    first = weeks[weeks["week"] == date(2026, 10, 19)].iloc[0]
    assert first["Supplier checks"] == 3000.0                       # the late check, on the first day


def test_by_supplier_lists_who_can_deposit_and_which_orders_are_coming(statement, db):
    _order(db, 50, delivery=date(2026, 10, 20))
    df = by_supplier(60, today=TODAY).set_index("payee")
    assert df.loc["ACME Tires", "checks"] == 2 and df.loc["ACME Tires", "check_amount"] == 7000.0
    assert df.loc["PO Supplier", "order_amount"] == 5000.0 and df.loc["PO Supplier", "checks"] == 0
    assert list(by_supplier(60, today=TODAY)["total"]) == sorted(by_supplier(60, today=TODAY)["total"], reverse=True)


def test_nothing_committed_is_an_empty_but_valid_answer(db):
    assert next_days(7, today=TODAY)["amount"] == 0 and by_week(4, today=TODAY).empty is False and by_supplier(30, today=TODAY).empty
