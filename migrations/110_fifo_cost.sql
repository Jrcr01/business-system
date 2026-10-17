ALTER TABLE items ADD COLUMN next_cost REAL;

-- FIFO COST (migration 110, owner 2026-10-17: "Yes by delivery date"; mock-up al_fifo_cost.html). Every delivery is a batch; pieces leave oldest first.
CREATE TABLE IF NOT EXISTS stock_layers (
    layer_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    received_on DATE NOT NULL,                                      -- the delivery date: the FIFO order
    prio INTEGER NOT NULL DEFAULT 1,                                -- 0 = pieces returned, placed first so they sell next
    qty_in REAL NOT NULL,
    qty_left REAL NOT NULL,
    unit_cost REAL NOT NULL DEFAULT 0,
    est INTEGER NOT NULL DEFAULT 0,                                 -- 1 = still at the order's net price, waiting for the supplier's invoice
    ref_type TEXT,
    ref_id INTEGER,
    source TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_stock_layers_item ON stock_layers (item_id, prio, received_on, layer_id);
CREATE INDEX IF NOT EXISTS idx_stock_layers_ref ON stock_layers (ref_type, ref_id);
CREATE TABLE IF NOT EXISTS stock_layer_uses (
    use_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES items(id),
    layer_id INTEGER REFERENCES stock_layers(layer_id),             -- NULL = sold before its delivery was recorded: at the last price until the delivery comes
    qty REAL NOT NULL,
    unit_cost REAL NOT NULL DEFAULT 0,
    ref_type TEXT,
    ref_id INTEGER,
    move_id INTEGER,                                                -- the stock_ledger row
    used_on DATE,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_stock_layer_uses_layer ON stock_layer_uses (layer_id);
CREATE INDEX IF NOT EXISTS idx_stock_layer_uses_ref ON stock_layer_uses (ref_type, ref_id, item_id);
CREATE INDEX IF NOT EXISTS idx_stock_layer_uses_pending ON stock_layer_uses (item_id) WHERE layer_id IS NULL;
