-- PURCHASING (2026-10-02). Applied by update_database.bat (schema.sql carries the tables and columns; this file is the record).
-- Replaces the earlier 068/069 drafts, which only ever existed on the sandbox copy.
-- The ITEM MASTER: one row per product (Tires, Battery, Mags, Rims) with our own code, the category's standard fields in specs, the running stock and average cost,
-- shared by price lists, purchase orders, stock and sales. supplier_items remembers each supplier's own code for an item.
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL CHECK(category IN ('tire', 'battery', 'mags', 'rims')),
    brand TEXT NOT NULL,
    size_model TEXT NOT NULL,
    remark TEXT NOT NULL DEFAULT '',
    key TEXT NOT NULL UNIQUE,
    specs TEXT NOT NULL DEFAULT '{}',
    name TEXT NOT NULL DEFAULT '',
    unit TEXT,
    reorder_level INTEGER NOT NULL DEFAULT 2,
    on_hand INTEGER NOT NULL DEFAULT 0,
    avg_cost DECIMAL NOT NULL DEFAULT 0,
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS supplier_items (
    supplier_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    item_id INTEGER NOT NULL,
    supplier_code TEXT NOT NULL DEFAULT '',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (creditor_id, item_id),
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id),
    FOREIGN KEY (item_id) REFERENCES items(id)
);
ALTER TABLE supplier_price_list ADD COLUMN item_id INTEGER REFERENCES items(id);
CREATE INDEX IF NOT EXISTS idx_price_list_item ON supplier_price_list (item_id);
ALTER TABLE purchase_order_lines ADD COLUMN item_id INTEGER REFERENCES items(id);
