-- CHECK RELEASE SLIPS (2026-10-03). Applied by update_database.bat (schema.sql carries the tables; this file is the record).
-- The statement printed for hand-over carries a slip number (RS-2026-0001, doc_counters series 'RS'). Releasing is recorded by that number: every check on the slip is marked
-- released at once, with who received them and when.
CREATE TABLE IF NOT EXISTS release_slips (
    slip_id INTEGER PRIMARY KEY AUTOINCREMENT,
    slip_no TEXT NOT NULL UNIQUE,
    statement_id INTEGER NOT NULL REFERENCES order_statements(statement_id),
    creditor_id INTEGER NOT NULL REFERENCES creditors(creditor_id),
    check_count INTEGER NOT NULL,
    total REAL NOT NULL,
    printed_by TEXT,
    printed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    released_at DATETIME,
    received_by TEXT,
    released_by TEXT
);
CREATE TABLE IF NOT EXISTS release_slip_checks (
    slip_id INTEGER NOT NULL REFERENCES release_slips(slip_id),
    check_id INTEGER NOT NULL REFERENCES checks_issued(check_id),
    PRIMARY KEY (slip_id, check_id)
);
CREATE INDEX IF NOT EXISTS idx_release_slips_open ON release_slips (released_at);
