"""A loan note's whole schedule in one go, and every pending installment's post-dated check in one go (2026-10-03): about 80 clicks for a 12-month note before."""
from datetime import date

from conftest import fetchone, make_creditor
from engines.acting_user import acting_as
from engines.loan_note_rules import create_loan_note
from engines.loan_schedule_service import generate_schedule, issue_all_pdcs, preview_schedule


def _note(db, principal=120000.0, rate=2.0):
    cid = make_creditor(db, name="Richie", creditor_type="LENDER", terms="0")
    ok, msg, lnid = create_loan_note(cid, date(2026, 10, 1), principal, rate)
    assert ok, msg
    return cid, lnid


def test_the_preview_splits_the_principal_equally_and_charges_interest_on_the_balance_left(db):
    cid, lnid = _note(db)
    rows = preview_schedule(lnid, date(2026, 11, 1), 12)
    assert len(rows) == 12 and rows[0]["due_date"] == date(2026, 11, 1) and rows[11]["due_date"] == date(2027, 10, 1)
    assert [r["principal"] for r in rows] == [10000.0] * 12 and rows[-1]["balance"] == 0.0
    assert rows[0]["interest"] == 2400.0 and rows[1]["interest"] == 2200.0                  # 2% of 120,000, then of 110,000
    assert preview_schedule(lnid, date(2026, 11, 1), 0) == []




def test_generating_saves_the_rows_once_and_refuses_a_second_time(db):
    cid, lnid = _note(db, principal=100000.0, rate=0.0)
    ok, msg = generate_schedule(lnid, date(2026, 11, 30), 3)
    assert ok and fetchone(db, "SELECT COUNT(*) FROM loan_installments WHERE loan_note_id = ?", (lnid,))[0] == 3
    dues = [r[0] for r in __import__("sqlite3").connect(db).execute("SELECT due_date FROM loan_installments ORDER BY due_date")]
    assert dues == ["2026-11-30", "2026-12-30", "2027-01-30"]                                  # the day is kept; a short month gives its last day
    assert round(fetchone(db, "SELECT SUM(principal) FROM loan_installments")[0], 2) == 100000.0
    assert not generate_schedule(lnid, date(2026, 11, 30), 3)[0]


def test_the_last_installment_takes_the_odd_centavos(db):
    cid, lnid = _note(db, principal=100000.0, rate=0.0)
    rows = preview_schedule(lnid, date(2026, 11, 1), 3)
    assert [r["principal"] for r in rows] == [33333.33, 33333.33, 33333.34]


def test_one_press_parks_a_check_for_every_pending_installment(db):
    cid, lnid = _note(db, principal=30000.0, rate=0.0)
    generate_schedule(lnid, date(2026, 11, 1), 3)
    with acting_as("clerk@example.com", "payables"):
        ok, msg, n = issue_all_pdcs(lnid)
    assert ok and n == 3, msg
    checks = __import__("sqlite3").connect(db).execute("SELECT check_date, amount FROM checks_issued WHERE loan_note_id = ? ORDER BY check_date", (lnid,)).fetchall()
    assert checks == [("2026-11-01", 10000.0), ("2026-12-01", 10000.0), ("2027-01-01", 10000.0)]
    with acting_as("clerk@example.com", "payables"):
        assert not issue_all_pdcs(lnid)[0]                                                    # nothing left to park


def test_a_role_that_may_not_park_checks_gets_a_refusal_and_nothing_is_parked(db):
    cid, lnid = _note(db, principal=30000.0, rate=0.0)
    generate_schedule(lnid, date(2026, 11, 1), 3)
    with acting_as("buyer@example.com", "purchasing"):
        ok, msg, n = issue_all_pdcs(lnid)
    assert not ok and n == 0 and fetchone(db, "SELECT COUNT(*) FROM checks_issued")[0] == 0


def test_a_bank_loan_gets_equal_monthly_rows_once(db):
    import sqlite3
    from engines.bank_loan_service import fill_flat_schedule
    with sqlite3.connect(db) as conn:
        bank = conn.execute("INSERT INTO creditors (creditor_name, creditor_type, terms) VALUES ('Metrobank', 'BANK_LOAN', '0')").lastrowid
        blid = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref) VALUES (?, 'BL-9')", (bank,)).lastrowid
    ok, msg = fill_flat_schedule(blid, date(2026, 11, 30), 3, 25000.0)
    assert ok, msg
    rows = sqlite3.connect(db).execute("SELECT installment_no, due_date, amount FROM bank_loan_installments ORDER BY installment_no").fetchall()
    assert rows == [(1, "2026-11-30", 25000.0), (2, "2026-12-30", 25000.0), (3, "2027-01-30", 25000.0)]
    assert not fill_flat_schedule(blid, date(2026, 11, 30), 3, 25000.0)[0] and not fill_flat_schedule(blid + 1, date(2026, 11, 30), 0, 0)[0]


# --- the loan note generator: the owner's sheet (2026-10-03) ------------------------------------------------------------------------------------------------------------

def test_the_owners_example_loan_note_comes_out_exactly(db):
    from engines.loan_schedule_service import build_schedule, interest_only_months, loan_sheet
    rows = build_schedule(200000.0, 3.0, date(2026, 10, 1), date(2027, 1, 1), 4)
    assert interest_only_months(date(2026, 10, 1), date(2027, 1, 1)) == 2
    assert [(r["due_date"], r["principal"], r["interest"], r["balance"]) for r in rows] == [
        (date(2026, 11, 1), 0.0, 6000.0, 200000.0), (date(2026, 12, 1), 0.0, 6000.0, 200000.0), (date(2027, 1, 1), 50000.0, 6000.0, 150000.0),
        (date(2027, 2, 1), 50000.0, 4500.0, 100000.0), (date(2027, 3, 1), 50000.0, 3000.0, 50000.0), (date(2027, 4, 1), 50000.0, 1500.0, 0.0)]
    s = loan_sheet("LN 31", date(2026, 10, 1), 200000.0, 3.0, rows)
    assert s["loan_rows"][0] == {"date": date(2026, 10, 1), "principal": None, "interest": None, "balance": 200000.0}      # the opening row
    assert [p["amount"] for p in s["payment_rows"]] == [6000.0, 6000.0, 56000.0, 54500.0, 53000.0, 51500.0] and [p["no"] for p in s["payment_rows"]] == [1, 2, 3, 4, 5, 6]
    assert s["loan_total"] == 227000.0 and s["payment_total"] == 227000.0 and s["difference"] == 0.0 and s["header"]["monthly_rate"] == 3.0


def test_starting_the_principal_at_the_first_payment_means_no_interest_only_months(db):
    from engines.loan_schedule_service import build_schedule, interest_only_months
    assert interest_only_months(date(2026, 10, 1), date(2026, 11, 1)) == 0 and interest_only_months(date(2026, 10, 1), date(2026, 10, 1)) == 0
    assert interest_only_months(date(2026, 10, 1), date(2027, 1, 15)) == 3                            # the first payment on or after Jan 15 is Feb 1: Nov, Dec, Jan are interest only
    rows = build_schedule(30000.0, 1.0, date(2026, 10, 1), date(2026, 11, 1), 3)
    assert [r["principal"] for r in rows] == [10000.0] * 3 and rows[0]["interest"] == 300.0


def test_the_generator_refuses_what_cannot_make_a_schedule(db):
    import pytest
    from engines.loan_schedule_service import build_schedule
    for args in ((0, 3, date(2026, 10, 1), date(2027, 1, 1), 4), (1000, None, date(2026, 10, 1), date(2027, 1, 1), 4), (1000, 3, date(2026, 10, 1), date(2027, 1, 1), 0),
                 (1000, 3, date(2026, 10, 1), date(2027, 1, 1), 121), (1000, 3, date(2026, 10, 1), date(2026, 9, 1), 4), (1000, 3, date(2026, 10, 1), date(2035, 1, 1), 4)):
        with pytest.raises(ValueError):
            build_schedule(*args)


def test_a_saved_note_shows_its_check_numbers_in_the_payment_schedule_and_a_changed_check_shows_as_a_difference(db):
    from engines.check_run_service import mark_checks_printed
    from engines.check_rules import assign_check_numbers_manual, numbers_in_sequence
    from engines.loan_schedule_service import loan_note_sheet
    cid = make_creditor(db, name="Sheet Lender", creditor_type="LENDER", terms="0")
    with acting_as("clerk@example.com", "payables"):
        from engines.loan_schedule_service import create_note_with_schedule
        ok, msg, lnid = create_note_with_schedule(cid, date(2026, 10, 1), 200000.0, 3.0, date(2027, 1, 1), 4)
        assert ok, msg
        assert issue_all_pdcs(lnid)[0]
    ids = [r[0] for r in __import__("sqlite3").connect(db).execute("SELECT check_id FROM checks_issued WHERE loan_note_id = ? ORDER BY check_date", (lnid,))]
    assert len(ids) == 6
    s = loan_note_sheet(lnid)
    assert [p["check_number"] for p in s["payment_rows"]] == [""] * 6 and s["difference"] == 0.0                 # checks parked, no numbers yet
    with acting_as("owner@example.com", "owner"):
        mark_checks_printed(ids)
        assert assign_check_numbers_manual(dict(zip(ids, numbers_in_sequence("3992000801", 6))), None)[0]
    s = loan_note_sheet(lnid)
    assert [p["check_number"] for p in s["payment_rows"]] == [str(3992000801 + i) for i in range(6)]
    con = __import__("sqlite3").connect(db)
    con.execute("UPDATE checks_issued SET amount = 55000 WHERE check_id = ?", (ids[2],))
    con.commit()
    con.close()
    s = loan_note_sheet(lnid)
    assert s["payment_rows"][2]["amount"] == 55000.0 and s["difference"] == 1000.0                                  # the check is 1,000 short of the schedule


def test_create_note_with_schedule_makes_the_note_the_number_and_the_schedule_together(db):
    from engines.loan_schedule_service import create_note_with_schedule
    cid = make_creditor(db, name="Gen Lender", creditor_type="LENDER", terms="0")
    with acting_as("clerk@example.com", "payables"):
        ok, msg, lnid = create_note_with_schedule(cid, date(2026, 10, 1), 200000.0, 3.0, date(2027, 1, 1), 4)
    assert ok, msg
    ref, total_payable, status = fetchone(db, "SELECT note_ref, total_payable, status FROM loan_notes WHERE loan_note_id = ?", (lnid,))
    assert " - LN " in ref and total_payable == 227000.0 and status == "Active"
    assert fetchone(db, "SELECT COUNT(*) FROM loan_installments WHERE loan_note_id = ?", (lnid,))[0] == 6


def test_a_note_is_not_left_behind_when_the_schedule_cannot_be_made(db):
    from engines.loan_schedule_service import create_note_with_schedule
    cid = make_creditor(db, name="Bad Lender", creditor_type="LENDER", terms="0")
    with acting_as("clerk@example.com", "payables"):
        ok, msg, lnid = create_note_with_schedule(cid, date(2026, 10, 1), 200000.0, 3.0, date(2027, 1, 1), 0)             # no principal payments
    assert not ok and lnid is None and fetchone(db, "SELECT COUNT(*) FROM loan_notes")[0] == 0
    with acting_as("buyer@example.com", "purchasing"):
        ok, msg, lnid = create_note_with_schedule(cid, date(2026, 10, 1), 1000.0, 3.0, date(2026, 11, 1), 2)
    assert fetchone(db, "SELECT COUNT(*) FROM loan_notes WHERE status != 'Cancelled'")[0] == 0 or not ok


def test_the_loan_note_pdf_is_laid_out_like_the_owners_sheet(db):
    import fitz
    from engines.loan_note_service import generate_loan_note_pdf
    from engines.loan_schedule_service import create_note_with_schedule
    cid = make_creditor(db, name="Print Lender", creditor_type="LENDER", terms="0")
    with acting_as("clerk@example.com", "payables"):
        ok, msg, lnid = create_note_with_schedule(cid, date(2026, 10, 1), 200000.0, 3.0, date(2027, 1, 1), 4)
        assert ok, msg
    text = "".join(page.get_text() for page in fitz.open(stream=generate_loan_note_pdf(lnid), filetype="pdf"))
    for must in ("Loan Schedule", "Payment Schedule", "Difference", "Outstanding Balance", "Check Number", "10/01/2026", "11/01/2026", "04/01/2027", "227,000.00", "56,000.00", "54,500.00",
                 "53,000.00", "51,500.00", "Pending", "200,000.00"):
        assert must in text, must
    assert "Checks Issued" not in text


def test_an_error_midway_leaves_no_half_made_note(db, monkeypatch):
    """Load test, 2026-10-03: a save that waited too long for the database ('database is locked') left a draft note with no schedule."""
    import sqlite3
    from datetime import date
    import engines.loan_schedule_service as svc
    from tests.conftest import fetchone, make_creditor
    lender = make_creditor(db, "Busy Lender", "LENDER")

    def locked(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(svc, "generate_schedule", locked)
    ok, msg, lnid = svc.create_note_with_schedule(lender, date(2026, 10, 1), 200000, 3, date(2027, 1, 1), 4)
    assert not ok and lnid is None and "database is locked" in msg
    assert fetchone(db, "SELECT COUNT(*) FROM loan_notes WHERE creditor_id = ? AND status != 'Cancelled'", (lender,))[0] == 0
