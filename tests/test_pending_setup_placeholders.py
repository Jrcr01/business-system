"""Phase 8a migrated a batch of lenders/bank loans in as placeholders with no real terms on
file yet -- loan_notes.status='Pending Setup' (principal_amount/monthly_interest_rate NULL) and
bank_loans with has_amortization=0 (start_date/maturity_date NULL). Several features crashed with
a raw TypeError the moment they touched one of these records before its real terms were entered
(2026-09-17 fix)."""
import sqlite3

import app
from conftest import make_creditor


def _make_pending_setup_loan_note(db_path, creditor_id):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO loan_notes (creditor_id, note_ref, issue_date, principal_amount, monthly_interest_rate, status) "
        "VALUES (?, 'PENDING-1', '2026-01-01', NULL, NULL, 'Pending Setup')",
        (creditor_id,),
    )
    loan_note_id = cur.lastrowid
    conn.commit()
    conn.close()
    return loan_note_id


def _make_placeholder_bank_loan(db_path, creditor_id):
    conn = sqlite3.connect(db_path)
    cur = conn.execute(
        "INSERT INTO bank_loans (creditor_id, loan_ref, start_date, maturity_date, principal_amount, has_amortization) "
        "VALUES (?, 'BL-PENDING-1', NULL, NULL, NULL, 0)",
        (creditor_id,),
    )
    bank_loan_id = cur.lastrowid
    conn.commit()
    conn.close()
    return bank_loan_id


def test_add_loan_installment_on_pending_setup_note_fails_cleanly(db):
    """Used to raise a raw TypeError (None - float) inside _recompute_schedule()."""
    from datetime import date
    from engines.loan_schedule_service import add_loan_installment

    creditor_id = make_creditor(db, creditor_type="LENDER")
    loan_note_id = _make_pending_setup_loan_note(db, creditor_id)

    ok, msg = add_loan_installment(loan_note_id, date(2026, 2, 1), 1000.0, 50.0)

    assert not ok
    assert "terms first" in msg.lower()


def test_loan_note_pdf_handles_missing_interest_rate(db):
    """Used to raise a raw TypeError formatting None with :g."""
    from engines.loan_note_service import generate_loan_note_pdf

    creditor_id = make_creditor(db, creditor_type="LENDER")
    loan_note_id = _make_pending_setup_loan_note(db, creditor_id)

    pdf_bytes = generate_loan_note_pdf(loan_note_id)

    assert pdf_bytes is not None
    assert len(pdf_bytes) > 0


def test_bank_loan_pdf_handles_missing_dates(db):
    """Used to raise a raw TypeError passing None into reportlab's Paragraph()."""
    from engines.bank_loan_report_service import generate_bank_loan_pdf

    creditor_id = make_creditor(db, creditor_type="BANK_LOAN")
    bank_loan_id = _make_placeholder_bank_loan(db, creditor_id)

    pdf_bytes = generate_bank_loan_pdf(bank_loan_id)

    assert pdf_bytes is not None
    assert len(pdf_bytes) > 0
