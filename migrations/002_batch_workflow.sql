-- Migration 002: Batch workflow (credit memos, lock/unlock, corrections, replacement context, spoiled checks)
-- Additive only: new tables + nullable columns with defaults. Safe to run against the populated DB.

ALTER TABLE order_statements ADD COLUMN is_locked INTEGER NOT NULL DEFAULT 0;

ALTER TABLE checks_issued ADD COLUMN replacement_reason TEXT;
ALTER TABLE checks_issued ADD COLUMN replaced_at DATETIME;

CREATE TABLE IF NOT EXISTS credit_memos (
    credit_memo_id INTEGER PRIMARY KEY AUTOINCREMENT,
    statement_id INTEGER NOT NULL,
    memo_ref TEXT NOT NULL,
    memo_date DATE NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0),
    reason TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);

CREATE TABLE IF NOT EXISTS check_corrections (
    correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    check_id INTEGER NOT NULL,
    statement_id INTEGER,
    correction_type TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    reason TEXT,
    corrected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (check_id) REFERENCES checks_issued(check_id),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);

CREATE TABLE IF NOT EXISTS spoiled_checks (
    spoiled_id INTEGER PRIMARY KEY AUTOINCREMENT,
    bank_account_id INTEGER NOT NULL,
    check_number TEXT NOT NULL,
    reason TEXT,
    logged_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(bank_account_id)
);

-- Net-of-credit-memo batch totals (gross total_amount stays untouched/auditable)
CREATE VIEW IF NOT EXISTS v_batch_net_totals AS
SELECT
    os.statement_id,
    os.creditor_id,
    os.statement_ref,
    os.total_amount AS gross_total,
    COALESCE(cm.memo_total, 0.0) AS credit_memo_total,
    os.total_amount - COALESCE(cm.memo_total, 0.0) AS net_total,
    os.status,
    os.is_locked
FROM order_statements os
LEFT JOIN (
    SELECT statement_id, SUM(amount) AS memo_total
    FROM credit_memos
    GROUP BY statement_id
) cm ON cm.statement_id = os.statement_id;
