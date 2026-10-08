-- THE 6-DIGIT SIGN-IN CODE (owner, 2026-10-08): email, password, then the code from an authenticator app. Applied by apply_pending_migrations.py
-- (schema.sql carries the columns; this file is the record).
--   totp_secret       the key the authenticator app holds; NULL = not set up (asked at the next sign-in)
--   totp_enabled_at   when the person confirmed it with a first correct code
--   totp_last_step    the last code used, so the same code never works twice
ALTER TABLE app_users ADD COLUMN totp_secret TEXT;
ALTER TABLE app_users ADD COLUMN totp_enabled_at DATETIME;
ALTER TABLE app_users ADD COLUMN totp_last_step INTEGER;
