-- Credit card payments actually made (2026-09-21).
CREATE TABLE IF NOT EXISTS cc_payments (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cc_statement_id INTEGER NOT NULL,
    card_id INTEGER NOT NULL,
    payment_date DATE NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0),
    method TEXT,
    note TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (cc_statement_id) REFERENCES cc_statements(cc_statement_id),
    FOREIGN KEY (card_id) REFERENCES credit_cards(card_id)
);
