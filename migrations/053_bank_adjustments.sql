-- BANK ADJUSTMENTS (2026-09-28). Applied by update_database.bat (schema.sql adds the new table); this file is the record.

-- Bank adjustments (migration 053, 2026-09-28, owner: "Bank statement is the ultimate truth"; "not direct edit the record but tag it
-- as adjustment due to incorrect recording in the book"). The check stays exactly as recorded; each difference the bank shows is a
-- separate, reversible entry: CHECK_AMOUNT (signed: bank amount - recorded amount), CHECK_DATE (recorded date vs the bank's date) and
-- BATCH_BALANCE (signed, the matching change to what the batch owes, "bank clearing difference"). Reversed = reversed_at set, kept.
CREATE TABLE IF NOT EXISTS bank_adjustments (
    adjustment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL CHECK(kind IN ('CHECK_AMOUNT', 'CHECK_DATE', 'BATCH_BALANCE')),
    check_id INTEGER,
    bank_txn_id INTEGER,
    statement_id INTEGER,
    loan_note_id INTEGER,
    recorded_value TEXT,
    bank_value TEXT,
    amount REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    reversed_at DATETIME,
    FOREIGN KEY (check_id) REFERENCES checks_issued(check_id),
    FOREIGN KEY (bank_txn_id) REFERENCES bank_transactions(txn_id),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);
