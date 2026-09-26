-- Cancelled bank loans (2026-09-20): a cancelled loan stays on record with its repayment rows,
-- but its rows stop counting as amounts due anywhere (dashboards, reports, interest).
ALTER TABLE bank_loans ADD COLUMN is_cancelled INTEGER NOT NULL DEFAULT 0 CHECK(is_cancelled IN (0, 1));
