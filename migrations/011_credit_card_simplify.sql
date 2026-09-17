-- Migration 011: credit card module re-scope
--
-- User: "for credit card, these are the only important [things]" -- Due date, amount due, whose
-- card it is, making sure all statements are recorded, and the annual fee waiver reminder date.
-- Cash2Go/Cash-It-Easy cash conversions were confirmed to be dropped (CLAUDE.md already flagged
-- this as should-have-been-dropped; the live code just hadn't caught up).
--
-- New `cardholders` table -- "there is a need of credit card holder registry": the app only
-- tracked which bank issued a card, not who inside the company holds/uses it. `credit_cards`
-- gains `cardholder_id` (nullable FK -- existing cards need to be backfilled, not forced) and
-- `annual_fee_due_month`/`annual_fee_due_day` (nullable INTEGER -- a single recurring date, not
-- a per-year record, per explicit choice: "One recurring annual date").
--
-- `cc_cash_conversions` dropped entirely (table rebuild not needed for this one, plain DROP is
-- sufficient since nothing else references it). `credit_cards` gets a full rebuild since SQLite
-- can't add a FOREIGN KEY constraint via ALTER TABLE. Applied with foreign_keys off,
-- pre-migration backup taken, verified after with PRAGMA foreign_key_check and a row count.
--
-- Real gotcha hit while applying this: dropping `credit_cards` (with `v_cc_due_alerts` still
-- referencing it) failed with "no such table: main.credit_cards" -- SQLite's default
-- (non-legacy) ALTER TABLE eagerly re-validates every view/trigger in the schema whenever a
-- table is renamed, even a view that has nothing to do with the table actually being renamed,
-- and fails if that view momentarily points at a table that doesn't exist yet mid-migration.
-- Fixed by setting `PRAGMA legacy_alter_table = ON` before the final RENAME, which skips that
-- eager dependent-object validation (safe here since the view already references the correct
-- final name "credit_cards", not the temporary "_new" name, so no rewrite was ever needed).

CREATE TABLE cardholders (
    cardholder_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    department TEXT,
    contact_number TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE credit_cards_new (
    card_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    cardholder_id INTEGER,
    card_name TEXT NOT NULL,
    card_number_last4 TEXT NOT NULL,
    credit_limit REAL NOT NULL CHECK(credit_limit > 0),
    annual_fee_due_month INTEGER CHECK(annual_fee_due_month BETWEEN 1 AND 12),
    annual_fee_due_day INTEGER CHECK(annual_fee_due_day BETWEEN 1 AND 31),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (cardholder_id) REFERENCES cardholders(cardholder_id)
);

INSERT INTO credit_cards_new (card_id, creditor_id, cardholder_id, card_name, card_number_last4, credit_limit, annual_fee_due_month, annual_fee_due_day, created_at)
SELECT card_id, creditor_id, NULL, card_name, card_number_last4, credit_limit, NULL, NULL, created_at
FROM credit_cards;

DROP TABLE cc_cash_conversions;
DROP TABLE credit_cards;
PRAGMA legacy_alter_table = ON;
ALTER TABLE credit_cards_new RENAME TO credit_cards;
PRAGMA legacy_alter_table = OFF;
