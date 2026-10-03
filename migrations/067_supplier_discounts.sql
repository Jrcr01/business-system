-- PURCHASING (2026-10-02). Applied by update_database.bat (schema.sql carries the tables and columns; this file is the record).
-- Supplier price lists show LIST prices; a supplier gives a chain of discounts ("less 5%, less 5%") per brand. A PO line keeps the list price, the chain and the NET price.
CREATE TABLE IF NOT EXISTS supplier_discounts (
    discount_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    brand TEXT NOT NULL DEFAULT '',                                 -- '' = every brand of this supplier
    chain TEXT NOT NULL DEFAULT '',                                 -- '5,5' = less 5%, then less 5% of what is left; '' = no discount from this date
    effective_date DATE NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (creditor_id, brand, effective_date),
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id)
);
CREATE INDEX IF NOT EXISTS idx_supplier_discounts_lookup ON supplier_discounts (creditor_id, brand, effective_date);
ALTER TABLE purchase_order_lines ADD COLUMN list_price REAL CHECK(list_price IS NULL OR list_price >= 0);
ALTER TABLE purchase_order_lines ADD COLUMN discount_chain TEXT;
