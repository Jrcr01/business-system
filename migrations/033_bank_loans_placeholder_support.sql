-- Migration 033: Phase 8a (Master Data Setup) -- bank_loans placeholder support
--
-- 30 real bank loans surface from the Access migration with no amortization schedule uploaded
-- yet. has_amortization flags which loans still need one (0 = placeholder, needs the real
-- schedule; set to 1 once bank_loan_installments rows exist for it). start_date/maturity_date
-- were NOT NULL since the original schema -- a placeholder genuinely doesn't have real dates
-- yet, so both become nullable. Full rebuild (same reasoning/pattern as migration 032's
-- loan_notes rebuild -- SQLite can't drop NOT NULL via ALTER TABLE); has_amortization itself
-- is a plain ADD COLUMN, folded into the same rebuild rather than a separate migration.
-- principal_amount also goes from NOT NULL to nullable -- the Access export carries no
-- monetary data at all for these loans, so a placeholder genuinely can't state one without
-- inventing it.
--
-- PRAGMA foreign_keys = OFF around the drop/rename: bank_loan_installments.bank_loan_id FKs
-- into this table.

PRAGMA foreign_keys = OFF;

CREATE TABLE bank_loans_new (
    bank_loan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    beneficiary_id INTEGER,
    loan_ref TEXT UNIQUE NOT NULL,
    account_number TEXT,
    start_date DATE,
    maturity_date DATE,
    repricing_date DATE,
    principal_amount REAL,
    mode_of_payment TEXT DEFAULT 'ADA',
    has_amortization INTEGER NOT NULL DEFAULT 0 CHECK(has_amortization IN (0, 1)),
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Closed')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (beneficiary_id) REFERENCES loan_beneficiaries(beneficiary_id)
);

INSERT INTO bank_loans_new (bank_loan_id, creditor_id, beneficiary_id, loan_ref, account_number,
    start_date, maturity_date, repricing_date, principal_amount, mode_of_payment,
    has_amortization, status, created_at)
SELECT bank_loan_id, creditor_id, beneficiary_id, loan_ref, account_number,
       start_date, maturity_date, repricing_date, principal_amount, mode_of_payment,
       1, status, created_at
FROM bank_loans;

DROP TABLE bank_loans;
PRAGMA legacy_alter_table = ON;
ALTER TABLE bank_loans_new RENAME TO bank_loans;
PRAGMA legacy_alter_table = OFF;

PRAGMA foreign_keys = ON;
