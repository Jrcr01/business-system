-- FINGERPRINT SIGN-IN (owner, 2026-10-13): the device's own fingerprint or face unlock stands in for typing the password on that device.
-- One row per device a person turned it on for: the device keeps the private key, this is the matching public key. Removed on a password
-- change or reset, on Sign out everywhere, or by hand (the person's own, or the System admin's Remove).
CREATE TABLE IF NOT EXISTS sign_in_keys (
    key_id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES app_users(user_id),
    credential_id TEXT NOT NULL UNIQUE,       -- the device's name for the key (base64url)
    public_key BLOB NOT NULL,                 -- SubjectPublicKeyInfo (DER)
    alg INTEGER NOT NULL,                     -- -7 ES256, -257 RS256, -8 EdDSA
    sign_count INTEGER NOT NULL DEFAULT 0,    -- the device's own counter, to tell a copied key
    device TEXT,                              -- "Chrome on Android"
    created_at DATETIME NOT NULL,             -- Manila time
    last_used_at DATETIME
);
CREATE INDEX IF NOT EXISTS idx_sign_in_keys_user ON sign_in_keys (user_id);
