-- A GAP THE BANK MADE (owner, 2026-10-12: "I cannot control what sequence will give to us when we order"): confirming "Add anyway" records the
-- jump on the booklet itself, so it stops being warned about -- no Review flag, a quiet grey line instead of the orange one.
ALTER TABLE check_booklets ADD COLUMN gap_before_confirmed_at DATETIME;
ALTER TABLE check_booklets ADD COLUMN gap_before_confirmed_by TEXT;
