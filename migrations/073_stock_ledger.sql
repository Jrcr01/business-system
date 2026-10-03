-- STOCK LEDGER (2026-10-03). Applied by update_database.bat (schema.sql carries the table; this file is the record).
-- Stock as a ledger, not a number: every movement is one row, and items.on_hand / items.avg_cost follow from the rows. Receiving goods writes IN at the order's net price; matching
-- the supplier's invoice corrects the cost to what was actually charged. (Named stock_ledger so it never clashes with the parked Django site's own inventory_stockmove.)
CREATE TABLE IF NOT EXISTS stock_ledger (
    move_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    kind TEXT NOT NULL CHECK(kind IN ('IN', 'OUT', 'ADJ', 'RETURN', 'COST')),   -- COST = the cost corrected to the invoice price (qty 0 here, the pieces already counted)
    qty REAL NOT NULL,                                                          -- + in, - out
    unit_cost REAL NOT NULL DEFAULT 0,
    on_hand_after REAL NOT NULL,
    avg_after REAL NOT NULL,
    ref_type TEXT,
    ref_id INTEGER,
    moved_on DATE NOT NULL,
    note TEXT,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_stock_ledger_item ON stock_ledger (item_id, move_id);
