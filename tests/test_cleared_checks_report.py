"""Cleared checks report (owner, 2026-09-29: "create additional standard report for cleared checks from bank recon"): every check the bank has
cleared, from the bank reconciliation -- with days to clear, totals by payee and by week, how it was matched, and any difference between
the bank's amount and our record. Same made-up statement as the reconciliation tests."""
import sqlite3
from datetime import date

from conftest import make_creditor
from engines.bank_statement_service import adjust_check_to_bank, exceptions, import_statement
from engines.cleared_checks_report import by_payee, by_week, cleared_checks, cleared_span
from test_bank_statement_import import _checks, _statement


def _setup(db, monkeypatch, tmp_path):
    monkeypatch.setenv("AP_DRIVE_DIR", str(tmp_path / "drive"))
    ids = _checks(db)                                    # A matched Oct 5, B matched Oct 5, C amount differs, D cancelled but cleared Oct 6
    import_statement(_statement(), "s.csv")
    return ids


def test_lists_what_the_bank_cleared_with_days_to_clear(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    df = cleared_checks()
    by_no = df.set_index("check_number")
    assert set(by_no.index) == {"3992000001", "2000002", "3992000004"}                 # C was not cleared for its amount; E never cleared
    a = by_no.loc["3992000001"]
    assert (str(a["cleared_date"]), a["amount"], str(a["check_date"])) == ("2026-10-05", 1000.0, "2026-10-01")
    assert a["days_to_clear"] == 4 and a["matched_how"] == "Matched automatically" and a["payee"] == "ACME Tires" and a["difference"] == 0
    assert by_no.loc["2000002", "days_to_clear"] == 3
    assert by_no.loc["3992000004", "status"] == "Cancelled"                            # cleared although we cancelled it: shown, with its status
    assert cleared_span() == (date(2026, 10, 5), date(2026, 10, 6))


def test_a_check_adjusted_to_the_bank_shows_the_difference(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE checks_issued SET printed_at = CURRENT_TIMESTAMP, check_date = '2026-10-01' WHERE check_id = ?", (ids["C"],))
    txn = next(e["txn_id"] for e in exceptions() if e["match_status"] == "amount_mismatch")
    assert adjust_check_to_bank(txn)[0]
    row = cleared_checks().set_index("check_number").loc["3992000003"]
    assert row["matched_how"] == "Adjusted to the bank" and row["amount"] == 1200.0 and row["bank_amount"] == 1250.0 and row["difference"] == 50.0
    assert row["days_to_clear"] == 5                                                   # Oct 1 -> Oct 6


def test_filters_and_summaries(db, monkeypatch, tmp_path):
    ids = _setup(db, monkeypatch, tmp_path)
    assert set(cleared_checks(date_from=date(2026, 10, 6))["check_number"]) == {"3992000004"}
    assert set(cleared_checks(date_to=date(2026, 10, 5))["check_number"]) == {"3992000001", "2000002"}
    other = make_creditor(db, name="Other Supplier")
    assert cleared_checks(creditor_id=other).empty
    df = cleared_checks()
    pay = by_payee(df)
    assert list(pay["payee"]) == ["ACME Tires"] and pay["checks"].iloc[0] == 3 and pay["total"].iloc[0] == 1000 + 1100 + 900
    assert round(pay["avg_days"].iloc[0], 2) == round((4 + 3 + 3) / 3, 2)
    wk = by_week(df)
    assert [str(d) for d in wk["week_start"]] == ["2026-10-05"] and wk["checks"].iloc[0] == 3        # Oct 5-6 2026 are Monday and Tuesday
    assert by_payee(df.iloc[0:0]).empty and by_week(df.iloc[0:0]).empty
