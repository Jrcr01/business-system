"""Settled outside the bank (owner, 2026-09-30: "checks not yet cleared -- have an option to solve, like when the check is encashed to us instead
of the bank"). A check the payee gave back and was paid in cash stops being money still leaving the bank: it leaves the not-yet-cleared list, the
cash forecast, the float, the stale-check count and the payment schedule -- but is NEVER marked cleared by the bank (the bank statement stays the
truth) and keeps counting toward its batch. It is audited and can be undone."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import fetchone
from engines.bank_statement_service import import_statement, link_candidates, not_yet_cleared
from engines.cash_insights_service import float_summary, scheduled_outflows
from engines.dashboard_kpi_service import get_stale_checks
from engines.reports_service import get_payment_schedule_all_tracks
from engines.settled_outside_bank import REASONS, settle_outside_bank, settled_checks, undo_settlement
from test_bank_statement_import import _checks, _statement

TODAY = date.today()                                        # the settlement date can never be in the future


def _setup(db, monkeypatch, tmp_path):
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    ids = _checks(db)                  # A, B cleared by the bank; C amount differs; D cancelled; E (PHP 800, Oct 3) not cleared
    import_statement(_statement(), "s.csv")
    return ids


def test_a_settled_check_leaves_the_not_yet_cleared_list_and_comes_back_on_undo(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    assert ids["E"] in {w["check_id"] for w in not_yet_cleared()}

    ok, msg = settle_outside_bank(ids["E"], "Paid in cash", TODAY, "payee returned the check")

    assert ok, msg
    assert ids["E"] not in {w["check_id"] for w in not_yet_cleared()}
    row = fetchone(db, "SELECT status, bank_cleared_date, settled_outside_at, settled_outside_reason, settled_outside_note FROM checks_issued WHERE check_id = ?", (ids["E"],))
    assert row == ("Active", None, str(TODAY), "Paid in cash", "payee returned the check")      # NOT marked cleared by the bank
    assert "Paid in cash" in fetchone(db, "SELECT new_values FROM audit_logs WHERE user_action = 'CHECK_SETTLED_OUTSIDE'")[0]

    assert undo_settlement(ids["E"])[0]
    assert ids["E"] in {w["check_id"] for w in not_yet_cleared()}
    assert fetchone(db, "SELECT settled_outside_at FROM checks_issued WHERE check_id = ?", (ids["E"],))[0] is None
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'CHECK_SETTLEMENT_UNDONE'")[0] == 1


def test_a_settled_check_is_no_longer_a_cash_forecast_outflow_or_float(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    start = date(2026, 10, 8)
    assert 800.0 in set(scheduled_outflows(start, start + timedelta(days=10))["amount"])
    assert 800.0 in set(float_summary()["overdue"]["amount"])

    settle_outside_bank(ids["E"], "Paid in cash", TODAY)

    assert 800.0 not in set(scheduled_outflows(start, start + timedelta(days=10))["amount"])
    assert 800.0 not in set(float_summary()["overdue"]["amount"])
    assert ids["E"] not in {c["check_id"] for c in link_candidates(fetchone(db, "SELECT txn_id FROM bank_transactions WHERE check_number = '3991234567'")[0])}


def test_a_settled_check_is_not_stale(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET check_date = '2025-01-01' WHERE check_id = ?", (ids["E"],))
    assert get_stale_checks(180)["n"] == 1

    settle_outside_bank(ids["E"], "Paid in cash", TODAY)

    assert get_stale_checks(180)["n"] == 0


def test_a_settled_future_check_is_no_longer_scheduled_to_be_paid(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    future = date.today() + timedelta(days=30)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET check_date = ? WHERE check_id = ?", (str(future), ids["E"]))
    assert 800.0 in set(get_payment_schedule_all_tracks()["amount"])

    settle_outside_bank(ids["E"], "Check returned and exchanged", TODAY)

    assert 800.0 not in set(get_payment_schedule_all_tracks()["amount"])


def test_refusals(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    today = TODAY

    ok, msg = settle_outside_bank(ids["A"], "Paid in cash", today)
    assert not ok and "bank" in msg.lower()                         # the bank already cleared it
    ok, msg = settle_outside_bank(ids["D"], "Paid in cash", today)
    assert not ok and "Active" in msg                               # cancelled
    ok, msg = settle_outside_bank(ids["E"], "Because", today)
    assert not ok and "reason" in msg.lower()
    ok, msg = settle_outside_bank(ids["E"], "Paid in cash", date.today() + timedelta(days=3))
    assert not ok and "future" in msg.lower()
    assert not settle_outside_bank(999999, "Paid in cash", today)[0]
    assert settle_outside_bank(ids["E"], "Paid in cash", today)[0]
    ok, msg = settle_outside_bank(ids["E"], "Paid in cash", today)
    assert not ok and "already" in msg.lower()
    assert not undo_settlement(ids["A"])[0]                         # nothing to undo


def test_the_reasons_offered(db):
    assert REASONS == ["Paid in cash", "Check returned and exchanged", "Other"]


def test_the_list_flags_a_settled_check_the_bank_paid_as_well(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    settle_outside_bank(ids["E"], "Paid in cash", TODAY, "returned")

    df = settled_checks()
    assert list(df["check_number"]) == ["3992000005"] and df["bank_also_cleared"].iloc[0] == ""
    assert df["amount"].iloc[0] == 800.0 and df["reason"].iloc[0] == "Paid in cash" and df["payee"].iloc[0] == "ACME Tires"

    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET bank_cleared_date = '2026-10-20' WHERE check_id = ?", (ids["E"],))      # the bank paid it too!
    assert "2026-10-20" in settled_checks()["bank_also_cleared"].iloc[0]
