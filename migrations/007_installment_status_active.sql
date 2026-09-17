-- Migration 007: rename loan_installments.status value 'Paid' -> 'Active'
-- "Paid" implied a check had actually cleared/been collected, but this app never tracks real
-- clearance (a check's cleared status is implied only by date being in the past) -- the row
-- just means a check has been issued against it and hasn't been cancelled, which is exactly
-- what checks_issued.status = 'Active' already means. Reusing that same word avoids the
-- private lender having to learn two different words for the same idea across two tables.
-- Full table rebuild (SQLite can't ALTER a CHECK constraint in place); applied against the live
-- DB with foreign_keys off and a pre-migration backup, verified after with
-- PRAGMA foreign_key_check (no violations) and a before/after row count.

CREATE TABLE loan_installments_new (
    installment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    loan_note_id INTEGER NOT NULL,
    installment_no INTEGER NOT NULL,
    due_date DATE NOT NULL,
    principal REAL NOT NULL DEFAULT 0 CHECK(principal >= 0),
    interest REAL NOT NULL DEFAULT 0 CHECK(interest >= 0),
    outstanding_balance REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending', 'Active')),
    check_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (loan_note_id) REFERENCES loan_notes(loan_note_id),
    FOREIGN KEY (check_id) REFERENCES checks_issued(check_id)
);

INSERT INTO loan_installments_new (installment_id, loan_note_id, installment_no, due_date, principal, interest, outstanding_balance, status, check_id, created_at)
SELECT installment_id, loan_note_id, installment_no, due_date, principal, interest, outstanding_balance,
       CASE WHEN status = 'Paid' THEN 'Active' ELSE status END,
       check_id, created_at
FROM loan_installments;

DROP TABLE loan_installments;
ALTER TABLE loan_installments_new RENAME TO loan_installments;
