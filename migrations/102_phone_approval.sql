-- SIGN IN ON A COMPUTER WITH YOUR PHONE (owner, 2026-10-14): one row per QR code shown on a computer's sign-in page. It lives two minutes and is
-- used once; the phone of the same person, signed in by its fingerprint, approves it by picking the number the computer shows.
CREATE TABLE IF NOT EXISTS signin_requests (
    request_id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT NOT NULL UNIQUE,               -- in the QR code: 32 random characters
    user_id INTEGER REFERENCES app_users(user_id),   -- the account Cloudflare verified on the computer, when it did
    number INTEGER NOT NULL,                  -- the two digits the computer shows
    browser TEXT,
    ip TEXT,
    next_url TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING' CHECK(status IN ('PENDING', 'APPROVED', 'DENIED', 'USED', 'EXPIRED')),
    approved_by INTEGER REFERENCES app_users(user_id),
    approved_at DATETIME,
    created_at DATETIME NOT NULL,             -- Manila time
    expires_at DATETIME NOT NULL
);
