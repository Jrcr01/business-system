-- GUARANTEE CHECKS (2026-10-01). Applied by update_database.bat (schema.sql carries the table; this file is the record).

-- A guarantee check is given to a lender as security (usually undated, face amount sometimes only noted, e.g. "2M") and cancelled or taken back once
-- the loan is settled. It is not a payment, so it has its own table instead of checks_issued. See engines/guarantee_check_service.py.
CREATE TABLE IF NOT EXISTS guarantee_checks (
    guarantee_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_account_id INTEGER NOT NULL DEFAULT 1,
    check_number TEXT NOT NULL,
    creditor_id INTEGER NOT NULL,
    loan_note_id INTEGER,
    face_amount REAL CHECK(face_amount IS NULL OR face_amount >= 0),
    given_on DATE,
    status TEXT NOT NULL DEFAULT 'Held' CHECK(status IN ('Held', 'Returned', 'Presented')),
    returned_on DATE,
    return_note TEXT,
    presented_on DATE,
    bank_txn_id INTEGER,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (bank_account_id, check_number),
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(bank_account_id),
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (loan_note_id) REFERENCES loan_notes(loan_note_id)
);
