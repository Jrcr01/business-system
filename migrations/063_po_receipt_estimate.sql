-- PURCHASING, stage B4 (2026-10-02). Applied by update_database.bat (schema.sql carries the column; this file is the record).
ALTER TABLE po_receipts ADD COLUMN est_amount REAL NOT NULL DEFAULT 0;
