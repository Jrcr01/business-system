-- BEGINNING-BALANCE LABEL ON MIGRATED LOANS (2026-10-01). Applied by update_database.bat (schema.sql carries the columns; this file is the record).

-- Loan notes the MS Access import left as placeholders (PENDING-...) and bank loans imported as a schedule only are tagged
-- "Beginning balance (from MS Access)": their interest / terms are not split out yet. The label goes away when real terms are entered or by hand;
-- beginning_label_removed makes sure a removed label is never put back. See engines/beginning_balance_service.py.
ALTER TABLE loan_notes ADD COLUMN is_beginning_balance INTEGER NOT NULL DEFAULT 0;
ALTER TABLE loan_notes ADD COLUMN beginning_label_removed INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bank_loans ADD COLUMN is_beginning_balance INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bank_loans ADD COLUMN beginning_label_removed INTEGER NOT NULL DEFAULT 0;
