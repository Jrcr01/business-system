-- PURCHASING (2026-10-02). Applied by update_database.bat (schema.sql carries the column; this file is the record).
-- Receiving is two steps: Purchasing records the goods that arrived (qty_received); Payables later matches the supplier invoice into a batch (qty_invoiced).
-- NULL qty_invoiced means "everything received is invoiced" (every order received before this change), so no data needs rewriting.
ALTER TABLE purchase_order_lines ADD COLUMN qty_invoiced REAL CHECK(qty_invoiced IS NULL OR qty_invoiced >= 0);
