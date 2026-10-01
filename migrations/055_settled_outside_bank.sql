-- SETTLED OUTSIDE THE BANK (2026-10-01). Applied by update_database.bat (schema.sql carries the columns; this file is the record).

-- A check the payee gave back and was paid in cash instead (owner, 2026-09-30) will never clear the bank. The check stays on file, keeps counting
-- toward its batch / loan note, and is NEVER marked cleared by the bank (the bank statement stays the truth) -- it just stops being money still
-- leaving the bank (not-yet-cleared list, cash forecast, float, stale checks, payment schedule). Undo = clear the three columns.
ALTER TABLE checks_issued ADD COLUMN settled_outside_at DATE;
ALTER TABLE checks_issued ADD COLUMN settled_outside_reason TEXT;
ALTER TABLE checks_issued ADD COLUMN settled_outside_note TEXT;
