"""Analytics tab (2026-09-17) -- AP composition/trend, payment behavior, and cost-of-debt
queries. Exercises the honesty properties that matter most: a facility with no rate on file
must not appear at all (not as a fabricated 0%), and check status/aging must use the app's
EFFECTIVE status convention (a postdated check that reached its date reads as "Cleared" even
though checks_issued.status itself never actually changes)."""
import sqlite3
from datetime import date, timedelta

from conftest import fetchone, make_creditor, make_statement
from engines import analytics_service


def _make_check(db_path, creditor_id, check_date, amount, status="Active", statement_id=None, loan_note_id=None):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, loan_note_id, check_number, check_date, amount, status) "
        "VALUES (?, 1, ?, ?, ?, ?, ?, ?)",
        (creditor_id, statement_id, loan_note_id, f"CHK-{check_date}-{amount}", str(check_date), amount, status),
    )
    conn.commit()
    check_id = cur.lastrowid
    conn.close()
    return check_id


def _make_statement(db_path, creditor_id, statement_ref):
    """conftest.make_statement() hardcodes a single 'TEST-REF' ref, so it can't be called twice
    in the same test (UNIQUE constraint) -- this variant takes its own ref for tests needing
    more than one statement."""
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, ?, 0, 'Draft')",
        (creditor_id, statement_ref),
    )
    conn.commit()
    statement_id = cur.lastrowid
    conn.close()
    return statement_id


def _make_loan_note(db_path, creditor_id, monthly_interest_rate=None, note_ref="LN-1", status="Active"):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO loan_notes (creditor_id, note_ref, issue_date, monthly_interest_rate, status) VALUES (?, ?, '2026-01-01', ?, ?)",
        (creditor_id, note_ref, monthly_interest_rate, status),
    )
    loan_note_id = cur.lastrowid
    conn.commit()
    conn.close()
    return loan_note_id


def test_ap_composition_by_track_splits_supplier_and_lender(db):
    supplier_id = make_creditor(db, name="ACME Tires", creditor_type="SUPPLIER")
    statement_id = make_statement(db, supplier_id)
    _make_check(db, supplier_id, date.today() + timedelta(days=30), 10000.0, statement_id=statement_id)

    lender_id = make_creditor(db, name="Juan Dela Cruz", creditor_type="LENDER")
    loan_note_id = _make_loan_note(db, lender_id)
    _make_check(db, lender_id, date.today() + timedelta(days=30), 5000.0, loan_note_id=loan_note_id)

    out = analytics_service.get_ap_composition_by_track("month")
    tracks = dict(zip(out["track"], out["amount"]))
    assert tracks["Supplier"] == 10000.0
    assert tracks["Lender"] == 5000.0


def test_ap_composition_end_date_excludes_far_future_tail(db):
    """A bank loan installment years out shouldn't drag the composition/growth charts across
    mostly-empty periods by default -- end_date bounds the window, same fix applied to
    get_ap_growth_rate() and get_payables_trend()."""
    supplier_id = make_creditor(db, name="ACME Tires", creditor_type="SUPPLIER")
    statement_id = make_statement(db, supplier_id)
    _make_check(db, supplier_id, date.today() + timedelta(days=30), 10000.0, statement_id=statement_id)

    bank_id = make_creditor(db, name="BDO", creditor_type="BANK_LOAN")
    conn = sqlite3.connect(db)
    cur = conn.execute(
        "INSERT INTO bank_loans (creditor_id, loan_ref, status) VALUES (?, 'PENDING-1', 'Active')", (bank_id,),
    )
    bank_loan_id = cur.lastrowid
    conn.execute(
        "INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (?, 1, '2031-05-21', 99999.0)",
        (bank_loan_id,),
    )
    conn.commit()
    conn.close()

    out_unbounded = analytics_service.get_ap_composition_by_track("month")
    assert "Bank Loan" in set(out_unbounded["track"])

    out_bounded = analytics_service.get_ap_composition_by_track("month", end_date=date(2026, 12, 31))
    assert "Bank Loan" not in set(out_bounded["track"])
    assert set(out_bounded["track"]) == {"Supplier"}


def test_ap_growth_rate_first_period_nan_second_period_computed():
    """No db-backed data needed -- exercises the pct_change math directly against a fixed
    two-row trend, independent of get_payables_trend()'s own query correctness (covered by
    its own existing tests)."""
    import pandas as pd
    from unittest.mock import patch

    fake_trend = pd.DataFrame({"period": ["Aug 2026", "Sep 2026"], "total_amount": [100000.0, 150000.0], "moving_avg": [100000.0, 125000.0]})
    with patch.object(analytics_service, "get_payables_trend", return_value=fake_trend):
        out = analytics_service.get_ap_growth_rate("month")
    assert pd.isna(out["pct_change"].iloc[0])
    assert out["pct_change"].iloc[1] == 50.0


def test_supplier_pareto_cumulative_reaches_true_grand_total(db):
    supplier_a = make_creditor(db, name="Big Supplier", creditor_type="SUPPLIER")
    statement_a = _make_statement(db, supplier_a, "REF-A")
    _make_check(db, supplier_a, date.today() + timedelta(days=30), 80000.0, statement_id=statement_a)

    supplier_b = make_creditor(db, name="Small Supplier", creditor_type="SUPPLIER")
    statement_b = _make_statement(db, supplier_b, "REF-B")
    _make_check(db, supplier_b, date.today() + timedelta(days=30), 20000.0, statement_id=statement_b)

    out = analytics_service.get_supplier_pareto(limit=10)
    assert len(out) == 2
    assert out["supplier_name"].iloc[0] == "Big Supplier"
    assert out["cumulative_pct"].iloc[0] == 80.0
    assert out["cumulative_pct"].iloc[1] == 100.0


def test_lender_pareto_cumulative_reaches_true_grand_total(db):
    lender_a = make_creditor(db, name="Big Lender", creditor_type="LENDER")
    note_a = _make_loan_note(db, lender_a, note_ref="LN-A")
    _make_check(db, lender_a, date.today() + timedelta(days=30), 60000.0, loan_note_id=note_a)

    lender_b = make_creditor(db, name="Small Lender", creditor_type="LENDER")
    note_b = _make_loan_note(db, lender_b, note_ref="LN-B")
    _make_check(db, lender_b, date.today() + timedelta(days=30), 40000.0, loan_note_id=note_b)

    out = analytics_service.get_lender_pareto(limit=10)
    assert len(out) == 2
    assert out["name"].iloc[0] == "Big Lender"
    assert out["cumulative_pct"].iloc[0] == 60.0
    assert out["cumulative_pct"].iloc[1] == 100.0


def test_bank_loan_pareto_disambiguates_same_bank_by_loan_ref(db):
    bank_id = make_creditor(db, name="BDO", creditor_type="BANK_LOAN")
    conn = sqlite3.connect(db)
    cur1 = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref, status) VALUES (?, 'PENDING-1', 'Active')", (bank_id,))
    loan1 = cur1.lastrowid
    cur2 = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref, status) VALUES (?, 'PENDING-2', 'Active')", (bank_id,))
    loan2 = cur2.lastrowid
    conn.execute("INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (?, 1, '2026-10-15', 70000.0)", (loan1,))
    conn.execute("INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (?, 1, '2026-10-15', 30000.0)", (loan2,))
    conn.commit()
    conn.close()

    out = analytics_service.get_bank_loan_pareto(limit=10)
    assert len(out) == 2
    assert out["name"].iloc[0] == "BDO — PENDING-1"
    assert out["name"].iloc[1] == "BDO — PENDING-2"
    assert out["cumulative_pct"].iloc[0] == 70.0
    assert out["cumulative_pct"].iloc[1] == 100.0


def test_all_tracks_pareto_ranks_across_tracks_with_true_combined_total(db):
    supplier_id = make_creditor(db, name="Big Supplier", creditor_type="SUPPLIER")
    statement_id = _make_statement(db, supplier_id, "REF-SUP")
    _make_check(db, supplier_id, date.today() + timedelta(days=30), 70000.0, statement_id=statement_id)

    lender_id = make_creditor(db, name="Small Lender", creditor_type="LENDER")
    note_id = _make_loan_note(db, lender_id, note_ref="LN-1")
    _make_check(db, lender_id, date.today() + timedelta(days=30), 30000.0, loan_note_id=note_id)

    out = analytics_service.get_all_tracks_pareto(limit=10)
    assert len(out) == 2
    assert out["name"].iloc[0] == "Big Supplier"
    assert out["track"].iloc[0] == "Supplier"
    assert out["cumulative_pct"].iloc[0] == 70.0
    assert out["name"].iloc[1] == "Small Lender"
    assert out["track"].iloc[1] == "Lender"
    assert out["cumulative_pct"].iloc[1] == 100.0


def _make_cc_statement(db_path, card_id, due_date, total_amount_due):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO cc_statements (card_id, due_date, total_amount_due, status) VALUES (?, ?, ?, 'Unpaid')",
        (card_id, str(due_date), total_amount_due),
    )
    conn.commit()
    conn.close()


def test_credit_card_pareto_empty_when_no_statements(db):
    """The common real case right now -- every migrated card has no statement history."""
    assert analytics_service.get_credit_card_pareto(limit=10).empty


def test_credit_card_pareto_ranks_by_real_unpaid_balance(db):
    issuer_id = make_creditor(db, name="BPI", creditor_type="CREDIT_CARD")
    conn = sqlite3.connect(db)
    cur1 = conn.execute(
        "INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Gold', '1111', 100000)",
        (issuer_id,),
    )
    big_card = cur1.lastrowid
    cur2 = conn.execute(
        "INSERT INTO credit_cards (creditor_id, card_name, card_number_last4, credit_limit) VALUES (?, 'Classic', '2222', 50000)",
        (issuer_id,),
    )
    small_card = cur2.lastrowid
    conn.commit()
    conn.close()

    future = date.today() + timedelta(days=10)
    _make_cc_statement(db, big_card, future, 80000.0)
    _make_cc_statement(db, small_card, future, 20000.0)

    out = analytics_service.get_credit_card_pareto(limit=10)
    assert len(out) == 2
    assert out["name"].iloc[0] == "Gold - 1111"
    assert out["cumulative_pct"].iloc[0] == 80.0
    assert out["cumulative_pct"].iloc[1] == 100.0


def test_check_status_breakdown_uses_effective_not_recorded_status(db):
    supplier_id = make_creditor(db, name="ACME Tires", creditor_type="SUPPLIER")
    statement_id = make_statement(db, supplier_id)
    # Still Active and genuinely in the future.
    _make_check(db, supplier_id, date.today() + timedelta(days=30), 1000.0, status="Active", statement_id=statement_id)
    # Recorded as Active, but its date already passed -- effectively Cleared.
    _make_check(db, supplier_id, date.today() - timedelta(days=5), 2000.0, status="Active", statement_id=statement_id)
    # A genuine Cancelled row.
    _make_check(db, supplier_id, date.today() + timedelta(days=10), 500.0, status="Cancelled", statement_id=statement_id)

    out = analytics_service.get_check_status_breakdown()
    by_status = dict(zip(out["effective_status"], out["total"]))
    assert by_status["Active"] == 1000.0
    assert by_status["Cleared"] == 2000.0
    assert by_status["Cancelled"] == 500.0


def test_check_aging_distribution_buckets_by_days_until_due(db):
    supplier_id = make_creditor(db, name="ACME Tires", creditor_type="SUPPLIER")
    statement_id = make_statement(db, supplier_id)
    _make_check(db, supplier_id, date.today() + timedelta(days=3), 1000.0, statement_id=statement_id)
    _make_check(db, supplier_id, date.today() + timedelta(days=45), 2000.0, statement_id=statement_id)

    out = analytics_service.get_check_aging_distribution()
    by_bucket = dict(zip(out["bucket"], out["amount"]))
    assert by_bucket["Due in 0-7 days"] == 1000.0
    assert by_bucket["Due in 31-60 days"] == 2000.0


def test_debt_cost_comparison_excludes_facilities_with_no_rate_on_file(db):
    lender_id = make_creditor(db, name="Pending Lender", creditor_type="LENDER")
    _make_loan_note(db, lender_id, monthly_interest_rate=None, status="Pending Setup")

    out = analytics_service.get_debt_cost_comparison()
    assert out.empty


def test_debt_cost_comparison_includes_private_loan_with_real_rate(db):
    lender_id = make_creditor(db, name="Active Lender", creditor_type="LENDER")
    _make_loan_note(db, lender_id, monthly_interest_rate=2.5, note_ref="LN-REAL")

    out = analytics_service.get_debt_cost_comparison()
    assert len(out) == 1
    row = out.iloc[0]
    assert row["source"] == "Private Loan"
    assert row["monthly_rate_pct"] == 2.5
    assert row["annualized_rate_pct"] == 30.0


def test_missing_debt_terms_ranks_by_real_exposure_excludes_zero_balance(db):
    big_lender = make_creditor(db, name="Big Lender", creditor_type="LENDER")
    big_note = _make_loan_note(db, big_lender, monthly_interest_rate=None, note_ref="LN-BIG")
    _make_check(db, big_lender, date(2026, 10, 1), 90000.0, loan_note_id=big_note)

    small_lender = make_creditor(db, name="Small Lender", creditor_type="LENDER")
    small_note = _make_loan_note(db, small_lender, monthly_interest_rate=None, note_ref="LN-SMALL")
    _make_check(db, small_lender, date(2026, 10, 1), 10000.0, loan_note_id=small_note)

    # A lender with a real rate already on file shouldn't show up as "missing."
    rated_lender = make_creditor(db, name="Rated Lender", creditor_type="LENDER")
    rated_note = _make_loan_note(db, rated_lender, monthly_interest_rate=2.0, note_ref="LN-RATED")
    _make_check(db, rated_lender, date(2026, 10, 1), 50000.0, loan_note_id=rated_note)

    # A lender with no rate but zero outstanding shouldn't show up either -- nothing to prioritize.
    make_creditor(db, name="Idle Lender", creditor_type="LENDER")

    out = analytics_service.get_missing_debt_terms(limit=10)
    names = list(out["name"])
    assert names == ["Big Lender", "Small Lender"]
    assert out["outstanding"].iloc[0] == 90000.0
