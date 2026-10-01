-- PURCHASING (2026-10-01). Applied by update_database.bat (schema.sql carries the tables; this file is the record). New tables only; nothing existing changes.
-- PURCHASING (migration 061, 2026-10-01; see the plan "Cash disbursement cycle: order to pay"). Supplier price lists, purchase orders and what was
-- received, so an order shows in the cash forecast on its expected payment date BEFORE the supplier's invoice exists. The price list gives the ESTIMATE
-- for a PO line; when the goods and the supplier's invoice arrive, the ACTUAL price prevails (purchase_order_lines.actual_*, po_receipts.actual_amount).
CREATE TABLE IF NOT EXISTS supplier_price_list (
    price_id INTEGER PRIMARY KEY AUTOINCREMENT,
    creditor_id INTEGER NOT NULL,
    item_code TEXT,
    item_name TEXT NOT NULL,
    unit TEXT,
    unit_price REAL NOT NULL CHECK(unit_price >= 0),
    effective_date DATE NOT NULL,                                   -- the price applies from this date until a later row for the same item
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (creditor_id, item_name, effective_date),
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id)
);
CREATE INDEX IF NOT EXISTS idx_price_list_lookup ON supplier_price_list (creditor_id, item_name, effective_date);

CREATE TABLE IF NOT EXISTS purchase_orders (
    po_id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_no TEXT NOT NULL UNIQUE,                                     -- DRAFT-PO-... until first ordered, then "YYYY - PO NNNN" (doc_counters series 'PO')
    creditor_id INTEGER NOT NULL,
    order_date DATE NOT NULL,
    expected_delivery_date DATE,
    terms_snapshot TEXT,                                            -- the supplier's terms when the order was made
    expected_pay_date DATE,                                         -- delivery date + terms, moved to a bank day: when the check will be due
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'ORDERED', 'PARTIAL', 'RECEIVED', 'CANCELLED')),
    notes TEXT,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (creditor_id) REFERENCES creditors(creditor_id)
);
CREATE INDEX IF NOT EXISTS idx_po_creditor_status ON purchase_orders (creditor_id, status);

CREATE TABLE IF NOT EXISTS purchase_order_lines (
    line_id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id INTEGER NOT NULL,
    price_id INTEGER,                                               -- the price-list row the estimate came from (none for a typed price)
    item_name TEXT NOT NULL,
    unit TEXT,
    qty REAL NOT NULL CHECK(qty > 0),
    est_unit_price REAL NOT NULL DEFAULT 0 CHECK(est_unit_price >= 0),
    est_amount REAL NOT NULL DEFAULT 0 CHECK(est_amount >= 0),
    qty_received REAL NOT NULL DEFAULT 0 CHECK(qty_received >= 0),
    actual_unit_price REAL CHECK(actual_unit_price IS NULL OR actual_unit_price >= 0),
    actual_amount REAL CHECK(actual_amount IS NULL OR actual_amount >= 0),
    FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id),
    FOREIGN KEY (price_id) REFERENCES supplier_price_list(price_id)
);
CREATE INDEX IF NOT EXISTS idx_po_lines_po ON purchase_order_lines (po_id);

CREATE TABLE IF NOT EXISTS po_receipts (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    po_id INTEGER NOT NULL,
    received_date DATE NOT NULL,
    supplier_invoice_no TEXT,
    supplier_invoice_date DATE,
    actual_amount REAL NOT NULL CHECK(actual_amount >= 0),
    invoice_id INTEGER,                                             -- the AP invoice this receipt created or matched
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (po_id) REFERENCES purchase_orders(po_id),
    FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id)
);
CREATE INDEX IF NOT EXISTS idx_po_receipts_po ON po_receipts (po_id);
