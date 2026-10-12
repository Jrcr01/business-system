-- SUPPLIER STATEMENT CHECK (owner, 2026-10-13: statements come as photos and paper in different layouts; the common fields are the document
-- number, the date and the amount). A saved comparison of a supplier's statement with our records: a copy of what was found, nothing more.
CREATE TABLE IF NOT EXISTS supplier_statement_checks (
    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL REFERENCES creditors(creditor_id),
    statement_date DATE,                                            -- the date on their paper
    their_total REAL,                                               -- the total printed on their paper
    found_total REAL NOT NULL DEFAULT 0,                            -- what was ticked and typed
    ours_total REAL NOT NULL DEFAULT 0,                             -- what our records said we owed them then
    result TEXT NOT NULL,                                           -- the five groups, as shown (JSON)
    saved_by TEXT,
    saved_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
