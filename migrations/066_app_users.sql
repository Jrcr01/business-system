-- SIGN-IN (2026-10-02). Applied by update_database.bat (schema.sql carries the table; this file is the record). New table only.
-- SIGN-IN (migration 066, 2026-10-02; B8 phase 1). The people who may use the AP app. The password is stored SCRAMBLED (scrypt + its own salt), never as typed.
-- role: 'owner' (Payables: everything) or 'purchasing' (purchase orders and price lists). A person is switched off, never deleted. Phase 2 (hosting) adds Google 2-step
-- sign-in on this same list, keyed by email.
CREATE TABLE IF NOT EXISTS app_users (
    user_id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    role TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1 CHECK(must_change_password IN (0, 1)),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    failed_attempts INTEGER NOT NULL DEFAULT 0,
    locked_until DATETIME,
    last_login_at DATETIME,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);
