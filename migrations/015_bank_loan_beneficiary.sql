-- Migration 015: loan beneficiaries registry + bank_loans.beneficiary_id
--
-- User: "for bank loans, i need also to see to whom the bank loan it is attributable to this
-- can be from a person, affliates that the business are the one using and paying for it."
-- Same idea as the credit card cardholder registry, but broader -- who's actually using/
-- benefiting from a loan the company is paying for, whether that's a person or an affiliate
-- company. Kept as its own registry (not reusing cardholders) since beneficiary_type explicitly
-- covers Person OR Affiliate, unlike cardholders which is always a person. Confirmed via
-- AskUserQuestion: a reusable registry (like cardholders), not a free-text field per loan.
--
-- Full rebuild of bank_loans since SQLite can't add a FOREIGN KEY via ALTER TABLE; applied with
-- foreign_keys off, pre-migration backup taken, verified after with PRAGMA foreign_key_check
-- and a row count. No live data lost -- both existing loans get beneficiary_id = NULL,
-- backfilled later via the UI same as cardholder_id was for existing credit cards.

CREATE TABLE loan_beneficiaries (
    beneficiary_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    beneficiary_type TEXT NOT NULL CHECK(beneficiary_type IN ('Person', 'Affiliate')),
    contact_number TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE bank_loans_new (
    bank_loan_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    beneficiary_id INTEGER,
    loan_ref TEXT UNIQUE NOT NULL,
    account_number TEXT,
    start_date DATE NOT NULL,
    maturity_date DATE NOT NULL,
    repricing_date DATE,
    principal_amount REAL NOT NULL,
    mode_of_payment TEXT DEFAULT 'ADA',
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Closed')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (beneficiary_id) REFERENCES loan_beneficiaries(beneficiary_id)
);

INSERT INTO bank_loans_new (bank_loan_id, creditor_id, beneficiary_id, loan_ref, account_number, start_date, maturity_date, repricing_date, principal_amount, mode_of_payment, status, created_at)
SELECT bank_loan_id, creditor_id, NULL, loan_ref, account_number, start_date, maturity_date, repricing_date, principal_amount, mode_of_payment, status, created_at
FROM bank_loans;

DROP TABLE bank_loans;
PRAGMA legacy_alter_table = ON;
ALTER TABLE bank_loans_new RENAME TO bank_loans;
PRAGMA legacy_alter_table = OFF;
