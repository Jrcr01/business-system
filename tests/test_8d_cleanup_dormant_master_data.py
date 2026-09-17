"""Phase 8d: dormant master data cleanup -- tests for phase8d_cleanup_dormant_master_data.py."""
import sqlite3

from conftest import make_creditor

import phase8d_cleanup_dormant_master_data as p8d


def test_supplier_with_no_active_check_gets_deactivated(db):
    conn = sqlite3.connect(db)
    dormant_id = make_creditor(db, "Dormant Supplier", "SUPPLIER")
    active_id = make_creditor(db, "Active Supplier", "SUPPLIER")
    conn.execute(
        "INSERT INTO bank_accounts (bank_account_id, bank_name, account_name, account_number) "
        "SELECT 99, 'Test Bank', 'Test', 'X' WHERE NOT EXISTS (SELECT 1 FROM bank_accounts)"
    )
    bank_account_id = conn.execute("SELECT bank_account_id FROM bank_accounts LIMIT 1").fetchone()[0]
    statement_id = conn.execute(
        "INSERT INTO order_statements (creditor_id, statement_ref, total_amount, status) VALUES (?, 'REF1', 0, 'Issued')",
        (active_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO checks_issued (creditor_id, bank_account_id, statement_id, check_date, amount, status) "
        "VALUES (?, ?, ?, '2027-01-01', 1000, 'Active')",
        (active_id, bank_account_id, statement_id),
    )
    conn.commit()

    stats = p8d.cleanup_dormant_master_data(conn)

    assert stats["suppliers_deactivated"] == 1
    dormant_active = conn.execute("SELECT is_active FROM creditors WHERE creditor_id = ?", (dormant_id,)).fetchone()[0]
    active_active = conn.execute("SELECT is_active FROM creditors WHERE creditor_id = ?", (active_id,)).fetchone()[0]
    assert dormant_active == 0
    assert active_active == 1
    conn.close()


def test_lender_with_no_active_check_gets_deactivated(db):
    conn = sqlite3.connect(db)
    dormant_id = make_creditor(db, "Dormant Lender", "LENDER")
    conn.execute(
        "INSERT INTO loan_notes (creditor_id, note_ref, issue_date, status) VALUES (?, 'LN1', '2026-01-01', 'Pending Setup')",
        (dormant_id,),
    )
    conn.commit()

    stats = p8d.cleanup_dormant_master_data(conn)

    assert stats["lenders_deactivated"] == 1
    is_active = conn.execute("SELECT is_active FROM creditors WHERE creditor_id = ?", (dormant_id,)).fetchone()[0]
    assert is_active == 0
    # The loan note itself is untouched -- only the creditor's is_active flag changes.
    status = conn.execute("SELECT status FROM loan_notes WHERE creditor_id = ?", (dormant_id,)).fetchone()[0]
    assert status == "Pending Setup"
    conn.close()


def test_bank_loan_with_no_installments_gets_closed(db):
    conn = sqlite3.connect(db)
    creditor_id = make_creditor(db, "Test Bank Loan Creditor", "BANK_LOAN")
    empty_loan_id = conn.execute(
        "INSERT INTO bank_loans (creditor_id, loan_ref, status) VALUES (?, 'BL1', 'Active')", (creditor_id,)
    ).lastrowid
    funded_loan_id = conn.execute(
        "INSERT INTO bank_loans (creditor_id, loan_ref, status) VALUES (?, 'BL2', 'Active')", (creditor_id,)
    ).lastrowid
    conn.execute(
        "INSERT INTO bank_loan_installments (bank_loan_id, installment_no, due_date, amount) VALUES (?, 1, '2027-01-01', 5000)",
        (funded_loan_id,),
    )
    conn.commit()

    stats = p8d.cleanup_dormant_master_data(conn)

    assert stats["bank_loans_closed"] == 1
    empty_status = conn.execute("SELECT status FROM bank_loans WHERE bank_loan_id = ?", (empty_loan_id,)).fetchone()[0]
    funded_status = conn.execute("SELECT status FROM bank_loans WHERE bank_loan_id = ?", (funded_loan_id,)).fetchone()[0]
    assert empty_status == "Closed"
    assert funded_status == "Active"
    conn.close()
