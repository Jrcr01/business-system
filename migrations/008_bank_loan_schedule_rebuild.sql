-- Migration 008: rebuild bank_loans + rename/rebuild bank_amortization_schedules -> bank_loan_installments
--
-- Phase 3 (Bank Loan: Monthly Dues) scoped 2026-09-06 against a real bank amortization schedule.
-- The pre-existing bank_loans/bank_amortization_schedules tables predate that scoping and don't
-- match the real document's columns (No. of Days, Int Rate, MRI, FIP, Accrued Interest, Running
-- Balance) and hard-assumed ADA settlement, which was confirmed to be wrong (settlement is
-- untracked cash by default). This is a full table rebuild (SQLite can't ALTER a CHECK constraint
-- or rename+retype columns in place); applied with foreign_keys off, pre-migration backup taken,
-- verified after with PRAGMA foreign_key_check (no violations) and before/after row counts.
--
-- bank_loans: adds account_number, repricing_date, mode_of_payment (informational only, describes
-- what the bank's paperwork says, drives no logic); drops monthly_installment (never authoritative
-- once schedules are entered row-by-row with their own totals, MRI, etc.).
--
-- bank_amortization_schedules -> bank_loan_installments: adds no_of_days, int_rate, mri, fip,
-- accrued_interest, running_balance, paid_date; renames principal_amount/interest_amount/
-- total_payment -> principal/interest/total_amount_due; drops payment_method (settlement is
-- untracked, no per-installment payment method needed); status values 'Pending'/'Settled' ->
-- 'Pending'/'Paid' (this phase never claims a check "settled" anything, just that cash was paid).

CREATE TABLE bank_loans_new (
    bank_loan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    loan_ref TEXT UNIQUE NOT NULL,
    account_number TEXT,
    start_date DATE NOT NULL,
    maturity_date DATE NOT NULL,
    repricing_date DATE,
    principal_amount REAL NOT NULL,
    mode_of_payment TEXT DEFAULT 'ADA',
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Closed')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id)
);

INSERT INTO bank_loans_new (bank_loan_id, creditor_id, loan_ref, account_number, start_date, maturity_date, repricing_date, principal_amount, mode_of_payment, status, created_at)
SELECT bank_loan_id, creditor_id, loan_ref, NULL, start_date, maturity_date, NULL, principal_amount, 'ADA', status, created_at
FROM bank_loans;

DROP TABLE bank_loans;
ALTER TABLE bank_loans_new RENAME TO bank_loans;

CREATE TABLE bank_loan_installments (
    installment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_loan_id INTEGER NOT NULL,
    installment_no INTEGER NOT NULL,
    due_date DATE NOT NULL,
    no_of_days INTEGER,
    int_rate REAL,
    principal REAL NOT NULL DEFAULT 0 CHECK(principal >= 0),
    interest REAL NOT NULL DEFAULT 0 CHECK(interest >= 0),
    mri REAL NOT NULL DEFAULT 0 CHECK(mri >= 0),
    fip REAL NOT NULL DEFAULT 0 CHECK(fip >= 0),
    accrued_interest REAL NOT NULL DEFAULT 0,
    total_amount_due REAL NOT NULL DEFAULT 0,
    running_balance REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'Pending' CHECK(status IN ('Pending', 'Paid')),
    paid_date DATE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bank_loan_id) REFERENCES bank_loans(bank_loan_id)
);

-- Legacy sample rows (Sampledata.sql's BDO term loan, 2 pending installments) carried over with
-- running_balance computed by hand from the loan's principal_amount minus cumulative principal:
-- 5,000,000.00 - 195,000.00 = 4,805,000.00; 4,805,000.00 - 197,000.00 = 4,608,000.00.
INSERT INTO bank_loan_installments (installment_id, bank_loan_id, installment_no, due_date, no_of_days, int_rate, principal, interest, mri, fip, accrued_interest, total_amount_due, running_balance, status, paid_date)
SELECT schedule_id, bank_loan_id, installment_no, due_date, NULL, NULL, principal_amount, interest_amount, 0, 0, 0, total_payment,
       CASE schedule_id WHEN 1 THEN 4805000.00 WHEN 2 THEN 4608000.00 ELSE 0 END,
       CASE WHEN status = 'Settled' THEN 'Paid' ELSE status END,
       NULL
FROM bank_amortization_schedules;

DROP TABLE bank_amortization_schedules;
