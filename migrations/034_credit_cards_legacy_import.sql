-- Migration 034: Phase 8a (Master Data Setup) -- credit_cards legacy import support
--
-- 59 real cards surface from the Access migration (TblCreditCard) with no credit limit on file
-- (the old system never tracked it) -- credit_limit was NOT NULL CHECK(credit_limit > 0) since
-- the original schema, so a placeholder card genuinely can't state one without inventing it.
-- Same reasoning/pattern as migration 032 (loan_notes) and 033 (bank_loans): SQLite can't drop
-- NOT NULL/CHECK via ALTER TABLE, so this is a full rebuild. The app-level guards in
-- add_credit_card()/edit_credit_card() (app.py) still reject a zero/blank credit limit when a
-- *user* adds or edits a card through the UI -- that tested behavior is untouched; only direct
-- SQL inserts (this migration script) can leave it NULL.
--
-- New columns, all additive to the rebuild:
--   network            -- payment network (Visa/Mastercard/JCB/AMEX), not tracked before.
--   is_active          -- this specific card's active/closed flag from the legacy system
--                         (distinct from creditors.is_active, which is the issuing bank).
--   card_number_full   -- the complete card number. card_number_last4 stays the short
--                         display value used everywhere in the UI already; this is separate
--                         and additional, for the one real reference use it has: matching a
--                         card to its bill when paying through a banking app.
--   legacy_card_id     -- traceable link back to the old Access TblCreditCard id.
--
-- PRAGMA foreign_keys = OFF around the drop/rename: cc_statements.card_id FKs into this table.

PRAGMA foreign_keys = OFF;

CREATE TABLE credit_cards_new (
    card_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    cardholder_id INTEGER,
    card_name TEXT NOT NULL,
    card_number_last4 TEXT NOT NULL,
    card_number_full TEXT,
    network TEXT,
    credit_limit REAL CHECK(credit_limit IS NULL OR credit_limit > 0),
    annual_fee_due_month INTEGER CHECK(annual_fee_due_month BETWEEN 1 AND 12),
    statement_day INTEGER CHECK(statement_day BETWEEN 1 AND 31),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    legacy_card_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (cardholder_id) REFERENCES cardholders(cardholder_id)
);

INSERT INTO credit_cards_new (card_id, creditor_id, cardholder_id, card_name, card_number_last4,
    credit_limit, annual_fee_due_month, statement_day, created_at)
SELECT card_id, creditor_id, cardholder_id, card_name, card_number_last4,
       credit_limit, annual_fee_due_month, statement_day, created_at
FROM credit_cards;

DROP TABLE credit_cards;
PRAGMA legacy_alter_table = ON;
ALTER TABLE credit_cards_new RENAME TO credit_cards;
PRAGMA legacy_alter_table = OFF;

PRAGMA foreign_keys = ON;
