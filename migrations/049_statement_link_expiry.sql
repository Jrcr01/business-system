-- THERMAL STATEMENT QR LINK EXPIRY (2026-09-27, owner): the public A4 link stops working 3 months after the batch's last check date
-- (a batch with no checks yet: its last invoice due date + 3 months). Google cannot put an end date on an anyone-with-link share, so
-- the app switches it off itself (nightly after the backup, and once a day when Payment run opens). A reprint after expiry re-opens the
-- same file for 3 more months from the reprint day. Additive only; applied by update_database.bat (schema.sql); this file is the record.
ALTER TABLE statement_versions ADD COLUMN public_until DATE;     -- the last computed expiry (display + audit); recomputed every sweep
ALTER TABLE statement_versions ADD COLUMN reopened_until DATE;   -- set when a reprint re-opened an expired link (reprint day + 3 months)
