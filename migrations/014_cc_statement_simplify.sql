-- Migration 014: drop statement_ref and statement_date from cc_statements; add statement_day
-- to credit_cards
--
-- User: "remove statement reference; no need for this" -- never used for anything beyond
-- typing a value in, unlike order_statements.statement_ref (used for search/promote/lock
-- elsewhere). Then: "also remove statement date (make this a permanent information of the card
-- i'll just provide the nth day every month)" -- the day a card's statement generates doesn't
-- vary month to month, so it belongs on the card (credit_cards.statement_day, alongside
-- annual_fee_due_month), not re-typed on every single statement. cc_statements now only carries
-- what actually varies per cycle: due_date and the amount due (matches the user's original
-- "only important" list -- due date, amount, whose card).
--
-- get_statement_gap_alerts() (engines/cc_service.py) switches its recency signal from
-- MAX(statement_date) to MAX(due_date), the only per-row date left.
--
-- Full rebuild of both tables (SQLite can't drop/add columns with constraints via ALTER TABLE);
-- applied with foreign_keys off, pre-migration backup taken, verified after with
-- PRAGMA foreign_key_check and a row count. statement_ref/statement_date values are dropped,
-- not preserved elsewhere -- confirmed with the user this data isn't needed.

CREATE TABLE credit_cards_new (
    card_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    cardholder_id INTEGER,
    card_name TEXT NOT NULL,
    card_number_last4 TEXT NOT NULL,
    credit_limit REAL NOT NULL CHECK(credit_limit > 0),
    annual_fee_due_month INTEGER CHECK(annual_fee_due_month BETWEEN 1 AND 12),
    statement_day INTEGER CHECK(statement_day BETWEEN 1 AND 31),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (cardholder_id) REFERENCES cardholders(cardholder_id)
);

INSERT INTO credit_cards_new (card_id, creditor_id, cardholder_id, card_name, card_number_last4, credit_limit, annual_fee_due_month, statement_day, created_at)
SELECT card_id, creditor_id, cardholder_id, card_name, card_number_last4, credit_limit, annual_fee_due_month, NULL, created_at
FROM credit_cards;

CREATE TABLE cc_statements_new (
    cc_statement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL,
    due_date DATE NOT NULL,
    total_amount_due REAL NOT NULL CHECK(total_amount_due >= 0),
    status TEXT NOT NULL DEFAULT 'Unpaid' CHECK(status IN ('Unpaid', 'Partially Paid', 'Paid', 'Cancelled')),
    payment_method TEXT DEFAULT 'Cash / Online' CHECK(payment_method IN ('Cash / Online', 'Direct Debit', 'Check')),
    payment_date DATE,
    payment_check_id INTEGER,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (card_id) REFERENCES credit_cards(card_id),
    FOREIGN KEY (payment_check_id) REFERENCES checks_issued(check_id)
);

INSERT INTO cc_statements_new (cc_statement_id, card_id, due_date, total_amount_due, status, payment_method, payment_date, payment_check_id, created_at)
SELECT cc_statement_id, card_id, due_date, total_amount_due, status, payment_method, payment_date, payment_check_id, created_at
FROM cc_statements;

DROP TABLE cc_statements;
DROP TABLE credit_cards;
PRAGMA legacy_alter_table = ON;
ALTER TABLE credit_cards_new RENAME TO credit_cards;
ALTER TABLE cc_statements_new RENAME TO cc_statements;
PRAGMA legacy_alter_table = OFF;

DROP VIEW IF EXISTS v_cc_due_alerts;

CREATE VIEW v_cc_due_alerts AS
SELECT
    c.creditor_name AS bank_issuer,
    cc.card_name,
    cc.card_number_last4,
    cs.cc_statement_id,
    cs.due_date,
    cs.total_amount_due,
    CAST(JULIANDAY(cs.due_date) - JULIANDAY('now', 'localtime') AS INTEGER) AS days_until_due,
    CASE
        WHEN JULIANDAY(cs.due_date) - JULIANDAY('now', 'localtime') <= 3 THEN 'CRITICAL (0-3 Days)'
        WHEN JULIANDAY(cs.due_date) - JULIANDAY('now', 'localtime') <= 7 THEN 'WARNING (4-7 Days)'
        ELSE 'UPCOMING'
    END AS urgency_status
FROM cc_statements cs
JOIN credit_cards cc ON cs.card_id = cc.card_id
JOIN creditors c ON cc.creditor_id = c.creditor_id
WHERE cs.status = 'Unpaid' AND cs.due_date >= DATE('now', 'localtime')
ORDER BY cs.due_date ASC;
