-- Migration 005: private lender repayment schedule
-- Loan notes gain a monthly interest rate (loans accrue on a declining balance at a fixed
-- rate, not a flat total agreed upfront); the actual schedule of installments lives in a new
-- table, entered progressively as real payment dates are committed to (not fully planned at
-- loan signing). total_interest/total_payable on loan_notes become derived from this schedule.

ALTER TABLE loan_notes ADD COLUMN monthly_interest_rate REAL NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS loan_installments (
    installment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_note_id INTEGER NOT NULL,
    installment_no INTEGER NOT NULL,
    due_date DATE NOT NULL,
    principal REAL NOT NULL DEFAULT 0 CHECK(principal >= 0),
    interest REAL NOT NULL DEFAULT 0 CHECK(interest >= 0),
    outstanding_balance REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending', 'Paid')),
    check_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (loan_note_id) REFERENCES loan_notes(loan_note_id),
    FOREIGN KEY (check_id) REFERENCES checks_issued(check_id)
);
