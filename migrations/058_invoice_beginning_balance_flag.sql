-- BEGINNING BALANCE FLAG (2026-10-01). Applied by update_database.bat (schema.sql carries the column; this file is the record).

-- Batches brought over from MS Access hold checks but no invoices. Each gets ONE line "Beginning balance (from MS Access)" equal to its total (see
-- engines/beginning_balance_service.py). This flag marks that line so aging, days-to-pay, the overview's invoice count, the saved-report invoice list and
-- the duplicate / vendor-statement matchers leave it out, while balances and histories show it.
ALTER TABLE invoices ADD COLUMN is_opening_balance INTEGER NOT NULL DEFAULT 0;
