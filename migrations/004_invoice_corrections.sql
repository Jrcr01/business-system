-- Migration 004: invoice in-place edit audit log
-- Supports the "Invoice correction" workflow: edits to a saved invoice row must be
-- auto-logged old->new per changed field, with no manual reason field (unlike
-- check_corrections, which does take a reason -- invoice edits don't per spec).

CREATE TABLE IF NOT EXISTS invoice_corrections (
    correction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    invoice_id INTEGER NOT NULL,
    statement_id INTEGER,
    field_name TEXT NOT NULL,
    old_value TEXT,
    new_value TEXT,
    corrected_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (invoice_id) REFERENCES invoices(invoice_id),
    FOREIGN KEY (statement_id) REFERENCES order_statements(statement_id)
);
