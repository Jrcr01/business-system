-- SECURITY AND ROLES (owner, 2026-10-08). Applied by apply_pending_migrations.py (schema.sql carries the tables; this file is the record).
--   user_restrictions   everyone is an Owner; the System admin switches off, per person, what they may NOT do (one row per action switched off)
--   password_history    a new password cannot be one of the person's last 5
CREATE TABLE IF NOT EXISTS user_restrictions (
    user_id INTEGER NOT NULL REFERENCES app_users(user_id),
    action TEXT NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT,
    PRIMARY KEY (user_id, action)
);
CREATE TABLE IF NOT EXISTS password_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES app_users(user_id),
    password_hash TEXT NOT NULL,
    changed_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_password_history_user ON password_history (user_id, history_id);
