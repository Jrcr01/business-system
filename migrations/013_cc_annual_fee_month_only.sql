-- Migration 013: drop annual_fee_due_day from credit_cards, keep month only
--
-- User: "remove annual fee day becuasr our bank billed us annual fee every anniversary of tje
-- card" -- the exact day is the card's original issuance date, which the user doesn't have on
-- hand ("i cannot give the issued date"). Rather than track a day that can't reliably be
-- supplied, the reminder drops to month-level precision only: "annual fee due sometime in
-- March," accurate to what's actually knowable. Full table rebuild since SQLite can't drop a
-- column with a CHECK constraint via ALTER TABLE; applied with foreign_keys off, pre-migration
-- backup taken, verified after with PRAGMA foreign_key_check and a row count. No live data lost
-- -- annual_fee_due_month/day were both still NULL on every existing card at migration time.

CREATE TABLE credit_cards_new (
    card_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    cardholder_id INTEGER,
    card_name TEXT NOT NULL,
    card_number_last4 TEXT NOT NULL,
    credit_limit REAL NOT NULL CHECK(credit_limit > 0),
    annual_fee_due_month INTEGER CHECK(annual_fee_due_month BETWEEN 1 AND 12),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (cardholder_id) REFERENCES cardholders(cardholder_id)
);

INSERT INTO credit_cards_new (card_id, creditor_id, cardholder_id, card_name, card_number_last4, credit_limit, annual_fee_due_month, created_at)
SELECT card_id, creditor_id, cardholder_id, card_name, card_number_last4, credit_limit, annual_fee_due_month, created_at
FROM credit_cards;

DROP TABLE credit_cards;
PRAGMA legacy_alter_table = ON;
ALTER TABLE credit_cards_new RENAME TO credit_cards;
PRAGMA legacy_alter_table = OFF;
