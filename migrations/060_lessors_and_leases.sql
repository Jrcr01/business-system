-- LESSOR TRACK (2026-10-01). Applied by update_database.bat (schema.sql carries the columns; this file is the record).
-- A lessor is a creditor we pay rent to; a lease is a loan note with no interest whose schedule is the rent checks. Both are flags on existing tables, so checks, schedules,
-- printing, numbering and bank matching all work unchanged. See engines/lessor_service.py.
ALTER TABLE creditors ADD COLUMN is_lessor INTEGER NOT NULL DEFAULT 0 CHECK(is_lessor IN (0, 1));
ALTER TABLE loan_notes ADD COLUMN is_lease INTEGER NOT NULL DEFAULT 0 CHECK(is_lease IN (0, 1));
ALTER TABLE loan_notes ADD COLUMN lease_description TEXT;
