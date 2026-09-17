-- Migration 021: Phase 6 Redux -- check_number becomes nullable
--
-- Design shift confirmed for this phase: a check number is assigned at PRINT time, not at
-- issue/park time. A parked check (recorded in checks_issued, printed_at IS NULL) genuinely has
-- no check number yet -- the physical booklet leaf it will end up on isn't decided until it's
-- actually printed (auto-sequenced from a booklet, or manually assigned during a backfill).
-- check_number NOT NULL (present since the original schema, unchanged through migration 018)
-- must be dropped for that to be representable at all.
--
-- Full table rebuild (SQLite can't drop a NOT NULL constraint via ALTER TABLE). Same shape as
-- migration 018's rebuild of this same table -- carries every column/constraint added since
-- (including printed_at from migration 020) unchanged, only check_number's NOT NULL is removed.
-- Applied with PRAGMA foreign_keys = OFF around the DROP (loan_installments.check_id and
-- check_corrections.check_id both FK into this table -- migration 018 hit and documented this
-- exact gotcha) and PRAGMA legacy_alter_table = ON around the RENAME (v_effective_checks
-- references checks_issued by name -- migration 011/018's eager-view-validation gotcha).

CREATE TABLE checks_issued_new (
    check_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    bank_account_id INTEGER NOT NULL,
    statement_id INTEGER,
    loan_note_id INTEGER,
    check_number TEXT,
    check_date DATE NOT NULL,
    amount REAL NOT NULL CHECK(amount >= 0),
    status TEXT NOT NULL DEFAULT 'Active' CHECK(status IN ('Active', 'Cleared', 'Cancelled', 'Replaced', 'Spoiled')),
    replacement_check_id INTEGER,
    replacement_reason TEXT,
    replaced_at DATETIME,
    printed_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (bank_account_id) REFERENCES bank_accounts(bank_account_id),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id),
    FOREIGN KEY (loan_note_id) REFERENCES loan_notes(loan_note_id),
    CHECK (statement_id IS NOT NULL OR loan_note_id IS NOT NULL)
);

INSERT INTO checks_issued_new
SELECT check_id, creditor_id, bank_account_id, statement_id, loan_note_id, check_number, check_date,
       amount, status, replacement_check_id, replacement_reason, replaced_at, printed_at, created_at
FROM checks_issued;

DROP TABLE checks_issued;
PRAGMA legacy_alter_table = ON;
ALTER TABLE checks_issued_new RENAME TO checks_issued;
PRAGMA legacy_alter_table = OFF;
