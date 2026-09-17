-- Per-invoice check allocation (2026-09-14): which specific invoice(s) a check pays, and how
-- much of each -- closes the gap where a batch's checks only ever paid the batch's net total,
-- with no way to say which invoice a given check actually covered. Many-to-many by design: one
-- check can pay several invoices, and one invoice can be paid across several checks (both
-- explicitly requested). Going-forward only -- no backfill for checks issued before this table
-- existed; sync_invoice_payment_status() (app.py) falls back to the old batch-level blanket
-- status for any batch that has never had an allocated check.
CREATE TABLE IF NOT EXISTS invoice_check_allocations (
    allocation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    check_id INTEGER NOT NULL,
    amount REAL NOT NULL CHECK(amount > 0),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id),
    FOREIGN KEY (check_id) REFERENCES checks_issued(check_id)
);
