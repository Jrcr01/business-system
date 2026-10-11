-- RECEIVABLES AND COLLECTIONS, stage B part 1 (owner, 2026-10-11: "the important to us is receivables can be track, any payments thereafter,
-- BIR CR is optional"). A charge sale is the receivable; a collection pays invoices oldest first, partial fine, the extra stays as the
-- customer's advance; the receipt number is optional; a write-off is the System admin's, lifted by a later payment.
ALTER TABLE sales ADD COLUMN is_opening INTEGER NOT NULL DEFAULT 0 CHECK(is_opening IN (0, 1));
ALTER TABLE sales ADD COLUMN white_copy_at DATETIME;
ALTER TABLE sales ADD COLUMN written_off_at DATETIME;
ALTER TABLE sales ADD COLUMN write_off_reason TEXT;
CREATE TABLE IF NOT EXISTS collections (
    collection_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cr_no TEXT,                                      -- optional: the paper receipt's number when one was written
    customer_id INTEGER NOT NULL REFERENCES customers(customer_id),
    collect_date DATE NOT NULL,
    received REAL NOT NULL DEFAULT 0,                -- money that came in (cash, checks, transfer, card)
    allocated REAL NOT NULL DEFAULT 0,               -- what was put on invoices
    advance_used REAL NOT NULL DEFAULT 0,            -- paid from the customer's advance; received - allocated accumulates it
    note TEXT,
    void_reason TEXT,
    voided_at DATETIME,
    voided_by TEXT,
    created_by TEXT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_collections_customer ON collections (customer_id);
CREATE TABLE IF NOT EXISTS collection_lines (
    line_id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES collections(collection_id),
    sale_id INTEGER NOT NULL REFERENCES sales(sale_id),
    amount REAL NOT NULL CHECK(amount > 0)
);
CREATE INDEX IF NOT EXISTS idx_collection_lines_sale ON collection_lines (sale_id);
CREATE TABLE IF NOT EXISTS collection_payments (
    payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    collection_id INTEGER NOT NULL REFERENCES collections(collection_id),
    method TEXT NOT NULL CHECK(method IN ('CASH', 'CHECK', 'TRANSFER', 'CARD', 'ADVANCE')),
    amount REAL NOT NULL CHECK(amount > 0),
    bank TEXT,
    check_no TEXT,
    check_date DATE,                                 -- a post-dated check waits here for stage B part 2 (deposits)
    reference TEXT
);
CREATE INDEX IF NOT EXISTS idx_collection_payments_k ON collection_payments (collection_id);
