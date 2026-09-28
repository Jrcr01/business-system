-- AUDIT "WHO DID IT" (Phase F1.5, 2026-09-28). Applied by update_database.bat (schema.sql adds the column; apply_pending_migrations.py
-- fills the old rows in the same run); this file is the record.

-- Every audit_logs row records the email of the person who did it. New rows: INSERT ... user_email = acting_user() (a SQL function the
-- one door registers; the owner until logins exist in Phase F2). Rows written before F1.5 were all the owner's (owner, 2026-09-28).
ALTER TABLE audit_logs ADD COLUMN user_email TEXT;
UPDATE audit_logs SET user_email = 'v.jrcroxas@gmail.com' WHERE user_email IS NULL;
