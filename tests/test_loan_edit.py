"""Editing what used to be fixed on a loan (owner, 2026-09-30, plan D2): a loan note's number, issue date and lender; a bank loan's bank, reference,
account, dates, principal, mode of payment and amortization flag. Every change is validated, audited old -> new, and leaves money alone."""
import sqlite3
from datetime import date, timedelta

import app
from conftest import fetchone, make_creditor
from engines.bank_loan_service import add_bank_loan_installment, create_bank_loan
from engines.beginning_balance_service import apply_loan_labels
from engines.loan_edit_service import edit_bank_loan_details, edit_loan_note_details
from engines.loan_schedule_service import add_loan_installment


def _note(db, name="Felix", ref="PENDING-123"):
    lender = make_creditor(db, name=name, creditor_type="LENDER")
    ok, _, lnid = app.create_loan_note(lender, date(2026, 9, 19), 100000.0, 2.0)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE loan_notes SET note_ref = ? WHERE loan_note_id = ?", (ref, lnid))
    return lender, lnid


def _check(db, lnid, creditor, number, cleared=False):
    with sqlite3.connect(db) as conn:
        conn.execute("""INSERT INTO checks_issued (bank_account_id, creditor_id, loan_note_id, check_number, check_date, amount, status, bank_cleared_date)
                        VALUES (1, ?, ?, ?, '2026-12-01', 5000, 'Active', ?)""", (creditor, lnid, number, "2026-09-20" if cleared else None))


def test_the_note_number_issue_date_and_lender_can_be_changed_and_are_audited(db):
    felix, lnid = _note(db)
    cruz = make_creditor(db, name="Felix T Cruz", creditor_type="LENDER")
    _check(db, lnid, felix, "3992000001")

    ok, msg = edit_loan_note_details(lnid, "2026 - LN 0099", date(2026, 8, 1), cruz)

    assert ok, msg
    assert fetchone(db, "SELECT note_ref, issue_date, creditor_id FROM loan_notes WHERE loan_note_id = ?", (lnid,)) == ("2026 - LN 0099", "2026-08-01", cruz)
    assert fetchone(db, "SELECT creditor_id FROM checks_issued WHERE loan_note_id = ?", (lnid,))[0] == cruz              # its checks follow the lender
    log = fetchone(db, "SELECT old_values, new_values FROM audit_logs WHERE user_action = 'LOAN_NOTE_DETAILS_EDITED'")
    assert "PENDING-123" in log[0] and "PENDING-123 → 2026 - LN 0099" in log[1] and "Felix → Felix T Cruz" in log[1]


def test_refused_changes_write_nothing(db):
    felix, lnid = _note(db)
    _, other = _note(db, name="Other", ref="2026 - LN 0001")
    supplier = make_creditor(db, name="Sup", creditor_type="SUPPLIER")
    tomorrow_ish = date(2026, 9, 19) + timedelta(days=30)
    add_loan_installment(lnid, tomorrow_ish, 10000.0, 0.0)

    assert "already used" in edit_loan_note_details(lnid, "2026 - LN 0001", date(2026, 9, 19), felix)[1]
    assert "empty" in edit_loan_note_details(lnid, "  ", date(2026, 9, 19), felix)[1]
    assert "after the first installment" in edit_loan_note_details(lnid, "PENDING-123", tomorrow_ish + timedelta(days=1), felix)[1]
    assert "not a lender" in edit_loan_note_details(lnid, "PENDING-123", date(2026, 9, 19), supplier)[1]
    assert edit_loan_note_details(999, "X", date(2026, 9, 19), felix)[0] is False
    assert fetchone(db, "SELECT note_ref, creditor_id FROM loan_notes WHERE loan_note_id = ?", (lnid,)) == ("PENDING-123", felix)
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'LOAN_NOTE_DETAILS_EDITED'")[0] == 0


def test_the_lender_cannot_change_once_the_bank_cleared_a_check_and_a_cancelled_note_is_permanent(db):
    felix, lnid = _note(db)
    cruz = make_creditor(db, name="Cruz", creditor_type="LENDER")
    _check(db, lnid, felix, "3992000002", cleared=True)
    ok, msg = edit_loan_note_details(lnid, "PENDING-123", date(2026, 9, 19), cruz)
    assert not ok and "already cleared by the bank" in msg
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE loan_notes SET status = 'Cancelled' WHERE loan_note_id = ?", (lnid,))
    assert "permanent" in edit_loan_note_details(lnid, "NEW", date(2026, 9, 19), felix)[1]


def test_an_unchanged_save_is_a_no_op(db):
    felix, lnid = _note(db)
    assert edit_loan_note_details(lnid, "PENDING-123", date(2026, 9, 19), felix) == (True, "No changes.")
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'LOAN_NOTE_DETAILS_EDITED'")[0] == 0


def _bank_loan(db, ref="LOAN - BDO_1", complete=False):
    bank = make_creditor(db, name=f"Bank {ref}", creditor_type="BANK_LOAN")
    if complete:
        return bank, create_bank_loan(bank, ref, "ACC-1", date(2026, 1, 1), date(2027, 1, 1), None, 500000.0, "ADA")
    with sqlite3.connect(db) as conn:
        return bank, conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref, status) VALUES (?, ?, 'Active')", (bank, ref)).lastrowid


def test_every_bank_loan_detail_can_be_edited_and_is_audited(db):
    bank, blid = _bank_loan(db, complete=True)
    other = make_creditor(db, name="BPI", creditor_type="BANK_LOAN")

    ok, msg = edit_bank_loan_details(blid, other, "BPI-2026-7", "999", date(2026, 2, 1), date(2028, 2, 1), date(2027, 2, 1), 750000.0, "Manual", True)

    assert ok, msg
    row = fetchone(db, """SELECT creditor_id, loan_ref, account_number, start_date, maturity_date, repricing_date, principal_amount, mode_of_payment, has_amortization
                          FROM bank_loans WHERE bank_loan_id = ?""", (blid,))
    assert row == (other, "BPI-2026-7", "999", "2026-02-01", "2028-02-01", "2027-02-01", 750000.0, "Manual", 1)
    log = fetchone(db, "SELECT new_values FROM audit_logs WHERE user_action = 'BANK_LOAN_DETAILS_EDITED'")[0]
    assert "Principal: 500,000.00 → 750,000.00" in log and "Loan ref: LOAN - BDO_1 → BPI-2026-7" in log


def test_bank_loan_rules_and_unchanged_save(db):
    bank, blid = _bank_loan(db, ref="A", complete=True)
    _, other_id = _bank_loan(db, ref="B", complete=True)
    ok = lambda **kw: edit_bank_loan_details(blid, **{"creditor_id": bank, "loan_ref": "A", "account_number": "ACC-1", "start_date": date(2026, 1, 1),   # noqa: E731
                                                       "maturity_date": date(2027, 1, 1), "repricing_date": None, "principal_amount": 500000.0, "mode_of_payment": "ADA", **kw})
    assert ok() == (True, "No changes.")
    assert "already used" in ok(loan_ref="B")[1]
    assert "after the start" in ok(maturity_date=date(2026, 1, 1))[1]
    assert "typo" in ok(principal_amount=0.5)[1]
    assert "Repricing" in ok(repricing_date=date(2025, 1, 1))[1]
    assert "not a bank" in ok(creditor_id=make_creditor(db, name="Sup", creditor_type="SUPPLIER"))[1]
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BANK_LOAN_DETAILS_EDITED'")[0] == 0
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE bank_loans SET is_cancelled = 1 WHERE bank_loan_id = ?", (blid,))
    assert "permanent" in ok(loan_ref="Z")[1]


def test_entering_real_details_removes_the_beginning_label_for_good_and_leaves_the_schedule_alone(db):
    bank, blid = _bank_loan(db)                                  # schedule-only, no start date, no principal
    add_bank_loan_installment(blid, date(2026, 12, 1), 25000.0)
    apply_loan_labels()
    assert fetchone(db, "SELECT is_beginning_balance FROM bank_loans WHERE bank_loan_id = ?", (blid,))[0] == 1

    ok, msg = edit_bank_loan_details(blid, bank, "LOAN - BDO_1", None, date(2026, 1, 1), date(2027, 1, 1), None, 300000.0, "ADA")

    assert ok, msg
    assert fetchone(db, "SELECT is_beginning_balance, beginning_label_removed FROM bank_loans WHERE bank_loan_id = ?", (blid,)) == (0, 1)
    assert fetchone(db, "SELECT COUNT(*), SUM(amount) FROM bank_loan_installments WHERE bank_loan_id = ?", (blid,)) == (1, 25000.0)
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BEGINNING_BALANCE_LABEL_REMOVED'")[0] == 1
    assert apply_loan_labels()[1] == "Labelled 0 loan note(s) and 0 bank loan(s)."                  # never put back


def test_a_partial_edit_keeps_the_label(db):
    bank, blid = _bank_loan(db)
    apply_loan_labels()
    edit_bank_loan_details(blid, bank, "LOAN - BDO_1", "ACC-9", None, None, None, None, "ADA")
    assert fetchone(db, "SELECT is_beginning_balance FROM bank_loans WHERE bank_loan_id = ?", (blid,))[0] == 1
