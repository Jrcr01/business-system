-- Migration 010: drop manual status/paid_date from bank_loan_installments
--
-- Follow-up correction, same session as 009: "paid status is the same, be cleared automatically
-- once the date is past the due date." A manually-toggled Pending/Paid status was the wrong
-- model -- this app already has a standing convention for exactly this (CLAUDE.md: "Check
-- 'cleared' status: not a stored field. Implied automatically once the check's date is in the
-- past relative to today -- no manual flag, no reconciliation step in this system," also used
-- for v_checks_ledger.effective_status and the Loan Notes/Order Statement "cleared vs issued"
-- work). Bank loan dues now follow the same rule: due_date <= today means cleared/paid,
-- computed at query time, never stored. Full table rebuild; applied with foreign_keys off,
-- pre-migration backup taken, verified with PRAGMA foreign_key_check and a row count.

CREATE TABLE bank_loan_installments_new (
    installment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_loan_id INTEGER NOT NULL,
    installment_no INTEGER NOT NULL,
    due_date DATE NOT NULL,
    amount REAL NOT NULL DEFAULT 0 CHECK(amount >= 0),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bank_loan_id) REFERENCES bank_loans(bank_loan_id)
);

INSERT INTO bank_loan_installments_new (installment_id, bank_loan_id, installment_no, due_date, amount, created_at)
SELECT installment_id, bank_loan_id, installment_no, due_date, amount, created_at
FROM bank_loan_installments;

DROP TABLE bank_loan_installments;
ALTER TABLE bank_loan_installments_new RENAME TO bank_loan_installments;
