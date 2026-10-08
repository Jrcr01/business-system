-- SIGN IN WITH A USERNAME (owner, 2026-10-08): username, password, then the 6-digit code; the email stays on the account for records.
-- Applied by apply_pending_migrations.py (schema.sql carries the column and index; this file is the record). Accounts without one get it from
-- their email (the part before @) by `manage.py fill_usernames`, run by deploy/release.sh.
ALTER TABLE app_users ADD COLUMN username TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_app_users_username ON app_users (username);
