-- SALES INVOICE AS THE REFERENCE (owner, 2026-10-03: "before we record delivery receipt as reference but now i want a sales invoice").
-- invoices.doc_ref is the supplier's SALES INVOICE number (SI) from now on; the delivery receipt number, when there is one, is kept beside it here.
-- Applied by update_database.bat (schema.sql carries the column; this file is the record).
ALTER TABLE invoices ADD COLUMN dr_no TEXT;
