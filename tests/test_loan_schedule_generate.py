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
