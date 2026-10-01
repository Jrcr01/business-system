-- PURCHASING, stage B3 (2026-10-01). Applied by update_database.bat (schema.sql carries the columns; this file is the record).
-- An order placed over the cash plan is flagged for the owner (who is told, not asked).
ALTER TABLE purchase_orders ADD COLUMN over_cash_plan INTEGER NOT NULL DEFAULT 0 CHECK(over_cash_plan IN (0, 1));
ALTER TABLE purchase_orders ADD COLUMN cash_note TEXT;
ALTER TABLE purchase_orders ADD COLUMN cash_ack_at DATETIME;
