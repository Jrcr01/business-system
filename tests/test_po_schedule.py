"""Proactive purchase orders: before any item is added the page shows what is already committed in the week the order would fall due (day by day), an 8-week strip
with a total per week, and can move the delivery date so the order falls due in a chosen week. The default delivery comes from the supplier's usual lead time."""
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from conftest import fetchone, make_creditor
from engines.cash_insights_service import scheduled_outflows
from engines.clock import set_fake_now
from engines.creditor_rules import set_lead_days
from engines.po_rules import (
    committed_load, default_delivery, delivery_for_week, expected_pay_date, order_po, save_po, week_schedule, weeks_strip,
)
from engines.price_list_service import add_price
from test_cash_insights import statement  # noqa: F401  (checks: 3,000 past its date, 4,000 dated Sat Oct 24 -> Mon Oct 26)

TODAY = date(2026, 10, 19)                          # a Monday


@pytest.fixture(autouse=True)
def _clock():
    set_fake_now(datetime(2026, 10, 19, 9, 0))
    yield
    set_fake_now(None)


def _supplier(db, terms="30"):
    cid = make_creditor(db, name="PO Supplier", terms=terms)
    add_price(cid, "Tire", "pc", 100, date(2026, 9, 1))
    return cid


def _ordered(db, cid, qty, delivery):
    ok, _, po = save_po(None, cid, TODAY, delivery, [{"item_name": "Tire", "unit": "pc", "qty": qty}], "")
    assert ok and order_po(po)[0]
    return po


def test_the_default_delivery_follows_the_suppliers_lead_time_and_is_a_week_when_empty(db):
    cid = _supplier(db)
    assert default_delivery(cid, TODAY) == date(2026, 10, 26)                   # no lead time on file: 7 days
    set_lead_days(cid, 14)
    assert default_delivery(cid, TODAY) == date(2026, 11, 2)
    set_lead_days(cid, None)
    assert default_delivery(cid, TODAY) == date(2026, 10, 26)
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CREDITOR_LEAD_DAYS'")[0] == 2


def test_lead_days_must_be_sensible(db):
    cid = _supplier(db)
    assert set_lead_days(cid, -1)[0] is False and set_lead_days(cid, 400)[0] is False


def test_the_weeks_schedule_lists_what_is_committed_that_week_day_by_day(statement, db):
    cid = _supplier(db)
    _ordered(db, cid, 50, date(2026, 10, 20))                                   # 5,000 falls due Fri Nov 20
    first = week_schedule(date(2026, 10, 21))                                   # this week: the late check counts today
    assert first["week_start"] == date(2026, 10, 19) and first["total"] == 3000.0
    assert list(first["rows"]["day"]) == [date(2026, 10, 19)] and first["rows"]["kind"].iloc[0] == "Supplier checks"
    week2 = week_schedule(date(2026, 10, 28))
    assert week2["total"] == 4000.0 and week2["rows"]["day"].iloc[0] == date(2026, 10, 26) and week2["rows"]["payee"].iloc[0] == "ACME Tires"
    due = week_schedule(date(2026, 11, 20))
    assert due["total"] == 5000.0 and due["rows"]["kind"].iloc[0] == "Purchase orders" and due["rows"]["payee"].iloc[0] == "PO Supplier"


def test_the_schedule_adds_up_to_the_outflows_the_system_already_counts(statement, db):
    cid = _supplier(db)
    _ordered(db, cid, 50, date(2026, 10, 20))
    strip = weeks_strip(date(2026, 11, 20))
    out = scheduled_outflows(TODAY, date(2027, 3, 1))
    assert strip["total"].sum() == pytest.approx(out[(pd_to_date(out["day"]) >= strip["week_start"].min()) & (pd_to_date(out["day"]) <= strip["week_end"].max())]["amount"].sum())


def pd_to_date(series):
    import pandas as pd
    return pd.to_datetime(series).dt.date


def test_an_order_being_changed_does_not_count_itself(statement, db):
    cid = _supplier(db)
    po = _ordered(db, cid, 50, date(2026, 10, 20))
    assert week_schedule(date(2026, 11, 20))["total"] == 5000.0
    assert week_schedule(date(2026, 11, 20), exclude_po_id=po)["total"] == 0.0


def test_the_strip_has_eight_weeks_starting_no_earlier_than_this_week_and_marks_the_lightest(statement, db):
    cid = _supplier(db)
    _ordered(db, cid, 50, date(2026, 10, 20))
    near = weeks_strip(date(2026, 10, 28))                                      # pay week 2: three weeks before would be in the past
    assert len(near) == 8 and near["week_start"].iloc[0] == date(2026, 10, 19)
    assert bool(near[near["week_start"] == date(2026, 10, 26)]["selected"].iloc[0])
    far = weeks_strip(date(2026, 11, 20))
    assert len(far) == 8 and far["week_start"].tolist()[3] == date(2026, 11, 16)    # three weeks before, the pay week, then four after
    assert far["lightest"].sum() == 1 and far[far["lightest"]]["total"].iloc[0] == far["total"].min()
    assert far[far["week_start"] == date(2026, 11, 16)]["total"].iloc[0] == 5000.0


def test_pressing_a_week_finds_the_delivery_that_lands_the_pay_date_in_it(statement, db):
    cid = _supplier(db, terms="30")                                              # whole months: same day next month
    for week in (date(2026, 11, 23), date(2026, 11, 30), date(2026, 12, 7)):
        d = delivery_for_week(cid, week, TODAY)
        pay = expected_pay_date(cid, d)
        assert week <= pay <= week + timedelta(days=6)
        assert expected_pay_date(cid, d - timedelta(days=1)) < week                       # it is the EARLIEST such delivery
    assert delivery_for_week(cid, date(2026, 11, 9), TODAY) is None             # that week cannot be reached: the earliest pay date (order today) is Nov 19


def test_day_count_terms_and_blocked_days_are_respected(statement, db):
    cid = _supplier(db, terms="45")
    d = delivery_for_week(cid, date(2026, 12, 1), TODAY)
    assert d and date(2026, 12, 1) <= expected_pay_date(cid, d) <= date(2026, 12, 7)
    sqlite3.connect(db).execute("INSERT OR REPLACE INTO calendar_blocked_dates (blocked_date, label, kind) VALUES (?, 'holiday', 'holiday')", (expected_pay_date(cid, d).isoformat(),)).connection.commit()
    d2 = delivery_for_week(cid, date(2026, 12, 1), TODAY)
    assert d2 and date(2026, 12, 1) <= expected_pay_date(cid, d2) <= date(2026, 12, 7) and expected_pay_date(cid, d2).weekday() < 5


def test_committed_load_still_answers_for_the_form(statement, db):
    cid = _supplier(db)
    _ordered(db, cid, 50, date(2026, 10, 20))
    load = committed_load(date(2026, 11, 20), 2000.0)
    assert load["orders"] == 5000.0 and load["total"] == 5000.0 and load["adds"] == 2000.0
