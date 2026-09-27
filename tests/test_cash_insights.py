"""Cash insights (2026-09-27): cash position, forecast, float, money in, unusual items, working capital -- checked against hand-worked
figures on a made-up MBOS-layout statement."""
import sqlite3
from datetime import date

import pytest

import app
from conftest import make_creditor, make_statement
from engines.bank_statement_service import import_statement
from engines.cash_insights_service import (
    cash_position, expected_daily_inflow, float_summary, forecast, inflow_trends, scheduled_outflows, set_buffer, unusual_items, working_capital,
)
from test_bank_statement_import import _csv, _row


@pytest.fixture
def statement(db, monkeypatch, tmp_path):
    """Two full weeks (Mon Oct 5 - Fri Oct 16, 2026): cash deposits of 1,000 every Monday and 500 every Wednesday, one of our checks
    clearing, a returned deposited check, a huge cash deposit, and a bank debit memo. Closing balance 10,000."""
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    creditor_id = make_creditor(db, name="ACME Tires")
    sid = make_statement(db, creditor_id)
    app.add_invoice(creditor_id, "INV-1", date(2026, 9, 1), date(2026, 10, 1), 9000.0, [], sid)
    checks = {}
    for key, num, amt, d in (("cleared", "3992000001", 2000.0, "2026-10-06"), ("late", "3992000002", 3000.0, "2026-10-01"),
                             ("ahead", "3992000003", 4000.0, "2026-10-24")):
        _, _, cid = app.issue_check(creditor_id, amt, date.fromisoformat(d), statement_id=sid)
        checks[key] = cid
        with sqlite3.connect(db) as conn:
            conn.execute("UPDATE checks_issued SET check_number = ? WHERE check_id = ?", (num, cid))
    lines, bal = [], 0.0

    def add(d, check, desc, debit, credit, seq):
        nonlocal bal
        bal = round(bal - debit + credit, 2)
        lines.append(_row(d, check, desc, f"{debit:,.2f}", f"{credit:,.2f}", f"{bal:,.2f}", seq))
    add("10/05/2026", "0000000000", "MO CSH DEP", 0, 12000, "1")            # opening money (a Monday)
    add("10/06/2026", "3992000001", "IM IN- CLRNG- BATCH", 2000, 0, "")      # our check clears
    add("10/07/2026", "0000000000", "ET CR IBFT", 0, 500, "2")               # Wednesday
    add("10/09/2026", "0000000000", "DE DM RET CHK", 300, 0, "3")            # a customer's check bounced
    add("10/09/2026", "0000000000", "MO DM", 700, 0, "4")                    # bank debit memo
    add("10/12/2026", "0000000000", "MO CSH DEP", 0, 1000, "5")              # Monday
    add("10/14/2026", "0000000000", "ET CR IBFT", 0, 500, "6")               # Wednesday
    add("10/16/2026", "0000000000", "MO DM", 1000, 0, "7")                   # -> closing 10,000
    import_statement(_csv(lines), "oct.csv")
    return checks


def test_position_and_expected_money_in_per_weekday(statement):
    assert cash_position() == {"balance": 10000.0, "as_of": date(2026, 10, 16)}
    inflow = expected_daily_inflow()
    assert inflow[0] == pytest.approx((12000 + 1000) / 2)     # two Mondays in the period
    assert inflow[2] == pytest.approx((500 + 500) / 2) and inflow[5] == 0.0 and inflow[1] == 0.0


def test_outflows_put_late_checks_on_the_first_day_and_weekend_dues_on_monday(statement):
    out = scheduled_outflows(date(2026, 10, 19), date(2026, 10, 31))
    by_amount = dict(zip(out["amount"], out["day"]))
    assert by_amount[3000.0] == date(2026, 10, 19)            # dated Oct 1, never cleared -> can hit any day: counted on the first day
    assert by_amount[4000.0] == date(2026, 10, 26)            # dated Sat Oct 24 -> Monday
    assert 2000.0 not in by_amount                            # cleared by the bank: not money going out any more


def test_forecast_adds_expected_money_in_and_flags_the_buffer(statement):
    set_buffer(8000)
    fc = forecast(days=7, today=date(2026, 10, 19))            # Monday Oct 19
    d = fc["daily"].set_index("day")
    assert d.loc[date(2026, 10, 19), "balance"] == 7000.0      # today: 10,000 - late check 3,000 (today's money in not counted)
    assert d.loc[date(2026, 10, 21), "balance"] == 7500.0      # + Wednesday's expected 500
    assert fc["first_below"] == date(2026, 10, 19) and fc["lowest"][1] == 7000.0


def test_float_waiting_checks_by_age(statement):
    fs = float_summary()
    assert list(fs["overdue"]["check_number"]) == ["3992000002"] and fs["overdue"]["days"].iloc[0] == 15
    assert list(fs["ahead"]["check_number"]) == ["3992000003"]
    assert fs["buckets"].set_index("age").loc["8-30 days", "amount"] == 3000.0


def test_money_in_by_week_ignores_partial_weeks(statement):
    it = inflow_trends()
    wk = it["weekly"]
    assert list(wk["money_in"]) == [12500.0, 1500.0] and not wk["partial"].any()
    assert it["weekly_average"] == 7000.0 and list(wk["slow"]) == [False, True]


def test_unusual_items_and_working_capital(statement):
    u = unusual_items()
    assert list(u["returned"]["debit"]) == [300.0]
    assert sorted(u["debits"]["debit"]) == [700.0, 1000.0]
    w = working_capital(today=date(2026, 10, 19))
    win = w["windows"].set_index("window")
    assert win.loc["Next 7 days", "due"] == 7000.0 and win.loc["Next 7 days", "cover"] == pytest.approx(10000 / 7000)
    assert win.loc["Next 7 days", "left"] == 3000.0
    assert w["days_of_cash"] == pytest.approx(10000 / (7000 / 30))
