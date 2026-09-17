-- Migration 006: allow loan notes to be Cancelled
-- Order Statements already had 'Cancelled' in their status CHECK constraint from day one;
-- Loan Notes didn't (only Active/Settled/Defaulted), which blocked a cancel-batch-style
-- soft-cancel for a loan note that already has checks issued against it. SQLite can't ALTER
-- a CHECK constraint in place, so this is a full table rebuild (rename -> recreate -> copy ->
-- drop), applied against the live DB with foreign_keys temporarily off and a pre-migration
-- backup taken; verified with PRAGMA foreign_key_check afterward (no violations).

CREATE TABLE loan_notes_new (
    loan_note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    note_ref TEXT UNIQUE NOT NULL,
    issue_date DATE NOT NULL,
    principal_amount REAL NOT NULL CHECK(principal_amount > 0),
    monthly_interest_rate REAL NOT NULL DEFAULT 0,
    total_interest REAL DEFAULT 0.0,
    total_payable REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Settled', 'Defaulted', 'Cancelled')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id)
);

INSERT INTO loan_notes_new (loan_note_id, creditor_id, note_ref, issue_date, principal_amount, monthly_interest_rate, total_interest, total_payable, status, created_at)
SELECT loan_note_id, creditor_id, note_ref, issue_date, principal_amount, monthly_interest_rate, total_interest, total_payable, status, created_at FROM loan_notes;

DROP TABLE loan_notes;
ALTER TABLE loan_notes_new RENAME TO loan_notes;
