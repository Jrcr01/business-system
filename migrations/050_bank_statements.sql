-- BANK STATEMENT IMPORT (2026-09-27, owner-approved plan): the owner downloads the Metrobank MBOS statement and drops it in the app; the
-- lines are stored once and our checks are matched automatically (engines/bank_statement_service.py). Additive only; applied by
-- update_database.bat (schema.sql); this file is the record. Show-both rule: nothing else in the app changes meaning -- the bank-confirmed
-- cleared date is shown next to the check-date rule the totals still use.

-- One row per imported file. file_hash is unique: the same file is never imported twice.
CREATE TABLE IF NOT EXISTS bank_statement_imports (
    import_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_account_id INTEGER NOT NULL DEFAULT 1,
    file_name TEXT NOT NULL,
    file_hash TEXT NOT NULL UNIQUE,
    stored_path TEXT,
    account_last4 TEXT,
    period_from DATE,
    period_to DATE,
    opening_balance REAL,
    closing_balance REAL,
    line_count INTEGER,
    new_lines INTEGER,
    balance_ok INTEGER NOT NULL DEFAULT 1,
    imported_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(bank_account_id)
);

-- One row per bank line. line_key is unique, so overlapping statement periods never double up.
-- kind: CHECK (ours clearing) / DEPOSIT / RETURNED_CHECK / BILLS_PAYMENT / DEBIT_MEMO / OTHER.
-- match_status (CHECK lines): matched / amount_mismatch / cancelled_check_cleared / not_in_system; returned_check for RETURNED_CHECK.
CREATE TABLE IF NOT EXISTS bank_transactions (
    txn_id INTEGER PRIMARY KEY AUTOINCREMENT,
    import_id INTEGER NOT NULL,
    bank_account_id INTEGER NOT NULL DEFAULT 1,
    txn_date DATE NOT NULL,
    posting_date DATE NOT NULL,
    txn_time TEXT,
    check_number TEXT,
    description TEXT,
    debit REAL NOT NULL DEFAULT 0,
    credit REAL NOT NULL DEFAULT 0,
    balance REAL,
    channel TEXT,
    sequence_no TEXT,
    kind TEXT NOT NULL DEFAULT 'OTHER',
    matched_check_id INTEGER,
    match_status TEXT,
    reviewed INTEGER NOT NULL DEFAULT 0,
    note TEXT,
    line_key TEXT NOT NULL UNIQUE,
    FOREIGN KEY (import_id) REFERENCES bank_statement_imports(import_id),
    FOREIGN KEY (matched_check_id) REFERENCES checks_issued(check_id)
);
CREATE INDEX IF NOT EXISTS idx_bank_transactions_date ON bank_transactions(posting_date);
CREATE INDEX IF NOT EXISTS idx_bank_transactions_check ON bank_transactions(check_number);

-- The bank's own cleared date for our checks (NULL = not seen on an imported statement yet).
ALTER TABLE checks_issued ADD COLUMN bank_cleared_date DATE;
ALTER TABLE checks_issued ADD COLUMN bank_txn_id INTEGER;
