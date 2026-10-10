-- ONE ORDER, MANY DRs, AND THE DR IS THE PAYABLE (owner, 2026-10-11: "our suppliers dont give SI just DR then that only binds us"; "we have instances
-- our suppliers make many DR to fulfill orders"). Each delivery keeps WHAT came on it, so the order's page can list its deliveries, a document can be
-- recorded for one DR, and a delivery recorded by mistake can be undone.
CREATE TABLE IF NOT EXISTS po_receipt_lines (
    receipt_line_id INTEGER PRIMARY KEY AUTOINCREMENT,
    receipt_id INTEGER NOT NULL REFERENCES po_receipts(receipt_id),
    line_id INTEGER NOT NULL REFERENCES purchase_order_lines(line_id),
    qty REAL NOT NULL CHECK(qty > 0),
    unit_price REAL NOT NULL DEFAULT 0,          -- the price the goods were counted at: the order's net price, or the price on the SI / DR once recorded
    amount REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_po_receipt_lines_receipt ON po_receipt_lines (receipt_id);
CREATE INDEX IF NOT EXISTS idx_po_receipt_lines_line ON po_receipt_lines (line_id);
ALTER TABLE purchase_order_lines ADD COLUMN added_at_delivery INTEGER NOT NULL DEFAULT 0;   -- 1 = an item that came but was not on the agreed order (flagged for review)
