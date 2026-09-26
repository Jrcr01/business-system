-- Check number CRUD-audit hardening (2026-09-17): a real DB-level backstop against two checks
-- ending up with the same number on the same bank account -- previously only enforced in app
-- code (_is_check_number_available()), checked inconsistently across issuance/correction/booklet
-- assignment paths. NULL (a parked, not-yet-printed check) is exempt since SQLite treats every
-- NULL as distinct under a UNIQUE index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_checks_issued_unique_number ON checks_issued(bank_account_id, check_number);
