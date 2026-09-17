-- Migration 018: enforce "every check belongs to a batch"
--
-- CLAUDE.md, Core System Configuration: "Every check must belong to a batch -- no exceptions...
-- This applies to Encashment too." This was a documented rule enforced only by app.py's own
-- discipline (issue_check()/issue_checks_grid()/replace_check() always pass one of statement_id
-- or loan_note_id), not by the schema -- a real integrity gap in a system whose whole design
-- philosophy is "no standalone checks, that was the legacy mistake." Confirmed zero standalone
-- rows exist in the live DB before adding this (checks_issued WHERE statement_id IS NULL AND
-- loan_note_id IS NULL -> 0 rows), so the constraint costs nothing today and only gets more
-- disruptive to retrofit the longer it's deferred.
--
-- checks_issued has two valid "batch" parents depending on track (statement_id for Supplier,
-- loan_note_id for Lender) -- a single-column NOT NULL doesn't fit this shape, so this is a CHECK
-- requiring at least one of the two to be set. Bank Loan and Credit Card tracks never write to
-- checks_issued at all (both are untracked-cash settlement per their own phase scoping), so
-- nothing about those tracks is affected.
--
-- Full table rebuild (SQLite can't add a CHECK constraint via ALTER TABLE). Same
-- eager-view-validation gotcha as migration 011 (v_effective_checks references checks_issued
-- directly by name) -- PRAGMA legacy_alter_table = ON around the final rename avoids it.

CREATE TABLE checks_issued_new (
    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    bank_account_id INTEGER NOT NULL,
    statement_id INTEGER,
    loan_note_id INTEGER,
    check_number TEXT NOT NULL,
    check_date DATE NOT NULL,
    amount REAL NOT NULL CHECK(amount >= 0),
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Cleared', 'Cancelled', 'Replaced', 'Spoiled')),
    replacement_check_id INTEGER,
    replacement_reason TEXT,
    replaced_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(bank_account_id),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id),
    FOREIGN KEY (loan_note_id) REFERENCES loan_notes(loan_note_id),
    CHECK (statement_id IS NOT NULL OR loan_note_id IS NOT NULL)
);

INSERT INTO checks_issued_new
SELECT check_id, creditor_id, bank_account_id, statement_id, loan_note_id, check_number, check_date,
       amount, status, replacement_check_id, replacement_reason, replaced_at, created_at
FROM checks_issued;

DROP TABLE checks_issued;
PRAGMA legacy_alter_table = ON;
ALTER TABLE checks_issued_new RENAME TO checks_issued;
PRAGMA legacy_alter_table = OFF;
