-- GOOGLE AUTHENTICATOR PROVES IT IS REALLY THEM (owner, 2026-10-11: "the risk is my family's email will be hack" ... "google authenticator because it
-- ensures the user only holds his phone"). The 6-digit code is asked of everyone, once a day per device; an authenticator is set up or replaced only
-- while the System admin allows it (24 hours); every sign-in is kept, and a device not used before goes on the Review list.
ALTER TABLE app_users ADD COLUMN totp_setup_until DATETIME;                -- the set-up is allowed until then (Manila time); NULL = not allowed
ALTER TABLE app_users ADD COLUMN device_epoch INTEGER NOT NULL DEFAULT 0;  -- "Sign out everywhere" adds 1: every sign-in and remembered device before it ends
CREATE TABLE IF NOT EXISTS sign_in_log (
    log_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES app_users(user_id),
    email TEXT,
    result TEXT NOT NULL,                     -- CODE, REMEMBERED, SET_UP (signed in) / PASSWORD_REFUSED, WRONG_CODE, SETUP_REFUSED (not signed in)
    device_id TEXT,                           -- the browser's own number (a cookie), so a device not used before can be told
    is_new_device INTEGER NOT NULL DEFAULT 0 CHECK(is_new_device IN (0, 1)),
    browser TEXT,
    ip TEXT,
    country TEXT,                             -- from Cloudflare, when it sends them
    city TEXT,
    at DATETIME NOT NULL                      -- Manila time
);
CREATE INDEX IF NOT EXISTS idx_sign_in_log_user ON sign_in_log (user_id, log_id);
