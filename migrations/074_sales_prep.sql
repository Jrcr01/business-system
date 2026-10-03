-- PREPARING FOR SELL (2026-10-03). Applied by update_database.bat (schema.sql carries the tables and the column; this file is the record).
-- Nothing here has a screen yet: they are the places the Sales / receivables and Stock modules will plug into, so they do not reshape the database later.
--   customers      who we sell to (same shape as creditors: a name, terms, active), kept apart from suppliers and lenders
--   item_prices    what we SELL an item for, dated, apart from the supplier cost lists (cost and selling prices never mix)
--   bank_transactions.deposit_kind   a deposit on the bank statement tagged as 'sales' (or 'other'), so real deposits can replace the forecast's guess when Sales arrives
CREATE TABLE IF NOT EXISTS customers (
    customer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_name TEXT NOT NULL UNIQUE,
    customer_type TEXT NOT NULL DEFAULT 'RETAIL' CHECK(customer_type IN ('RETAIL', 'FLEET', 'DEALER', 'OTHER')),
    terms TEXT,
    contact_person TEXT,
    contact_number TEXT,
    email TEXT,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT
);
CREATE TABLE IF NOT EXISTS item_prices (
    price_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    price REAL NOT NULL CHECK(price >= 0),
    effective_date DATE NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    UNIQUE (item_id, effective_date)
);
ALTER TABLE bank_transactions ADD COLUMN deposit_kind TEXT CHECK(deposit_kind IS NULL OR deposit_kind IN ('sales', 'other'));
