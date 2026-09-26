-- When an export run was actually printed in Chrysanth (2026-09-27). Chrysanth does not remember what it has printed, so the
-- app does: NULL = exported but not marked printed yet.
ALTER TABLE check_print_batches ADD COLUMN printed_confirmed_at DATETIME;
