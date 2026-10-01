"""Credit card due forecast (owner, 2026-09-29): "estimated dues by week for the next 4-5 months, from each card's history, replaced
by the real amount once the statement is entered". Owner's choices: estimates count as cash going out (planner + Cash forecast);
amount = median of the last 3 statements (low-high shown); 5 months ahead."""
import sqlite3
from datetime import date

from conftest import make_creditor
from engines.cc_forecast_service import estimated_card_dues, forecast_by_week
from engines.cc_service import paid_from_bank_ids, set_paid_from_bank

TODAY = date(2026, 9, 29)


def _card(db, name, last4, statement_day=None, active=1):
    bank = make_creditor(db, name=f"Bank {name}", creditor_type="CREDIT_CARD", terms="")
    with sqlite3.connect(db) as conn:
        return conn.execute("INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit, is_active, statement_day) "
                            "VALUES (?, ?, ?, 1, ?, ?)", (bank, name, last4, active, statement_day)).lastrowid


def _stmt(db, card, due, amount, status="Paid"):
    with sqlite3.connect(db) as conn:
        conn.execute("INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, ?, ?)", (card, due, amount, status))


def test_projects_monthly_from_the_last_due_with_median_of_last_3(db):
    c = _card(db, "Platinum", "0013")
    for due, amt in (("2026-06-08", 1_031_900), ("2026-07-08", 659_000), ("2026-08-08", 864_600), ("2026-09-08", 700_000)):
        _stmt(db, c, due, amt)
    est = estimated_card_dues(TODAY, date(2027, 2, 28), today=TODAY)
    mine = est[est["card_id"] == c]
    assert [str(d) for d in mine["due"]] == ["2026-10-08", "2026-11-08", "2026-12-08", "2027-01-08", "2027-02-08"]
    row = mine.iloc[0]
    assert row["amount"] == 700_000 and (row["low"], row["high"]) == (659_000, 864_600)   # median / min / max of the last 3
    assert row["track"] == "Credit Card (estimated)" and row["based_on"] == 3


def test_an_estimate_is_replaced_once_the_real_statement_is_entered(db):
    c = _card(db, "Visa Gold", "2005")
    _stmt(db, c, "2026-08-04", 33_100); _stmt(db, c, "2026-09-04", 41_700)
    first = estimated_card_dues(TODAY, date(2026, 12, 31), today=TODAY)
    assert str(first[first["card_id"] == c]["due"].iloc[0]) == "2026-10-04"
    _stmt(db, c, "2026-10-07", 45_400, status="Unpaid")                  # the October statement arrives
    after = estimated_card_dues(TODAY, date(2026, 12, 31), today=TODAY)
    assert [str(d) for d in after[after["card_id"] == c]["due"]] == ["2026-11-07", "2026-12-07"]   # nothing estimated for October any more


def test_no_due_months_cancelled_statements_and_inactive_cards(db):
    from engines.cc_service import mark_no_due

    c = _card(db, "Everyday", "0008", statement_day=21)
    _stmt(db, c, "2026-08-16", 10_000); _stmt(db, c, "2026-09-16", 12_000)
    _stmt(db, c, "2026-09-16", 99_999, status="Cancelled")               # a cancelled copy is ignored
    mark_no_due(c, "2026-09")                                            # the Sep 21 statement (due ~Oct 16) had nothing due
    est = estimated_card_dues(TODAY, date(2026, 12, 31), today=TODAY)
    mine = est[est["card_id"] == c]
    assert [str(d) for d in mine["due"]] == ["2026-11-16", "2026-12-16"] and mine["amount"].max() == 11_000
    gone = _card(db, "Closed", "9999", active=0)
    _stmt(db, gone, "2026-09-10", 5_000)
    assert gone not in set(estimated_card_dues(TODAY, date(2026, 12, 31), today=TODAY)["card_id"])


def test_weekly_view_splits_recorded_and_estimated(db):
    c = _card(db, "BPI Rewards", "0006")
    _stmt(db, c, "2026-08-28", 400_000); _stmt(db, c, "2026-09-28", 612_400, status="Unpaid")
    _stmt(db, c, "2026-10-28", 450_000, status="Unpaid")                 # already in: recorded, not estimated
    weeks = forecast_by_week(TODAY, months=5)
    oct_wk = weeks[weeks["week_start"] == date(2026, 10, 26)].iloc[0]
    nov_wk = weeks[weeks["week_start"] == date(2026, 11, 23)].iloc[0]
    assert (oct_wk["recorded"], oct_wk["estimated"]) == (450_000, 0)
    assert nov_wk["estimated"] == 450_000 and nov_wk["recorded"] == 0     # median of 400,000 / 612,400 / 450,000
    assert weeks["week_start"].max() <= date(2027, 2, 28)


def test_estimates_count_as_cash_going_out_but_not_as_checks(db):
    from engines import check_date_planner as cp
    from engines.cash_insights_service import scheduled_outflows

    c = _card(db, "Toyota", "0689")
    set_paid_from_bank([c])
    _stmt(db, c, "2026-08-29", 300_000); _stmt(db, c, "2026-09-29", 282_400)
    out = scheduled_outflows(TODAY, date(2026, 11, 30))
    est = out[out["track"] == "Credit Card (estimated)"]
    assert len(est) == 2 and est["amount"].iloc[0] == 291_200
    base = cp._baseline(TODAY, date(2026, 11, 30), 1_000_000, 0, set())  # typed-cash path
    i = base["days"].index(date(2026, 10, 29))
    assert base["out"][i] == 291_200 and base["checks"][i] == 0


def test_a_card_bill_a_month_away_moves_a_supplier_check(db):
    """The owner's purpose: "issuing checks to avoid large amount of payables especially when there is credit card that only month away
    to schedule" -- the card's statement is not in yet, but its estimated bill still pushes a supplier check off that day."""
    from engines import check_date_planner as cp
    from test_check_date_planner import D, MON, batch, by_doc, settings

    sup = make_creditor(db, name="Tire Supplier")
    batch(db, sup, "OS-1", [("T1", D(2), 8000.0)])                       # due Wednesday
    plan = lambda: by_doc(cp.plan(today=MON, start_cash=10_000, inflow_per_day=3_000, settings=settings()))["T1"]
    assert (plan().suggested_date, plan().status) == (D(2), "On time")   # no card bill known: pays on its due date
    c = _card(db, "Platinum", "0013")
    set_paid_from_bank([c])
    _stmt(db, c, "2029-12-09", 10_000)                                   # last month's bill -> ~PHP 10,000 again on Jan 9 (Wednesday)
    moved = plan()
    assert (moved.suggested_date, moved.status) == (D(3), "Moved") and "cash would go under" in moved.reason


def test_card_dues_for_the_weeks_of_typed_check_dates(db):
    """Owner, 2026-09-29: when typing a check date, show the card dues due that week (on statements + estimated) and flag a heavy week
    -- at least 1.5 times the average weekly card dues. Past dates are skipped; one line per week however many checks fall in it."""
    from engines.cc_forecast_service import card_week_info

    big = _card(db, "Platinum", "0013")
    set_paid_from_bank([big])
    for due, amt in (("2026-07-05", 3_000_000), ("2026-08-05", 3_000_000), ("2026-09-05", 3_000_000)):
        _stmt(db, big, due, amt)
    small = _card(db, "Visa", "0001")
    set_paid_from_bank([big, small])
    for due, amt in (("2026-07-20", 100_000), ("2026-08-20", 100_000), ("2026-09-20", 100_000)):
        _stmt(db, small, due, amt)
    info = card_week_info([date(2026, 11, 5), date(2026, 11, 6), date(2026, 11, 20), date(2026, 9, 1)], today=TODAY)
    assert [i["week_start"] for i in info] == [date(2026, 11, 2), date(2026, 11, 16)]      # Nov 5 + 6 share a week; Sep 1 is past
    heavy, light = info
    assert heavy["total"] == 3_000_000 and heavy["estimated"] == 3_000_000 and heavy["heavy"]
    assert light["total"] == 100_000 and not light["heavy"]
    assert heavy["dates"] == [date(2026, 11, 5), date(2026, 11, 6)]


def test_only_cards_paid_from_metrobank_count_against_the_bank_balance(db):
    """Owner, 2026-09-29: every card statement is paid "Cash / Online" and the Metrobank statement shows almost no card payments, so the
    planner and the Cash forecast plan against the Metrobank account only: a card counts only when marked 'paid from Metrobank'.
    Nothing is marked to start. The rest are reported as paid outside the bank (still shown in the card forecast)."""
    from engines.cash_insights_service import outside_card_dues, scheduled_outflows

    inside = _card(db, "Bank Card", "1111")
    outside = _card(db, "Cash Card", "2222")
    for card, base in ((inside, 100_000), (outside, 400_000)):
        _stmt(db, card, "2026-08-29", base); _stmt(db, card, "2026-09-29", base)
        _stmt(db, card, "2026-10-20", base * 2, status="Unpaid")             # a recorded, unpaid October bill
    assert paid_from_bank_ids() == set()

    def cards_out():
        out = scheduled_outflows(TODAY, date(2026, 12, 31))
        return out[out["track"].isin(["Credit Card", "Credit Card (estimated)"])]

    assert cards_out().empty                                                  # nothing marked: no card is deducted from the bank
    gone = outside_card_dues(TODAY, date(2026, 12, 31))
    assert gone["recorded"] == 1_000_000 and gone["estimated"] > 0 and gone["cards"] == 2   # 200,000 + 800,000 October bills

    set_paid_from_bank([inside])
    now = cards_out()
    assert now[now["track"] == "Credit Card"]["amount"].sum() == 200_000     # the recorded October bill of the marked card only
    assert (now["track"] == "Credit Card (estimated)").any()
    after = outside_card_dues(TODAY, date(2026, 12, 31))
    assert after["recorded"] == 800_000 and after["cards"] == 1               # the other card is now the only one outside


def test_the_marked_cards_are_remembered_and_audited(db):
    a, b = _card(db, "A", "0001"), _card(db, "B", "0002")
    set_paid_from_bank([a])
    assert paid_from_bank_ids() == {a}
    set_paid_from_bank([a, b])
    set_paid_from_bank([])
    assert paid_from_bank_ids() == set()
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CC_BANK_CARDS_SET'").fetchone()[0] == 3


def test_the_typed_cash_planner_path_also_skips_cards_paid_outside(db):
    from engines import check_date_planner as cp

    c = _card(db, "Outside", "3333")
    _stmt(db, c, "2026-08-29", 300_000); _stmt(db, c, "2026-09-29", 300_000)
    _stmt(db, c, "2026-10-20", 300_000, status="Unpaid")
    base = cp._baseline(TODAY, date(2026, 11, 30), 1_000_000, 0, set())
    assert sum(base["out"]) == 0                                              # a card paid outside the bank takes nothing from the balance
    set_paid_from_bank([c])
    base = cp._baseline(TODAY, date(2026, 11, 30), 1_000_000, 0, set())
    assert sum(base["out"]) > 300_000                                         # the recorded bill + the estimates


def test_weekly_view_and_heavy_week_note_follow_the_metrobank_cards(db):
    from engines.cc_forecast_service import card_week_info

    inside, outside = _card(db, "Inside", "4444"), _card(db, "Outside", "5555")
    for card, amt in ((inside, 100_000), (outside, 2_000_000)):
        for due in ("2026-07-05", "2026-08-05", "2026-09-05"):
            _stmt(db, card, due, amt)
    allc = forecast_by_week(TODAY, months=5)
    nov_all = allc[allc["week_start"] == date(2026, 11, 2)].iloc[0]
    assert nov_all["estimated"] == 2_100_000                                    # the planner page still shows every card
    assert forecast_by_week(TODAY, months=5, bank_only=True).empty              # ... but none is marked, so nothing counts against Metrobank
    assert card_week_info([date(2026, 11, 5)], today=TODAY) == [] or card_week_info([date(2026, 11, 5)], today=TODAY)[0]["total"] == 0
    set_paid_from_bank([inside])
    bank = forecast_by_week(TODAY, months=5, bank_only=True)
    assert bank[bank["week_start"] == date(2026, 11, 2)].iloc[0]["estimated"] == 100_000
    assert card_week_info([date(2026, 11, 5)], today=TODAY)[0]["total"] == 100_000
