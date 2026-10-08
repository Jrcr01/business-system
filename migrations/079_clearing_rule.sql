-- THE CLEARING RULE (owner, 2026-10-08): a check is paid only when it is cleared -- by the bank (bank_cleared_date), settled outside the bank
-- (settled_outside_at), or ticked cleared in the system (manual_cleared_at). Its date passing no longer counts. Applied by apply_pending_migrations.py
-- (schema.sql carries the columns and the updated v_effective_checks; this file is the record). The one-time cut-off marks the checks dated before the
-- first imported bank statement "cleared before go-live" (engines/clearing.apply_cutoff, from the screen).
ALTER TABLE checks_issued ADD COLUMN manual_cleared_at DATE;
ALTER TABLE checks_issued ADD COLUMN manual_cleared_by TEXT;
ALTER TABLE checks_issued ADD COLUMN manual_cleared_note TEXT;
