-- Migration 032: Phase 8a (Master Data Setup) -- loan_notes placeholder support
--
-- The Access migration surfaces 21 real-world lenders with no principal/rate/schedule data yet
-- (that lives in paper notes the user has to dig up and enter later) -- "Pending Setup" needs to
-- be representable without inventing numbers. principal_amount/total_payable were NOT NULL (the
-- former also CHECK > 0) and status's CHECK didn't include 'Pending Setup' since the original
-- schema; SQLite can't drop/alter either via ALTER TABLE, so this is a full rebuild, same
-- pattern as migration 021's checks_issued rebuild. monthly_interest_rate goes from
-- "NOT NULL DEFAULT 0" to nullable too, so a placeholder note doesn't silently imply a 0% rate.
--
-- PRAGMA foreign_keys = OFF around the drop/rename: checks_issued.loan_note_id and
-- loan_installments.loan_note_id both FK into this table (same gotcha migration 018/021 hit for
-- checks_issued). PRAGMA legacy_alter_table = ON around the RENAME in case anything references
-- loan_notes by name in a view (none currently do, but kept for consistency with prior rebuilds).

PRAGMA foreign_keys = OFF;

CREATE TABLE loan_notes_new (
    loan_note_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    note_ref TEXT UNIQUE NOT NULL,
    issue_date DATE NOT NULL,
    principal_amount REAL CHECK(principal_amount IS NULL OR principal_amount > 0),
    monthly_interest_rate REAL,
    total_interest REAL DEFAULT 0.0,
    total_payable REAL,
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Settled', 'Defaulted', 'Cancelled', 'Pending Setup')),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id)
);

INSERT INTO loan_notes_new
SELECT loan_note_id, creditor_id, note_ref, issue_date, principal_amount, monthly_interest_rate,
       total_interest, total_payable, status, created_at
FROM loan_notes;

DROP TABLE loan_notes;
PRAGMA legacy_alter_table = ON;
ALTER TABLE loan_notes_new RENAME TO loan_notes;
PRAGMA legacy_alter_table = OFF;

PRAGMA foreign_keys = ON;
