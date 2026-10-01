-- VOID REASON + DATE, HANDWRITTEN TAG (2026-10-01). Applied by update_database.bat (schema.sql carries the columns; this file is the record).

-- A void used to throw its reason away and leave no date on the check (only the audit log had a time). Now the check keeps both, and a
-- check written by hand outside the system (the owner's mother's urgent booklet) and recorded afterwards is tagged so it is never sent to
-- Chrysanth and shows in the registers. Voids made before this change have no cancelled_at: the register falls back to the audit log time.
ALTER TABLE checks_issued ADD COLUMN cancelled_at DATETIME;
ALTER TABLE checks_issued ADD COLUMN cancel_reason TEXT;
ALTER TABLE checks_issued ADD COLUMN is_handwritten INTEGER NOT NULL DEFAULT 0;
