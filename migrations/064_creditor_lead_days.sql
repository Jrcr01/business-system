-- PURCHASING (2026-10-02). Applied by update_database.bat (schema.sql carries the column; this file is the record).
ALTER TABLE creditors ADD COLUMN lead_days INTEGER CHECK(lead_days IS NULL OR (lead_days >= 0 AND lead_days <= 365));
