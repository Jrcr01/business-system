"""Beginning-balance label on migrated loans (owner, 2026-10-01: "label the loan notes and bank loans too"). Loan notes the MS Access import left as
placeholders (note no. PENDING-...) and bank loans that came in with only a schedule (no start date and no principal) are tagged
"Beginning balance (from MS Access)": their interest / terms are not split out yet. Nothing about the records or their amounts changes. The label
goes away by itself when the real terms of a loan note are entered, and can be removed by hand."""
import sqlite3
from datetime import date

import app
from conftest import fetchone, make_creditor
from engines.beginning_balance_service import (
    LABEL, apply_loan_labels, loan_label_candidates, remove_beginning_label,
)
from engines.bank_loan_service import add_bank_loan_installment, create_bank_loan
from engines.loan_schedule_service import edit_loan_note_terms
from engines.reports_service import get_bank_loan_servicing_report, get_private_lender_master_report


def _pending_note(db, ref="PENDING-13", name="Ramon Mendoza", principal=1000.0):
    lender = make_creditor(db, name=name, creditor_type="LENDER")
    ok, _, loan_note_id = app.create_loan_note(lender, date(2026, 9, 19), principal, 0.0)
    with sqlite3.connect(db) as conn:
        conn.execute("UPDATE loan_notes SET note_ref = ? WHERE loan_note_id = ?", (ref, loan_note_id))
    return lender, loan_note_id


def _bank_loan(db, ref, complete):
    bank = make_creditor(db, name=f"Bank {ref}", creditor_type="BANK_LOAN")
    if complete:
        return create_bank_loan(bank, ref, "ACC-1", date(2026, 1, 1), date(2027, 1, 1), None, 500000.0, "ADA")
    with sqlite3.connect(db) as conn:
        cur = conn.execute("INSERT INTO bank_loans (creditor_id, loan_ref, status) VALUES (?, ?, 'Active')", (bank, ref))
        return cur.lastrowid


def test_only_migrated_placeholders_are_candidates(db):
    _, migrated = _pending_note(db)
    lender = make_creditor(db, name="Richie", creditor_type="LENDER")
    ok, _, proper = app.create_loan_note(lender, date(2026, 9, 21), 400000.0, 3.0)         # a properly entered note: real LN number
    schedule_only = _bank_loan(db, "LOAN - BDO", complete=False)
    complete = _bank_loan(db, "Suzuki Jimney", complete=True)

    c = loan_label_candidates()

    assert set(c["kind"] + c["id"].astype(str)) == {f"loan_note{migrated}", f"bank_loan{schedule_only}"}
    assert proper not in set(c[c["kind"] == "loan_note"]["id"]) and complete not in set(c[c["kind"] == "bank_loan"]["id"])


def test_applying_labels_them_audits_each_and_changes_nothing_else(db):
    _, ln = _pending_note(db)
    bl = _bank_loan(db, "LOAN - BDO", complete=False)
    before = (fetchone(db, "SELECT principal_amount, total_payable, status, note_ref FROM loan_notes WHERE loan_note_id = ?", (ln,)),
              fetchone(db, "SELECT loan_ref, status, principal_amount FROM bank_loans WHERE bank_loan_id = ?", (bl,)))

    ok, msg = apply_loan_labels()

    assert ok, msg
    assert fetchone(db, "SELECT is_beginning_balance FROM loan_notes WHERE loan_note_id = ?", (ln,))[0] == 1
    assert fetchone(db, "SELECT is_beginning_balance FROM bank_loans WHERE bank_loan_id = ?", (bl,))[0] == 1
    after = (fetchone(db, "SELECT principal_amount, total_payable, status, note_ref FROM loan_notes WHERE loan_note_id = ?", (ln,)),
             fetchone(db, "SELECT loan_ref, status, principal_amount FROM bank_loans WHERE bank_loan_id = ?", (bl,)))
    assert before == after
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BEGINNING_BALANCE_LABELLED'")[0] == 2


def test_applying_twice_labels_nothing_new(db):
    _pending_note(db)
    apply_loan_labels()

    ok, msg = apply_loan_labels()

    assert ok and loan_label_candidates().empty
    assert fetchone(db, "SELECT COUNT(*) FROM audit_logs WHERE user_action = 'BEGINNING_BALANCE_LABELLED'")[0] == 1


def test_entering_the_real_terms_of_a_loan_note_removes_the_label(db):
    _, ln = _pending_note(db)
    apply_loan_labels()

    ok, _ = edit_loan_note_terms(ln, 1000.0, 3.0)

    assert ok
    assert fetchone(db, "SELECT is_beginning_balance, beginning_label_removed FROM loan_notes WHERE loan_note_id = ?", (ln,)) == (0, 1)
    assert "label" in fetchone(db, "SELECT new_values FROM audit_logs WHERE user_action = 'BEGINNING_BALANCE_LABEL_REMOVED'")[0].lower()
    assert loan_label_candidates().empty                                                    # still named PENDING-, but the owner entered real terms: never re-labelled


def test_the_label_can_be_removed_by_hand_and_is_not_put_back(db):
    bl = _bank_loan(db, "LOAN - BDO", complete=False)
    apply_loan_labels()

    ok, msg = remove_beginning_label("bank_loan", bl)

    assert ok, msg
    assert fetchone(db, "SELECT is_beginning_balance, beginning_label_removed FROM bank_loans WHERE bank_loan_id = ?", (bl,)) == (0, 1)
    assert loan_label_candidates().empty                                                    # removed on purpose: never re-applied
    assert not remove_beginning_label("bank_loan", bl)[0]                                   # nothing left to remove
    assert not remove_beginning_label("loan_note", 9999)[0]


def test_the_reports_show_the_label_as_a_source_column(db):
    lender, ln = _pending_note(db)
    bl = _bank_loan(db, "LOAN - BDO", complete=False)
    apply_loan_labels()

    lenders = get_private_lender_master_report()
    banks = get_bank_loan_servicing_report()

    assert list(lenders["source"]) == [LABEL] and list(banks["source"]) == [LABEL]
