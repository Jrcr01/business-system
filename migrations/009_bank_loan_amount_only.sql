-- Migration 009: simplify bank_loan_installments to Due Date + Amount only
--
-- User correction right after 008 shipped: "actually i need only the Due date and amount" --
-- the full bank-document column set (No. of Days, Int Rate, Principal, Interest, MRI, FIP,
-- Accrued Interest, Running Balance) from 008 was more than actually wanted. Dropping all of
-- it in favor of a single Amount per due date, matching how the user actually wants to work.
-- Full table rebuild (SQLite can't drop columns from a CHECK-constrained table in place);
-- applied with foreign_keys off, pre-migration backup taken, verified after with
-- PRAGMA foreign_key_check and a before/after row count. amount is carried over from the old
-- total_amount_due column (already the full amount due that period), so no data is lost beyond
-- the principal/interest/MRI/FIP breakdown itself, which is the intentional simplification.

CREATE TABLE bank_loan_installments_new (
    installment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_loan_id INTEGER NOT NULL,
    installment_no INTEGER NOT NULL,
    due_date DATE NOT NULL,
    amount REAL NOT NULL DEFAULT 0 CHECK(amount >= 0),
    status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending', 'Paid')),
    paid_date DATE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bank_loan_id) REFERENCES bank_loans(bank_loan_id)
);

INSERT INTO bank_loan_installments_new (installment_id, bank_loan_id, installment_no, due_date, amount, status, paid_date, created_at)
SELECT installment_id, bank_loan_id, installment_no, due_date, total_amount_due, status, paid_date, created_at
FROM bank_loan_installments;

DROP TABLE bank_loan_installments;
ALTER TABLE bank_loan_installments_new RENAME TO bank_loan_installments;
